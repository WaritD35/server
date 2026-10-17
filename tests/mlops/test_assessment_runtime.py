"""``run_probe``: prompt config → N backend runs → validate/repair → majority.

Langfuse and the VLM are both fakes. What is under test is that the
hyper-parameters come only from the prompt version, that a bad answer gets one
repair, that a majority of bad runs is raised rather than aggregated, and that
every way the dependencies can be missing surfaces as one error type.
"""

import dataclasses
import importlib.util
import json
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from langfuse.model import ChatPromptClient, TextPromptClient

from assessment.schemas import HealthStatus
from mlops.assessment import (
    AssessmentRunError,
    AssessmentUnavailableError,
    ProbeCheck,
    ProbeInput,
    ProbeVerdict,
    backends,
    prompts,
    run_probe,
    runtime,
)
from mlops.client import LangfuseNotConfiguredError
from mlops.settings import Component

PROBE = ProbeInput(
    plant_id="plant-1",
    species="Spathiphyllum wallisii",
    previous_image=b"previous-jpeg",
    current_image=b"current-jpeg",
)

CONFIG: dict[str, Any] = {
    "model": "OpenGVLab/InternVL3_5-2B",
    "runs": 3,
    "temperature": 0.3,
    "max_new_tokens": 520,
}


def answer(stress: str = "none", **overrides: Any) -> str:
    image = {
        "image_description": "A peace lily in a white pot.",
        "leaf_posture": "upright",
        "leaf_color": "even green",
        "visible_damage": "none visible",
        "visible_stress_level": stress,
    }
    payload = {
        "image_1_observation": image,
        "image_2_observation": image,
        "visible_differences": [],
        "current_observations": [],
        "comparison_summary": "No visible change.",
        "model_confidence": 0.8,
        **overrides,
    }
    return f"```json\n{json.dumps(payload)}\n```"


INVALID = answer(model_confidence="very sure")


class FakePrompt:
    name = prompts.PROBE_PROMPT
    version = 4

    def __init__(self, config: dict[str, Any] | None) -> None:
        self.config = config
        self.compiled_with: list[dict[str, Any]] = []

    def compile(self, **variables: Any) -> str:
        self.compiled_with.append(variables)
        knowledge = variables.get("plant_knowledge", "")
        checks = variables.get("probe_checks", "")
        return f"Describe the plant.\n{knowledge}\n{checks}"


class FakeBackend:
    """Returns the next scripted answer, recording what it was asked."""

    def __init__(self, *answers: str | Exception) -> None:
        self._answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        prompt: str,
        images: list[bytes],
        *,
        model: str,
        temperature: float,
        max_new_tokens: int,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "images": images,
                "model": model,
                "temperature": temperature,
                "max_new_tokens": max_new_tokens,
            }
        )
        result = self._answers[min(len(self.calls) - 1, len(self._answers) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


class FakeSpan:
    trace_id = "trace-123"

    def update(self, **_: Any) -> None:
        pass


class FakeLangfuse:
    @contextmanager
    def start_as_current_observation(self, **_: Any) -> Iterator[FakeSpan]:
        yield FakeSpan()


@contextmanager
def no_propagation(**_: Any) -> Iterator[None]:
    yield


@pytest.fixture(autouse=True)
def no_langfuse_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "get_client", lambda _: FakeLangfuse())
    monkeypatch.setattr(runtime, "propagate_attributes", no_propagation)


@pytest.fixture
def prompt(monkeypatch: pytest.MonkeyPatch) -> FakePrompt:
    fake = FakePrompt(dict(CONFIG))
    monkeypatch.setattr(runtime.prompts, "get", lambda *_, **__: fake)
    return fake


def use_backend(monkeypatch: pytest.MonkeyPatch, backend: Any) -> Any:
    monkeypatch.setattr(runtime, "get_vlm_backend", lambda: backend)
    return backend


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #


def test_a_verdict_is_aggregated_from_every_run(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = use_backend(monkeypatch, FakeBackend(answer("none")))

    verdict = run_probe(PROBE)

    assert isinstance(verdict, ProbeVerdict)
    assert HealthStatus(verdict.health_status) is HealthStatus.HEALTHY
    assert verdict.consensus["runs"] == 3
    assert verdict.trace_id == "trace-123"
    assert len(backend.calls) == 3


def test_the_trace_carries_the_run_id(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed ``pipeline_run`` can be found in Langfuse by its id."""
    use_backend(monkeypatch, FakeBackend(answer()))
    propagated: list[dict[str, Any]] = []

    @contextmanager
    def record(**kwargs: Any) -> Iterator[None]:
        propagated.append(kwargs)
        yield

    monkeypatch.setattr(runtime, "propagate_attributes", record)

    run_probe(dataclasses.replace(PROBE, run_id="run-9"))
    run_probe(PROBE)

    assert propagated[0]["metadata"] == {"plant_id": "plant-1", "run_id": "run-9"}
    assert propagated[1]["metadata"] == {"plant_id": "plant-1"}


def test_the_hyper_parameters_and_model_come_from_the_prompt_config(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = use_backend(monkeypatch, FakeBackend(answer()))

    run_probe(PROBE)

    call = backend.calls[0]
    assert (call["model"], call["temperature"], call["max_new_tokens"]) == (
        "OpenGVLab/InternVL3_5-2B",
        0.3,
        520,
    )


def test_both_images_are_sent_previous_first(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt calls the earlier frame Image 1 and the current one Image 2."""
    backend = use_backend(monkeypatch, FakeBackend(answer()))

    run_probe(PROBE)

    assert backend.calls[0]["images"] == [b"previous-jpeg", b"current-jpeg"]


def test_the_prompt_is_compiled_with_exactly_the_declared_variables(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_backend(monkeypatch, FakeBackend(answer()))

    run_probe(PROBE)

    (compiled,) = prompt.compiled_with
    assert set(compiled) == (
        set(prompts.PROBE_VARIABLES)
        | {"plant_knowledge"}
        | set(prompts.RETIRED_VARIABLES)
    )
    assert compiled["plant_knowledge"] == ""


def test_no_checks_compile_to_empty_sections() -> None:
    probe = ProbeInput(plant_id="p", species="s", previous_image=b"", current_image=b"")

    assert probe.as_prompt_variables() == {"plant_knowledge": "", "probe_checks": ""}


def test_plant_knowledge_is_a_titled_section_on_its_own_line() -> None:
    probe = dataclasses.replace(PROBE, plant_knowledge="  Keep the soil moist. ")

    section = probe.as_prompt_variables()["plant_knowledge"]

    assert section == "Plant knowledge:\nKeep the soil moist."


# --------------------------------------------------------------------------- #
# species-specific checks (approved knowledge probes)
# --------------------------------------------------------------------------- #

DROOP = ProbeCheck(
    slug="water_deficit.leaf_droop",
    question="Are the leaves drooping?",
    worse_looks_like="Leaves hang below the pot rim.",
    better_looks_like="Leaves stand upright.",
    not_this="Natural arching of old leaves.",
)
SPOTS = ProbeCheck(
    slug="pest.leaf_spots",
    question="Are there pale stippled spots?",
    worse_looks_like="Fine pale dots on the leaf surface.",
    better_looks_like="Even leaf colour.",
    not_this="Water marks after misting.",
)
WITH_CHECKS = dataclasses.replace(PROBE, checks=(DROOP, SPOTS))


def finding(slug: str, answer: str, evidence: str = "") -> dict[str, str]:
    return {"slug": slug, "answer": answer, "evidence": evidence}


def test_checks_are_listed_in_the_prompt_section_in_order() -> None:
    section = WITH_CHECKS.as_prompt_variables()["probe_checks"]

    assert section.index("water_deficit.leaf_droop") < section.index("pest.leaf_spots")
    assert "- water_deficit.leaf_droop: Are the leaves drooping?" in section
    assert "yes looks like: Leaves hang below the pot rim." in section
    assert "not this: Natural arching of old leaves." in section
    assert "answer only yes, no, or cannot_tell" in section
    assert "never put a probe slug or care_need" in section
    assert (
        "exactly one of: leaf_yellowing, brown_edges, drooping, curling, "
        "visible_damage, discoloration, other"
    ) in section


def test_each_check_is_voted_on_across_the_runs(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_backend(
        monkeypatch,
        FakeBackend(
            answer(probe_findings=[finding(DROOP.slug, "yes", "tips hang down"),
                                   finding(SPOTS.slug, "no")]),
            answer(probe_findings=[finding(DROOP.slug, "yes", "leaves hang well below the rim"),
                                   finding(SPOTS.slug, "no")]),
            answer(probe_findings=[finding(DROOP.slug, "no"), finding(SPOTS.slug, "Cannot_Tell")]),
        ),
    )

    verdict = run_probe(WITH_CHECKS)

    droop, spots = verdict.probe_results
    assert (droop["slug"], droop["answer"], droop["fired"]) == (DROOP.slug, "yes", True)
    assert droop["votes"] == {"yes": 2, "no": 1, "cannot_tell": 0}
    assert droop["agreement"] == pytest.approx(0.667)
    assert droop["evidence"] == "leaves hang well below the rim"
    assert (spots["answer"], spots["fired"], spots["evidence"]) == ("no", False, "")


def test_a_tie_and_a_missing_answer_lean_cautious(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that leaves a check out counts as cannot_tell; a tie goes to yes."""
    use_backend(
        monkeypatch,
        FakeBackend(
            answer(probe_findings=[finding(DROOP.slug, "yes")]),
            answer(probe_findings=[finding(DROOP.slug, "no")]),
            answer(probe_findings=[]),
        ),
    )

    droop, spots = run_probe(WITH_CHECKS).probe_results

    assert droop["answer"] == "yes"
    assert spots["answer"] == "cannot_tell"
    assert spots["votes"]["cannot_tell"] == 3


def test_a_version_holding_retired_passages_compiles_them_to_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version registered while passage search existed sends no search text."""
    old = FakePrompt(dict(CONFIG))
    old.variables = ["probe_checks", "retrieved_passages"]  # type: ignore[attr-defined]
    monkeypatch.setattr(runtime.prompts, "get", lambda *_, **__: old)
    use_backend(monkeypatch, FakeBackend(answer()))

    run_probe(PROBE)

    (compiled,) = old.compiled_with
    assert compiled["retrieved_passages"] == ""
    assert "retrieved_passages" not in prompts.PROBE_VARIABLES


def test_an_extra_json_field_is_rejected_by_the_schema(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads(answer().removeprefix("```json\n").removesuffix("\n```"))
    payload["note"] = "invented"
    backend = use_backend(
        monkeypatch,
        FakeBackend(f"```json\n{json.dumps(payload)}\n```", answer(), answer(), answer()),
    )

    run_probe(PROBE)

    assert "note" in backend.calls[1]["prompt"]


def test_unknown_and_duplicate_slugs_are_dropped_without_repair(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = answer(
        probe_findings=[
            finding("made_up.check", "yes"),
            finding(DROOP.slug, "yes"),
            finding(DROOP.slug, "no"),
        ]
    )
    backend = use_backend(
        monkeypatch,
        FakeBackend(
            raw,
            answer(probe_findings=[finding(DROOP.slug, "yes")]),
            answer(probe_findings=[finding(DROOP.slug, "yes")]),
        ),
    )

    verdict = run_probe(dataclasses.replace(WITH_CHECKS, checks=(DROOP,)))
    parsed = runtime._parse(raw, (DROOP.slug,))

    assert len(backend.calls) == 3
    assert verdict.probe_results[0]["answer"] == "yes"
    assert [(item.slug, item.answer) for item in parsed.probe_findings] == [
        (DROOP.slug, "yes")
    ]


def test_a_prompt_without_the_checks_section_records_no_results(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    old = FakePrompt(dict(CONFIG))
    old.variables = ["plant_knowledge"]  # type: ignore[attr-defined]  # a pre-checks version
    monkeypatch.setattr(runtime.prompts, "get", lambda *_, **__: old)
    use_backend(monkeypatch, FakeBackend(answer()))

    with caplog.at_level("WARNING"):
        verdict = run_probe(WITH_CHECKS)

    assert verdict.probe_results == []
    assert any("probe_checks" in r.getMessage() for r in caplog.records)


def test_without_checks_there_are_no_probe_results(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_backend(monkeypatch, FakeBackend(answer()))

    assert run_probe(PROBE).probe_results == []


def test_an_experiment_can_override_runs_and_sampling(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = use_backend(monkeypatch, FakeBackend(answer()))

    run_probe(PROBE, version=4, overrides={"runs": 1, "temperature": 0.0})

    assert len(backend.calls) == 1
    assert backend.calls[0]["temperature"] == 0.0


def test_a_pinned_version_is_what_gets_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fetch(name: str, **kwargs: Any) -> FakePrompt:
        seen.update(name=name, **kwargs)
        return FakePrompt(dict(CONFIG))

    monkeypatch.setattr(runtime.prompts, "get", fetch)
    use_backend(monkeypatch, FakeBackend(answer()))

    run_probe(PROBE, label=None, version=7)

    assert seen == {"name": prompts.PROBE_PROMPT, "label": None, "version": 7}


def test_prompts_get_asks_langfuse_for_a_text_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_prompt.return_value = MagicMock(spec=TextPromptClient)
    monkeypatch.setattr(prompts, "get_client", lambda _: client)

    prompts.get()

    args, kwargs = client.get_prompt.call_args
    assert args == (prompts.PROBE_PROMPT,)
    assert kwargs["type"] == prompts.PROBE_PROMPT_TYPE == "text"
    assert kwargs["label"] == "production"


def test_prompts_get_rejects_a_chat_prompt_under_the_probe_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.get_prompt.return_value = MagicMock(spec=ChatPromptClient)
    monkeypatch.setattr(prompts, "get_client", lambda _: client)

    with pytest.raises(TypeError, match="text prompt"):
        prompts.get()


# --------------------------------------------------------------------------- #
# validate → one repair → majority rule
# --------------------------------------------------------------------------- #


def test_an_invalid_answer_gets_one_repair_carrying_its_errors(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = use_backend(
        monkeypatch, FakeBackend(INVALID, answer(), answer(), answer())
    )

    verdict = run_probe(PROBE)

    assert verdict.consensus["runs"] == 3
    assert len(backend.calls) == 4
    first, repair = backend.calls[0]["prompt"], backend.calls[1]["prompt"]
    assert repair.startswith(first)
    assert "model_confidence" in repair[len(first) :]
    assert "very sure" in repair[len(first) :]


def test_unparseable_text_is_repaired_the_same_way(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = use_backend(
        monkeypatch, FakeBackend("I cannot see a plant.", answer(), answer(), answer())
    )

    run_probe(PROBE)

    assert "JSON" in backend.calls[1]["prompt"]


def test_a_run_that_fails_its_repair_is_dropped_but_a_majority_still_counts(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 runs need ceil(3/2) = 2 valid ones."""
    backend = use_backend(
        monkeypatch, FakeBackend(INVALID, INVALID, answer("severe"), answer("severe"))
    )

    verdict = run_probe(PROBE)

    assert len(backend.calls) == 4
    assert verdict.consensus["runs"] == 2
    assert verdict.health_status == "unhealthy"


def test_a_majority_of_invalid_runs_is_raised_not_aggregated(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no 'mostly valid' way out of an mlops runtime."""
    backend = use_backend(
        monkeypatch, FakeBackend(INVALID, INVALID, INVALID, INVALID, answer())
    )

    with pytest.raises(AssessmentRunError) as exc:
        run_probe(PROBE)

    assert (exc.value.runs, exc.value.valid) == (3, 1)
    assert len(backend.calls) == 5
    assert "model_confidence" in str(exc.value.last_error)


@pytest.mark.parametrize(("runs", "valid_needed"), [(1, 1), (2, 1), (4, 2), (5, 3)])
def test_the_valid_run_threshold_is_half_the_runs_rounded_up(
    runs: int, valid_needed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakePrompt({**CONFIG, "runs": runs})
    monkeypatch.setattr(runtime.prompts, "get", lambda *_, **__: fake)
    script = [answer()] * valid_needed + [INVALID] * (2 * (runs - valid_needed))
    use_backend(monkeypatch, FakeBackend(*script))

    assert run_probe(PROBE).consensus["runs"] == valid_needed

    short = [answer()] * (valid_needed - 1) + [INVALID] * (2 * (runs - valid_needed + 1))
    use_backend(monkeypatch, FakeBackend(*short))
    with pytest.raises(AssessmentRunError):
        run_probe(PROBE)


def test_invalid_severity_is_normalized_without_repair(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    critical = answer(
        "severe",
        current_observations=[
            {"type": "drooping", "severity": "critical", "description": "Leaves collapsed."}
        ],
    )
    backend = use_backend(monkeypatch, FakeBackend(critical, answer(), answer()))

    verdict = run_probe(PROBE)

    assert len(backend.calls) == 3
    assert {o["severity"] for o in verdict.observations} <= {"mild", "moderate", "severe"}
    parsed = runtime._parse(critical)
    assert parsed.current_observations[0].severity == "mild"


def test_invalid_observation_type_is_normalized_without_repair(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = answer(
        "mild",
        current_observations=[
            {
                "type": "overwatering.yellow_lower_leaves",
                "severity": "mild",
                "description": "Lower leaves are yellow.",
            }
        ],
    )
    backend = use_backend(monkeypatch, FakeBackend(invalid, answer(), answer()))

    verdict = run_probe(PROBE)

    assert len(backend.calls) == 3
    parsed = runtime._parse(invalid)
    assert parsed.current_observations[0].type == "other"


def test_invalid_probe_answer_becomes_cannot_tell_and_keeps_slug(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = dataclasses.replace(
        PROBE,
        checks=(
            ProbeCheck(
                slug="low_light.green_emerging_flowers",
                question="Are emerging flowers green?",
                worse_looks_like="Green flowers",
                better_looks_like="White flowers",
                not_this="Green leaves",
            ),
        ),
    )
    invalid = answer(
        probe_findings=[
            {
                "slug": "low_light.green_emerging_flowers",
                "answer": "not this",
                "evidence": "No flowers are visible.",
            }
        ]
    )
    backend = use_backend(monkeypatch, FakeBackend(invalid, answer(), answer()))

    verdict = run_probe(probe)

    assert len(backend.calls) == 3
    assert verdict.probe_results[0]["slug"] == "low_light.green_emerging_flowers"


# --------------------------------------------------------------------------- #
# everything that makes the assessment unavailable (→ 503 at the caller)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("missing", ["model", "runs", "temperature", "max_new_tokens"])
def test_a_prompt_config_missing_a_key_is_unavailable(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No in-repo defaults: the version must carry every hyper-parameter."""
    config = {k: v for k, v in CONFIG.items() if k != missing}
    monkeypatch.setattr(runtime.prompts, "get", lambda *_, **__: FakePrompt(config))
    backend = use_backend(monkeypatch, FakeBackend(answer()))

    with pytest.raises(AssessmentUnavailableError, match=missing):
        run_probe(PROBE)

    assert backend.calls == []


@pytest.mark.parametrize(
    "config",
    [
        None,
        {**CONFIG, "runs": 0},
        {**CONFIG, "runs": "five"},
        {**CONFIG, "runs": 9},
        {**CONFIG, "max_new_tokens": 2049},
    ],
)
def test_an_unusable_prompt_config_is_unavailable(
    config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime.prompts, "get", lambda *_, **__: FakePrompt(config))
    backend = use_backend(monkeypatch, FakeBackend(answer()))

    with pytest.raises(AssessmentUnavailableError):
        run_probe(PROBE)

    assert backend.calls == []


@pytest.mark.parametrize(
    "error",
    [
        LangfuseNotConfiguredError(Component.ASSESSMENT),
        ConnectionError("langfuse is down"),
        TypeError("prompt is a chat prompt"),
    ],
)
def test_a_failed_prompt_fetch_is_unavailable(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fetch(*_: Any, **__: Any) -> Any:
        raise error

    monkeypatch.setattr(runtime.prompts, "get", fetch)

    with pytest.raises(AssessmentUnavailableError) as exc:
        run_probe(PROBE)

    assert exc.value.__cause__ is error


def test_a_backend_that_cannot_be_built_is_unavailable(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_backend() -> Any:
        raise backends.VLMBackendUnavailableError("VLM_API_BASE is not set")

    monkeypatch.setattr(runtime, "get_vlm_backend", no_backend)

    with pytest.raises(AssessmentUnavailableError, match="VLM_API_BASE"):
        run_probe(PROBE)


def test_a_backend_that_cannot_be_reached_is_unavailable_at_once(
    prompt: FakePrompt, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = use_backend(
        monkeypatch,
        FakeBackend(backends.VLMBackendUnavailableError("connection refused")),
    )

    with pytest.raises(AssessmentUnavailableError, match="connection refused"):
        run_probe(PROBE)

    assert len(backend.calls) == 1


# --------------------------------------------------------------------------- #
# backend selection
# --------------------------------------------------------------------------- #


def settings(**values: str) -> SimpleNamespace:
    base = {
        "vlm_provider": "vllm",
        "vlm_api_base": "http://vlm:8000/v1",
        "vlm_api_key": "",
        "vlm_local_model_path": "models/internvl3_5-2b",
    }
    return SimpleNamespace(**{**base, **values})


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("vllm", backends.VLLMBackend),
        ("local", backends.LocalTransformersBackend),
        (" Local ", backends.LocalTransformersBackend),
    ],
)
def test_vlm_provider_selects_the_backend(
    provider: str, expected: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends, "get_settings", lambda: settings(vlm_provider=provider))

    assert isinstance(backends.get_vlm_backend(), expected)


def test_vllm_without_an_api_base_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backends, "get_settings", lambda: settings(vlm_api_base=""))

    with pytest.raises(backends.VLMBackendUnavailableError, match="VLM_API_BASE"):
        backends.get_vlm_backend()


def test_an_unknown_provider_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backends, "get_settings", lambda: settings(vlm_provider="ollama"))

    with pytest.raises(backends.VLMBackendUnavailableError, match="ollama"):
        backends.get_vlm_backend()


def test_the_local_backend_without_torch_is_unavailable_not_an_import_error() -> None:
    """`vlm-local` is an opt-in group; the default environment has no torch."""
    if importlib.util.find_spec("torch") is not None:
        pytest.skip("torch is installed")

    backend = backends.LocalTransformersBackend("models/does-not-matter")
    with pytest.raises(backends.VLMBackendUnavailableError, match="vlm-local"):
        backend.generate("p", [b"a", b"b"], model="m", temperature=0.0, max_new_tokens=8)


# --------------------------------------------------------------------------- #
# the vLLM request shape
# --------------------------------------------------------------------------- #


class RecordingChat:
    instances: list["RecordingChat"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.messages: list[Any] = []
        RecordingChat.instances.append(self)

    def invoke(self, messages: list[Any]) -> Any:
        self.messages = messages
        return SimpleNamespace(content='{"ok": true}')


def test_vllm_sends_the_prompt_model_and_both_images_as_base64_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RecordingChat.instances = []
    monkeypatch.setattr(backends, "ChatOpenAI", RecordingChat)
    backend = backends.VLLMBackend(api_base="http://vlm:8000/v1", api_key="")

    text = backend.generate(
        "Describe.", [b"\x01", b"\x02"], model="m-1", temperature=0.3, max_new_tokens=520
    )

    assert text == '{"ok": true}'
    chat = RecordingChat.instances[0]
    assert chat.kwargs["base_url"] == "http://vlm:8000/v1"
    assert chat.kwargs["model"] == "m-1"
    assert chat.kwargs["api_key"]
    content = chat.messages[0].content
    assert [part["type"] for part in content] == ["image_url", "image_url", "text"]
    assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,AQ=="
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,Ag=="
    assert content[2]["text"] == "Describe."


def test_vllm_connection_failure_becomes_backend_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openai

    class Unreachable(RecordingChat):
        def invoke(self, messages: list[Any]) -> Any:
            raise openai.APIConnectionError(request=MagicMock())

    monkeypatch.setattr(backends, "ChatOpenAI", Unreachable)
    backend = backends.VLLMBackend(api_base="http://vlm:8000/v1", api_key="k")

    with pytest.raises(backends.VLMBackendUnavailableError):
        backend.generate("p", [b"a"], model="m", temperature=0.0, max_new_tokens=8)

