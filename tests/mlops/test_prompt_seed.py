"""``mlops.<component>.seed`` — registering the bootstrap prompts as Langfuse versions."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langfuse.api import NotFoundError
from langfuse.model import ChatPromptClient, TextPromptClient

from mlops.advice import prompts as advice_prompts
from mlops.advice import seed as advice_seed
from mlops.assessment import prompts as assessment_prompts
from mlops.assessment import seed as assessment_seed
from mlops.companion import prompts as companion_prompts
from mlops.companion import seed as companion_seed


def _client(monkeypatch: pytest.MonkeyPatch, module: Any, latest: Any) -> MagicMock:
    client = MagicMock()
    if latest is None:
        client.get_prompt.side_effect = NotFoundError(body="not found")
    else:
        client.get_prompt.return_value = latest
    client.create_prompt.return_value = MagicMock(version=4)
    monkeypatch.setattr(module, "get_client", lambda _c: client)
    return client


def _text_prompt(text: str, config: dict[str, Any], version: int = 3) -> MagicMock:
    prompt = MagicMock(spec=TextPromptClient)
    prompt.prompt, prompt.config, prompt.version, prompt.labels = text, config, version, []
    return prompt


def _chat_prompt(messages: list[dict[str, str]], config: dict[str, Any]) -> MagicMock:
    prompt = MagicMock(spec=ChatPromptClient)
    prompt.prompt = [{"type": "message", **m} for m in messages]
    prompt.config, prompt.version, prompt.labels = config, 3, ["production"]
    return prompt


# --------------------------------------------------------------------------- #
# assessment
# --------------------------------------------------------------------------- #


def test_assessment_first_version_goes_live(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, assessment_seed, latest=None)

    result = assessment_seed.seed(model="internvl")

    assert (result.action, result.version, result.labels) == ("created", 4, ("production",))
    _, kwargs = client.create_prompt.call_args
    assert kwargs["name"] == assessment_prompts.PROBE_PROMPT
    assert kwargs["type"] == "text"
    assert kwargs["prompt"] == assessment_prompts.BOOTSTRAP_PROMPT
    assert kwargs["config"] == {"model": "internvl", "runs": 5, "temperature": 0.3,
                                "max_new_tokens": 1024}


def test_assessment_a_changed_prompt_is_created_unlabelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _text_prompt("an older text", assessment_seed.bootstrap_config("internvl"))
    client = _client(monkeypatch, assessment_seed, latest=latest)

    result = assessment_seed.seed(model="internvl")

    assert result.labels == ()
    assert client.create_prompt.call_args.kwargs["labels"] == []


def test_assessment_promote_labels_the_new_version(monkeypatch: pytest.MonkeyPatch) -> None:
    latest = _text_prompt("an older text", {})
    client = _client(monkeypatch, assessment_seed, latest=latest)

    assessment_seed.seed(model="internvl", promote=True)

    assert client.create_prompt.call_args.kwargs["labels"] == ["production"]


def test_assessment_an_identical_latest_version_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _text_prompt(
        assessment_prompts.BOOTSTRAP_PROMPT, assessment_seed.bootstrap_config("internvl")
    )
    client = _client(monkeypatch, assessment_seed, latest=latest)

    result = assessment_seed.seed(model="internvl")

    assert (result.action, result.version) == ("unchanged", 3)
    client.create_prompt.assert_not_called()


def test_assessment_dry_run_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, assessment_seed, latest=None)

    result = assessment_seed.seed(model="internvl", dry_run=True)

    assert result.action == "dry_run"
    client.create_prompt.assert_not_called()


def test_assessment_needs_a_model() -> None:
    with pytest.raises(ValueError, match="model"):
        assessment_seed.bootstrap_config(" ")


def test_assessment_bootstrap_prompt_uses_only_declared_variables() -> None:
    compiled = assessment_prompts.BOOTSTRAP_PROMPT
    for name in assessment_prompts.PROBE_VARIABLES:
        compiled = compiled.replace("{{" + name + "}}", "")
    assert "{{" not in compiled


# --------------------------------------------------------------------------- #
# advice
# --------------------------------------------------------------------------- #


def test_advice_first_version_goes_live(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, advice_seed, latest=None)

    result = advice_seed.seed()

    assert (result.action, result.labels) == ("created", ("production",))
    _, kwargs = client.create_prompt.call_args
    assert kwargs["type"] == "chat"
    assert kwargs["config"] == {"temperature": 0.2}
    assert [m["role"] for m in kwargs["prompt"]] == ["system", "user"]


def test_advice_model_is_pinned_only_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, advice_seed, latest=None)

    advice_seed.seed(model="gpt-4o-mini")

    assert client.create_prompt.call_args.kwargs["config"]["model"] == "gpt-4o-mini"


def test_advice_an_identical_latest_version_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _chat_prompt(advice_prompts.BOOTSTRAP_MESSAGES, advice_seed.bootstrap_config())
    client = _client(monkeypatch, advice_seed, latest=latest)

    result = advice_seed.seed()

    assert (result.action, result.labels) == ("unchanged", ("production",))
    client.create_prompt.assert_not_called()


def test_advice_a_changed_config_is_a_new_unlabelled_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _chat_prompt(advice_prompts.BOOTSTRAP_MESSAGES, {"temperature": 0.9})
    client = _client(monkeypatch, advice_seed, latest=latest)

    result = advice_seed.seed()

    assert (result.action, result.labels) == ("created", ())
    assert client.create_prompt.call_args.kwargs["labels"] == []


# --------------------------------------------------------------------------- #
# companion
# --------------------------------------------------------------------------- #


def test_companion_first_version_goes_live_with_its_tones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, companion_seed, latest=None)

    result = companion_seed.seed(model="gpt-4o-mini")

    assert (result.action, result.labels) == ("created", ("production",))
    _, kwargs = client.create_prompt.call_args
    assert kwargs["name"] == companion_prompts.SPEAK_PROMPT
    assert kwargs["type"] == "chat"
    assert kwargs["config"]["model"] == "gpt-4o-mini"
    assert kwargs["config"]["tones"] == companion_prompts.BOOTSTRAP_CONFIG["tones"]
    assert "model" not in companion_prompts.BOOTSTRAP_CONFIG


def test_companion_an_identical_latest_version_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _chat_prompt(
        companion_prompts.BOOTSTRAP_MESSAGES, companion_seed.bootstrap_config()
    )
    client = _client(monkeypatch, companion_seed, latest=latest)

    assert companion_seed.seed().action == "unchanged"
    client.create_prompt.assert_not_called()


def test_companion_a_changed_tone_is_a_new_unlabelled_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = companion_seed.bootstrap_config()
    config["tones"]["healthy"] = "Something else."
    latest = _chat_prompt(companion_prompts.BOOTSTRAP_MESSAGES, config)
    client = _client(monkeypatch, companion_seed, latest=latest)

    result = companion_seed.seed()

    assert (result.action, result.labels) == ("created", ())
    assert client.create_prompt.call_args.kwargs["labels"] == []
