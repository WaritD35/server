"""Register the ``advice`` diagnose prompt in Langfuse as a new version.

Owner: TODO

::

    uv run python -m mlops.advice.seed --check
    uv run python -m mlops.advice.seed --dry-run
    uv run python -m mlops.advice.seed [--model gpt-4o-mini]

Registers :data:`~mlops.advice.prompts.BOOTSTRAP_MESSAGES` with
:data:`~mlops.advice.prompts.BOOTSTRAP_CONFIG` (plus ``--model`` when given)
under :data:`~mlops.advice.prompts.DIAGNOSE_PROMPT`. Langfuse numbers the
versions; nothing is ever overwritten:

- **No prompt yet** — v1 is created with the ``production`` label.
- **The latest version already matches** (messages and config) — nothing is
  created.
- **Otherwise** — a new version is created *without* ``production``. Try it with
  ``python -m mlops.advice.experiment --version N``, then move the label in the
  Langfuse UI. ``--promote`` labels it straight away.

Like the experiment CLI, this touches Langfuse only — no database, no LLM.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Final

from langfuse.api import NotFoundError
from langfuse.model import ChatMessageDict, ChatPromptClient

from mlops.advice import prompts
from mlops.client import LangfuseNotConfiguredError, get_client
from mlops.settings import LATEST_LABEL, PRODUCTION_LABEL, Component

COMPONENT: Final = Component.ADVICE

COMMIT_MESSAGE: Final[str] = "seeded from mlops.advice.prompts"


@dataclass(frozen=True)
class SeedResult:
    #: ``created`` | ``unchanged`` | ``dry_run``
    action: str
    #: The version created, or the latest one when ``unchanged``.
    version: int | None
    labels: tuple[str, ...]


def bootstrap_config(model: str | None = None) -> dict[str, Any]:
    config = dict(prompts.BOOTSTRAP_CONFIG)
    if model and model.strip():
        config["model"] = model.strip()
    return config


def latest_version() -> ChatPromptClient | None:
    """The newest registered version, or ``None`` when the prompt does not exist yet."""
    try:
        prompt = get_client(COMPONENT).get_prompt(
            prompts.DIAGNOSE_PROMPT,
            type="chat",
            label=LATEST_LABEL,
            cache_ttl_seconds=0,
            max_retries=0,
        )
    except NotFoundError:
        return None
    if not isinstance(prompt, ChatPromptClient):
        raise TypeError(f"Langfuse prompt {prompts.DIAGNOSE_PROMPT!r} exists as a text prompt")
    return prompt


def _same_messages(prompt: ChatPromptClient) -> bool:
    registered = [(m.get("role"), m.get("content")) for m in prompt.prompt]  # type: ignore[union-attr]
    return registered == [(m["role"], m["content"]) for m in prompts.BOOTSTRAP_MESSAGES]


def seed(
    *,
    model: str | None = None,
    promote: bool = False,
    dry_run: bool = False,
    commit_message: str = COMMIT_MESSAGE,
) -> SeedResult:
    """Register the bootstrap prompt as a new version unless the latest one matches it."""
    config = bootstrap_config(model)
    latest = latest_version()
    if latest is not None and _same_messages(latest) and latest.config == config:
        return SeedResult("unchanged", latest.version, tuple(latest.labels))

    labels = (PRODUCTION_LABEL,) if promote or latest is None else ()
    if dry_run:
        return SeedResult("dry_run", None, labels)
    created = get_client(COMPONENT).create_prompt(
        name=prompts.DIAGNOSE_PROMPT,
        prompt=[
            ChatMessageDict(role=m["role"], content=m["content"])
            for m in prompts.BOOTSTRAP_MESSAGES
        ],
        type="chat",
        labels=list(labels),
        config=config,
        commit_message=commit_message,
    )
    return SeedResult("created", created.version, labels)


def main() -> None:
    """CLI entry point (``python -m mlops.advice.seed``)."""
    parser = argparse.ArgumentParser(
        description=f"Register {prompts.DIAGNOSE_PROMPT!r} in the advice Langfuse project."
    )
    parser.add_argument("--check", action="store_true", help="only verify the credentials")
    parser.add_argument(
        "--model", help="pin the version to this model id; omit to use the provider default"
    )
    parser.add_argument(
        "--promote", action="store_true", help="give the new version the production label"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would be registered; send nothing"
    )
    parser.add_argument("--message", default=COMMIT_MESSAGE, help="Langfuse commit message")
    args = parser.parse_args()

    # noqa: T201 below — a CLI is allowed to print.
    try:
        if args.check:
            ok = get_client(COMPONENT).auth_check()
            print("advice: credentials OK" if ok else "advice: auth check failed")  # noqa: T201
            raise SystemExit(0 if ok else 1)
        result = seed(
            model=args.model,
            promote=args.promote,
            dry_run=args.dry_run,
            commit_message=args.message,
        )
    except LangfuseNotConfiguredError as exc:
        raise SystemExit(str(exc)) from None

    if result.action == "dry_run":
        print(f"would create {prompts.DIAGNOSE_PROMPT} labels={list(result.labels)}")  # noqa: T201
        print(json.dumps(bootstrap_config(args.model), indent=2))  # noqa: T201
        print(json.dumps(prompts.BOOTSTRAP_MESSAGES, indent=2, ensure_ascii=False))  # noqa: T201
    elif result.action == "unchanged":
        print(f"{prompts.DIAGNOSE_PROMPT}@{result.version} already matches; nothing created")  # noqa: T201
    else:
        print(f"created {prompts.DIAGNOSE_PROMPT}@{result.version} labels={list(result.labels)}")  # noqa: T201


if __name__ == "__main__":
    main()
