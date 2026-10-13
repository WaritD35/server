"""``mlops.advice`` — plans built only from approved actions, prompts and fallbacks."""

from __future__ import annotations

import dataclasses
import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.runnables import RunnableLambda
from langfuse.model import ChatPromptClient

from mlops.advice import prompts, runtime
from mlops.advice.runtime import (
    AdviceConfig,
    AdviceInput,
    ApprovedAction,
    FiredCheck,
    generate_advice,
)

PAUSE = ApprovedAction(
    id="11111111-1111-1111-1111-111111111111",
    check_slug="water_excess.soggy_soil",
    care_need="water_excess",
    instruction="Pause watering until the top 3 cm of soil is dry.",
    urgency="soon",
    expect_typical_hours=48,
    expect_max_hours=96,
)
DRAIN = ApprovedAction(
    id="22222222-2222-2222-2222-222222222222",
    check_slug="water_excess.soggy_soil",
    care_need="water_excess",
    instruction="Empty the saucer under the pot.",
    urgency="now",
    expect_typical_hours=1,
    expect_max_hours=6,
)

#: The model's answer refers to the listed keys: A1 = PAUSE, A2 = DRAIN.
PLAN = {
    "status_label": "Overwatering stress",
    "assessment": "The soil stays wet.",
    "confidence": 0.9,
    "actions": [
        {"approved_action_id": "A1", "priority": 2, "label": "Pause water", "type": "water"},
        {"approved_action_id": "A2", "priority": 1, "label": "Drain tray", "type": "Water"},
    ],
}

PAYLOAD = AdviceInput(
    plant_id="p1",
    health_status="unhealthy",
    symptoms=[{"type": "leaf_yellowing", "severity": "severe"}],
    observation_history=[{"health_status": "healthy", "symptoms": []}],
    trigger_reason="Health status dropped.",
    fired_checks=(
        FiredCheck(
            slug="water_excess.soggy_soil",
            care_need="water_excess",
            question="Is the soil surface dark and wet?",
            evidence="dark wet soil",
        ),
    ),
    approved_actions=(PAUSE, DRAIN),
)


class FakeModel:
    """Answers in turn from ``answers`` (the last one repeats); records what it was sent."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers) or [PLAN]
        self.sent: list[Any] = []
        self.kwargs: dict[str, Any] = {}

    def with_structured_output(self, _schema: Any) -> RunnableLambda[Any, Any]:
        def _call(messages: Any) -> Any:
            self.sent.append(messages)
            answer = self.answers[min(len(self.sent) - 1, len(self.answers) - 1)]
            if isinstance(answer, Exception):
                raise answer
            return answer

        return RunnableLambda(_call)


class FakePrompt:
    def __init__(self, config: dict[str, Any], version: int = 3) -> None:
        self.name = prompts.DIAGNOSE_PROMPT
        self.version = version
        self.config = config

    def compile(self, **variables: str) -> list[dict[str, str]]:
        return [{"role": "user", "content": f"LANGFUSE {variables['approved_actions']}"}]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()

    def get_llm(_component: Any, **kwargs: Any) -> FakeModel:
        fake.kwargs = kwargs
        return fake

    monkeypatch.setattr("mlops.factory.get_llm", get_llm)
    return fake


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "is_configured", lambda _c: False)


PROPAGATED: list[dict[str, Any]] = []


def _langfuse_configured(monkeypatch: pytest.MonkeyPatch, prompt: Any) -> MagicMock:
    span = MagicMock(trace_id="trace-1")

    @contextmanager
    def observation(**_: Any) -> Any:
        yield span

    def propagate(**kwargs: Any) -> Any:
        PROPAGATED.append(kwargs)
        return _nullcontext()

    PROPAGATED.clear()
    client = MagicMock()
    client.start_as_current_observation.side_effect = observation
    monkeypatch.setattr(runtime, "is_configured", lambda _c: True)
    monkeypatch.setattr(runtime, "get_client", lambda _c: client)
    monkeypatch.setattr(runtime, "get_callback_handler", lambda _c: MagicMock())
    monkeypatch.setattr(runtime, "propagate_attributes", propagate)
    monkeypatch.setattr(runtime.prompts, "get", prompt)
    return client


@contextmanager
def _nullcontext() -> Any:
    yield


# --------------------------------------------------------------------------- #
# only approved knowledge
# --------------------------------------------------------------------------- #


def test_the_plan_is_made_of_approved_actions_only(model: FakeModel, offline: None) -> None:
    result = generate_advice(PAYLOAD)

    assert result.source == "llm"
    assert result.status_label == "Overwatering stress"
    first, second = result.actions
    # Ordered by the model's priority; text, urgency and timing copied verbatim.
    assert (first["priority"], first["approved_action_id"]) == (1, DRAIN.id)
    assert first["action"] == "Empty the saucer under the pot."
    assert (first["urgency"], first["expect_typical_hours"], first["expect_max_hours"]) == (
        "now",
        1,
        6,
    )
    assert (first["label"], first["type"]) == ("Drain tray", "water")
    assert first["check_slug"] == "water_excess.soggy_soil"
    assert (second["priority"], second["approved_action_id"]) == (2, PAUSE.id)
    assert first["id"].startswith("act_")


def test_the_prompt_lists_the_fired_checks_and_the_approved_actions(
    model: FakeModel, offline: None
) -> None:
    generate_advice(PAYLOAD)

    user = model.sent[0][1][1]
    assert "water_excess.soggy_soil (water_excess): Is the soil surface dark and wet?" in user
    assert "- A1: Pause watering until the top 3 cm of soil is dry." in user
    assert "- A2: Empty the saucer under the pot." in user
    assert "effect expected within 1-6 h" in user
    assert json.dumps(PAYLOAD.symptoms) in user
    assert "{{" not in user


def test_a_fired_check_brings_its_approved_basis_into_the_prompt(
    model: FakeModel, offline: None
) -> None:
    (check,) = PAYLOAD.fired_checks
    payload = dataclasses.replace(
        PAYLOAD,
        fired_checks=(
            dataclasses.replace(
                check,
                basis="Soggy soil starves roots of oxygen.",
                not_this="soil just watered",
            ),
        ),
    )

    generate_advice(payload)

    user = model.sent[0][1][1]
    assert "  Seen in the photo: dark wet soil" in user
    assert "  Approved basis: Soggy soil starves roots of oxygen." in user
    assert "  Not to be confused with: soil just watered" in user


def test_an_action_that_was_not_listed_is_sent_back_once(offline: None, monkeypatch) -> None:
    invented = {**PLAN, "actions": [{"approved_action_id": "A9", "priority": 1, "label": "Repot"}]}
    fake = FakeModel(invented, PLAN)
    monkeypatch.setattr("mlops.factory.get_llm", lambda *_a, **_k: fake)

    result = generate_advice(PAYLOAD)

    assert result.source == "llm"
    assert len(fake.sent) == 2
    assert "'A9' is not one of ['A1', 'A2']" in fake.sent[1][-1][1]


def test_an_action_that_never_validates_falls_back_to_approved_actions(
    offline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    invented = {**PLAN, "actions": [{"approved_action_id": "A9", "priority": 1, "label": "Repot"}]}
    fake = FakeModel(invented)
    monkeypatch.setattr("mlops.factory.get_llm", lambda *_a, **_k: fake)

    result = generate_advice(PAYLOAD)

    assert result.source == "fallback"
    assert [a["approved_action_id"] for a in result.actions] == [PAUSE.id, DRAIN.id]
    assert result.assessment == "Approved checks fired: water_excess.soggy_soil."


def test_an_llm_failure_uses_the_approved_actions_in_order(
    offline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeModel(RuntimeError("LLM unreachable"))
    monkeypatch.setattr("mlops.factory.get_llm", lambda *_a, **_k: fake)

    result = generate_advice(PAYLOAD)

    assert result.source == "fallback"
    assert [a["action"] for a in result.actions] == [PAUSE.instruction, DRAIN.instruction]
    assert all(a["label"] == "Water excess" for a in result.actions)


def test_without_approved_actions_no_llm_is_called(model: FakeModel, offline: None) -> None:
    result = generate_advice(dataclasses.replace(PAYLOAD, fired_checks=(), approved_actions=()))

    assert model.sent == []
    assert result.source == "no_knowledge"
    (step,) = result.actions
    assert step["type"] == "inspect"
    assert "approved_action_id" not in step
    assert "No approved care actions" in result.assessment


# --------------------------------------------------------------------------- #
# prompts and tracing
# --------------------------------------------------------------------------- #


def test_uses_the_bootstrap_prompt_when_langfuse_is_not_configured(
    model: FakeModel, offline: None
) -> None:
    result = generate_advice(PAYLOAD)

    assert result.prompt_version is None
    assert result.prompt_ref == "advice/diagnose@bootstrap"
    assert [role for role, _ in model.sent[0]] == ["system", "user"]


def test_runs_the_production_prompt_and_its_config(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fetch(*_: Any, **kwargs: Any) -> FakePrompt:
        calls.append(kwargs)
        return FakePrompt({"model": "gpt-x", "temperature": 0.5}, version=7)

    _langfuse_configured(monkeypatch, fetch)

    result = generate_advice(PAYLOAD)

    assert calls == [{"label": "production"}]
    assert model.kwargs == {"temperature": 0.5, "model_override": "gpt-x"}
    assert model.sent[0][0][1].startswith("LANGFUSE - A1: ")
    assert result.prompt_version == 7
    assert result.trace_id == "trace-1"


def test_the_trace_carries_the_run_id(model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    _langfuse_configured(monkeypatch, lambda *_, **__: FakePrompt({}))

    generate_advice(dataclasses.replace(PAYLOAD, run_id="run-9"))

    assert PROPAGATED[0]["metadata"] == {"plant_id": "p1", "run_id": "run-9"}


def test_falls_back_to_the_bootstrap_prompt_when_the_fetch_fails(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fetch(*_: Any, **__: Any) -> Any:
        raise RuntimeError("langfuse down")

    _langfuse_configured(monkeypatch, fetch)

    result = generate_advice(PAYLOAD)

    assert result.source == "llm"
    assert result.prompt_version is None
    assert model.kwargs == {"temperature": 0.2, "model_override": None}


def test_a_version_without_room_for_the_knowledge_uses_the_bootstrap_prompt(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    old = FakePrompt({"model": "gpt-x"}, version=2)
    old.prompt = [{"role": "user", "content": "{{plant_id}} {{symptoms}}"}]  # type: ignore[attr-defined]
    _langfuse_configured(monkeypatch, lambda *_, **__: old)

    with caplog.at_level("WARNING", logger=runtime.__name__):
        result = generate_advice(PAYLOAD)

    assert result.source == "llm"
    assert result.prompt_version is None
    assert "- A1: " in model.sent[0][1][1]
    assert any("{{approved_actions}}" in r.getMessage() for r in caplog.records)


def test_a_pinned_version_never_falls_back(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fetch(*_: Any, **__: Any) -> Any:
        raise RuntimeError("no such version")

    _langfuse_configured(monkeypatch, fetch)

    with pytest.raises(RuntimeError, match="no such version"):
        generate_advice(PAYLOAD, version=4)


def test_overrides_win_over_the_prompt_config() -> None:
    config = AdviceConfig.from_prompt_config(
        {"model": "a", "temperature": 0.1}, overrides={"temperature": 0.9}
    )
    assert config == AdviceConfig(model="a", temperature=0.9)
    assert AdviceConfig.from_prompt_config(None) == AdviceConfig(model=None, temperature=0.2)


def test_the_bootstrap_prompt_uses_exactly_the_declared_variables() -> None:
    text = "".join(m["content"] for m in prompts.BOOTSTRAP_MESSAGES)
    used = set(prompts._VARIABLE.findall(text))
    assert used == set(prompts.DIAGNOSE_VARIABLES)
    assert set(PAYLOAD.as_prompt_variables()) == set(prompts.DIAGNOSE_VARIABLES)
    assert "knowledge_context" not in prompts.DIAGNOSE_VARIABLES


def test_get_rejects_a_text_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_prompt.return_value = object()
    monkeypatch.setattr(prompts, "get_client", lambda _c: client)

    with pytest.raises(TypeError, match="chat prompt"):
        prompts.get()


def test_get_pins_a_version_instead_of_the_label(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_prompt.return_value = MagicMock(spec=ChatPromptClient)
    monkeypatch.setattr(prompts, "get_client", lambda _c: client)

    prompts.get(version=5)

    _, kwargs = client.get_prompt.call_args
    assert kwargs["version"] == 5
    assert "label" not in kwargs
