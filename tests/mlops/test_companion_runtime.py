"""``mlops.companion`` — the Langfuse prompt, its tones, and the bootstrap stand-in."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from mlops.companion import prompts, runtime
from mlops.companion.runtime import CompanionConfig, generate_companion_message

ACTIONS = ["Pause watering for {3} days.", "Move me away from the heater."]


class FakeModel:
    def __init__(self, answer: Any = "  I need a little help!  ") -> None:
        self.answer = answer
        self.sent: list[Any] = []
        self.kwargs: dict[str, Any] = {}

    def as_runnable(self) -> RunnableLambda[Any, Any]:
        def _call(messages: Any) -> AIMessage:
            self.sent.append(messages)
            if isinstance(self.answer, Exception):
                raise self.answer
            return AIMessage(content=self.answer)

        return RunnableLambda(_call)


class FakePrompt:
    def __init__(self, config: dict[str, Any], version: int = 2) -> None:
        self.name = prompts.SPEAK_PROMPT
        self.version = version
        self.config = config
        self.variables: dict[str, str] = {}

    def compile(self, **variables: str) -> list[dict[str, str]]:
        self.variables = variables
        return [{"role": "system", "content": f"LANGFUSE {variables['tone']}"}]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()

    def get_llm(_component: Any, **kwargs: Any) -> Any:
        fake.kwargs = kwargs
        return fake.as_runnable()

    monkeypatch.setattr("mlops.factory.get_llm", get_llm)
    return fake


@contextmanager
def _nullcontext() -> Any:
    yield


PROPAGATED: list[dict[str, Any]] = []


def _langfuse_configured(monkeypatch: pytest.MonkeyPatch, fetch: Any) -> None:
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
    monkeypatch.setattr(runtime.prompts, "get", fetch)


def test_the_trace_carries_the_run_id(model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    _langfuse_configured(monkeypatch, lambda *_, **__: FakePrompt({}))

    generate_companion_message("Monty", "Dry.", ACTIONS, plant_id="p1", run_id="run-9")

    assert PROPAGATED[0]["metadata"] == {"plant_id": "p1", "run_id": "run-9"}


def test_bootstrap_prompt_when_langfuse_is_not_configured(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "is_configured", lambda _c: False)

    text = generate_companion_message("Monty", "Soil is soggy.", ACTIONS, "unhealthy")

    assert text == "I need a little help!"
    system, user = model.sent[0]
    assert system[0] == "system"
    assert prompts.BOOTSTRAP_CONFIG["tones"]["unhealthy"] in system[1]
    # Braces in the care text are data, not template variables.
    assert "- Pause watering for {3} days." in user[1]
    assert "{{" not in system[1] + user[1]
    assert model.kwargs == {
        "temperature": 0.7,
        "model_override": None,
        "max_tokens": prompts.MAX_REPLY_TOKENS,
    }


def test_production_prompt_tones_and_config_come_from_langfuse(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = FakePrompt(
        {"model": "gpt-x", "temperature": 0.3, "tones": {"healthy": "Sing!"}, "default_tone": "Hm."}
    )
    calls: list[dict[str, Any]] = []

    def fetch(*_: Any, **kwargs: Any) -> FakePrompt:
        calls.append(kwargs)
        return prompt

    _langfuse_configured(monkeypatch, fetch)

    generate_companion_message("Monty", "Fine.", [], "healthy")
    generate_companion_message("Monty", "Hmm.", [], "unknown_status")

    assert calls == [{"label": "production"}, {"label": "production"}]
    assert model.kwargs == {
        "temperature": 0.3,
        "model_override": "gpt-x",
        "max_tokens": prompts.MAX_REPLY_TOKENS,
    }
    assert model.sent[0][0][1] == "LANGFUSE Sing!"
    assert model.sent[1][0][1] == "LANGFUSE Hm."
    assert set(prompt.variables) == set(prompts.SPEAK_VARIABLES)


def test_fetch_failure_falls_back_to_the_bootstrap_prompt(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fetch(*_: Any, **__: Any) -> Any:
        raise RuntimeError("langfuse down")

    _langfuse_configured(monkeypatch, fetch)

    assert generate_companion_message("Monty", "Dry.", ACTIONS, "healthy") is not None
    assert prompts.BOOTSTRAP_CONFIG["tones"]["healthy"] in model.sent[0][0][1]


def test_a_pinned_version_never_falls_back(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fetch(*_: Any, **__: Any) -> Any:
        raise RuntimeError("no such version")

    _langfuse_configured(monkeypatch, fetch)

    with pytest.raises(RuntimeError, match="no such version"):
        generate_companion_message("Monty", "Dry.", ACTIONS, version=9)


def test_an_llm_failure_returns_none(model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "is_configured", lambda _c: False)
    model.answer = RuntimeError("LLM unreachable")

    assert generate_companion_message("Monty", "Dry.", ACTIONS) is None


def test_a_long_reply_is_cut_after_four_sentences(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "is_configured", lambda _c: False)
    model.answer = " ".join(f"Sentence {i} is here." for i in range(1, 9))

    text = generate_companion_message("Monty", "Dry.", ACTIONS)

    assert text == "Sentence 1 is here. Sentence 2 is here. Sentence 3 is here. Sentence 4 is here."


def test_a_version_without_tones_uses_the_bootstrap_tones() -> None:
    config = CompanionConfig.from_prompt_config({"temperature": 0.5})
    assert config.tone_for("healthy") == prompts.BOOTSTRAP_CONFIG["tones"]["healthy"]
    assert config.tone_for("other") == prompts.BOOTSTRAP_CONFIG["default_tone"]


def test_memory_is_empty_without_history_and_formatted_with_it() -> None:
    assert prompts.format_memory() == ""

    obs = SimpleNamespace(
        timestamp=datetime(2026, 10, 1, tzinfo=UTC),
        health_status=SimpleNamespace(value="unhealthy"),
        observations=[{"type": "leaf_yellowing"}],
    )
    milestone = SimpleNamespace(
        timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        event_type="first_symptom",
        description="First yellow leaf.",
    )
    memory = prompts.format_memory([obs], [milestone])  # type: ignore[list-item]

    assert "2026-10-01: status=unhealthy, symptoms=leaf_yellowing" in memory
    assert "2026-09-01 [first_symptom]: First yellow leaf." in memory


def test_the_bootstrap_prompt_uses_exactly_the_declared_variables() -> None:
    text = "".join(m["content"] for m in prompts.BOOTSTRAP_MESSAGES)
    assert set(prompts._VARIABLE.findall(text)) == set(prompts.SPEAK_VARIABLES)
