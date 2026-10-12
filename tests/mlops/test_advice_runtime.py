"""``mlops.advice`` — the Langfuse prompt, its bootstrap stand-in, and the fallback plan."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.runnables import RunnableLambda
from langfuse.model import ChatPromptClient

from mlops.advice import prompts, runtime
from mlops.advice.runtime import AdviceConfig, AdviceInput, generate_advice

PLAN = {
    "status_label": "Overwatering stress",
    "assessment": "Soil stays wet.",
    "confidence": 0.9,
    "actions": [{"priority": 1, "action": "Pause watering.", "label": "Pause water"}],
}

PAYLOAD = AdviceInput(
    plant_id="p1",
    health_status="unhealthy",
    symptoms=[{"type": "leaf_yellowing", "severity": "severe"}],
    observation_history=[{"health_status": "healthy", "symptoms": []}],
    trigger_reason="Health status dropped.",
)


class FakeModel:
    """Records what the structured-output call was sent."""

    def __init__(self, answer: Any = PLAN) -> None:
        self.answer = answer
        self.sent: list[Any] = []
        self.kwargs: dict[str, Any] = {}

    def with_structured_output(self, _schema: Any) -> RunnableLambda[Any, Any]:
        def _call(messages: Any) -> Any:
            self.sent.append(messages)
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

        return RunnableLambda(_call)


class FakePrompt:
    def __init__(self, config: dict[str, Any], version: int = 3) -> None:
        self.name = prompts.DIAGNOSE_PROMPT
        self.version = version
        self.config = config

    def compile(self, **variables: str) -> list[dict[str, str]]:
        return [{"role": "user", "content": f"LANGFUSE {variables['symptoms']}"}]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()

    def get_llm(_component: Any, **kwargs: Any) -> FakeModel:
        fake.kwargs = kwargs
        return fake

    monkeypatch.setattr("mlops.factory.get_llm", get_llm)
    return fake


def _langfuse_configured(monkeypatch: pytest.MonkeyPatch, prompt: Any) -> MagicMock:
    span = MagicMock(trace_id="trace-1")

    @contextmanager
    def observation(**_: Any) -> Any:
        yield span

    client = MagicMock()
    client.start_as_current_observation.side_effect = observation
    monkeypatch.setattr(runtime, "is_configured", lambda _c: True)
    monkeypatch.setattr(runtime, "get_client", lambda _c: client)
    monkeypatch.setattr(runtime, "get_callback_handler", lambda _c: MagicMock())
    monkeypatch.setattr(runtime, "propagate_attributes", lambda **_: _nullcontext())
    monkeypatch.setattr(runtime.prompts, "get", prompt)
    return client


@contextmanager
def _nullcontext() -> Any:
    yield


def test_uses_the_bootstrap_prompt_when_langfuse_is_not_configured(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "is_configured", lambda _c: False)

    result = generate_advice(PAYLOAD)

    assert result.source == "llm"
    assert result.status_label == "Overwatering stress"
    assert result.prompt_version is None
    assert result.prompt_ref == "advice/diagnose@bootstrap"
    roles = [role for role, _ in model.sent[0]]
    assert roles == ["system", "user"]


def test_json_in_the_variables_reaches_the_model_verbatim(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Braces in the symptoms are data, not template variables."""
    monkeypatch.setattr(runtime, "is_configured", lambda _c: False)

    generate_advice(PAYLOAD)

    user = model.sent[0][1][1]
    assert json.dumps(PAYLOAD.symptoms) in user
    assert "{{" not in user


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
    assert model.sent[0][0][1].startswith("LANGFUSE ")
    assert result.prompt_version == 7
    assert result.trace_id == "trace-1"


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


def test_a_pinned_version_never_falls_back(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fetch(*_: Any, **__: Any) -> Any:
        raise RuntimeError("no such version")

    _langfuse_configured(monkeypatch, fetch)

    with pytest.raises(RuntimeError, match="no such version"):
        generate_advice(PAYLOAD, version=4)


def test_an_llm_failure_returns_the_rule_based_plan(
    model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "is_configured", lambda _c: False)
    model.answer = RuntimeError("LLM unreachable")

    result = generate_advice(PAYLOAD)

    assert result.source == "fallback"
    assert [a["label"] for a in result.actions] == ["Check soil", "Check light"]


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
