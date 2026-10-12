"""Register the ``assessment`` probe prompt in Langfuse as a new version.

Owner: TODO

::

    uv run python -m mlops.assessment.seed --check
    uv run python -m mlops.assessment.seed --model OpenGVLab/InternVL3_5-2B --dry-run
    uv run python -m mlops.assessment.seed --model OpenGVLab/InternVL3_5-2B

Registers :data:`~mlops.assessment.prompts.BOOTSTRAP_PROMPT` with
:data:`~mlops.assessment.prompts.BOOTSTRAP_CONFIG` (plus ``--model``) under
:data:`~mlops.assessment.prompts.PROBE_PROMPT`. Langfuse numbers the versions;
nothing is ever overwritten:

- **No prompt yet** — v1 is created with the ``production`` label, because the
  runtime cannot run without one.
- **The latest version already matches** (text and config) — nothing is created.
- **Otherwise** — a new version is created *without* ``production``. Try it with
  ``python -m mlops.assessment.experiment --version N``, then move the label in
  the Langfuse UI. ``--promote`` labels it straight away.

Like the experiment CLI, this touches Langfuse only — no database, no VLM.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Final

from langfuse.api import NotFoundError
from langfuse.model import TextPromptClient

from mlops.assessment import prompts
from mlops.client import LangfuseNotConfiguredError, get_client
from mlops.settings import LATEST_LABEL, PRODUCTION_LABEL, Component

COMPONENT: Final = Component.ASSESSMENT

COMMIT_MESSAGE: Final[str] = "seeded from mlops.assessment.prompts"


@dataclass(frozen=True)
class SeedResult:
    #: ``created`` | ``unchanged`` | ``dry_run``
    action: str
    #: The version created, or the latest one when ``unchanged``.
    version: int | None
    labels: tuple[str, ...]


def bootstrap_config(model: str) -> dict[str, Any]:
    if not model.strip():
        raise ValueError("model must not be empty")
    return {"model": model.strip(), **prompts.BOOTSTRAP_CONFIG}


def latest_version() -> TextPromptClient | None:
    """The newest registered version, or ``None`` when the prompt does not exist yet."""
    try:
        prompt = get_client(COMPONENT).get_prompt(
            prompts.PROBE_PROMPT,
            type="text",
            label=LATEST_LABEL,
            cache_ttl_seconds=0,
            max_retries=0,
        )
    except NotFoundError:
        return None
    if not isinstance(prompt, TextPromptClient):
        raise TypeError(f"Langfuse prompt {prompts.PROBE_PROMPT!r} exists as a chat prompt")
    return prompt


def seed(
    *,
    model: str,
    promote: bool = False,
    dry_run: bool = False,
    commit_message: str = COMMIT_MESSAGE,
) -> SeedResult:
    """Register the bootstrap prompt as a new version unless the latest one matches it."""
    config = bootstrap_config(model)
    latest = latest_version()
    if latest is not None and latest.prompt == prompts.BOOTSTRAP_PROMPT and latest.config == config:
        return SeedResult("unchanged", latest.version, tuple(latest.labels))

    labels = (PRODUCTION_LABEL,) if promote or latest is None else ()
    if dry_run:
        return SeedResult("dry_run", None, labels)
    created = get_client(COMPONENT).create_prompt(
        name=prompts.PROBE_PROMPT,
        prompt=prompts.BOOTSTRAP_PROMPT,
        type="text",
        labels=list(labels),
        config=config,
        commit_message=commit_message,
    )
    return SeedResult("created", created.version, labels)


def main() -> None:
    """CLI entry point (``python -m mlops.assessment.seed``)."""
    parser = argparse.ArgumentParser(
        description=f"Register {prompts.PROBE_PROMPT!r} in the assessment Langfuse project."
    )
    parser.add_argument("--check", action="store_true", help="only verify the credentials")
    parser.add_argument("--model", help="the VLM model id the version runs on (required)")
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
            print("assessment: credentials OK" if ok else "assessment: auth check failed")  # noqa: T201
            raise SystemExit(0 if ok else 1)
        if not args.model:
            parser.error("--model is required (the served VLM model id)")
        result = seed(
            model=args.model,
            promote=args.promote,
            dry_run=args.dry_run,
            commit_message=args.message,
        )
    except LangfuseNotConfiguredError as exc:
        raise SystemExit(str(exc)) from None

    if result.action == "dry_run":
        print(f"would create {prompts.PROBE_PROMPT} labels={list(result.labels)}")  # noqa: T201
        print(json.dumps(bootstrap_config(args.model), indent=2))  # noqa: T201
        print(prompts.BOOTSTRAP_PROMPT)  # noqa: T201
    elif result.action == "unchanged":
        print(f"{prompts.PROBE_PROMPT}@{result.version} already matches; nothing created")  # noqa: T201
    else:
        print(f"created {prompts.PROBE_PROMPT}@{result.version} labels={list(result.labels)}")  # noqa: T201


if __name__ == "__main__":
    main()
