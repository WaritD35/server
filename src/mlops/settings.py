"""Which Langfuse project a component talks to, and with which credentials.

Langfuse is split by project — one per LLM-using component — so there is a key
pair per component rather than one for the whole server. Secret values live in
``core.config.Settings`` (declared in ``.env.example``); this module only names
the components and resolves their credentials.

This is the *only* thing every component package shares. Everything else —
prompt handling, model calls, tracing, experiments, scorers — lives inside each
``mlops/<component>/`` package and is that component owner's call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from core.config import get_settings


class Component(StrEnum):
    """A bounded context that calls a VLM / LLM through :mod:`mlops`.

    Each member is simultaneously a subpackage of :mod:`mlops`, a Langfuse
    project, and one key pair below.
    """

    #: VLM — runs each probe against the on-prem VLM.
    ASSESSMENT = "assessment"
    #: LLM — verdicts + history → diagnosis, ranked actions.
    ADVICE = "advice"
    #: LLM — research document → probes + actions (offline authoring).
    KNOWLEDGE = "knowledge"
    #: VLM — species identification, once per plant.
    REGISTRY = "registry"
    #: LLM — 1st-person character companion dialogue projection.
    COMPANION = "companion"


#: The label a component reads in production. Promotion is moving this label in
#: Langfuse; there is no ship step on our side.
PRODUCTION_LABEL: Final[str] = "production"

#: Points at the newest version. Experiments and local runs only.
LATEST_LABEL: Final[str] = "latest"


@dataclass(frozen=True)
class LangfuseCredentials:
    """One Langfuse project's connection details."""

    host: str
    public_key: str
    secret_key: str

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.public_key and self.secret_key)


def credentials_for(component: Component) -> LangfuseCredentials:
    """The Langfuse project credentials for ``component``.

    An explicit mapping rather than ``getattr`` so a component added without its
    key pair is a type error, not a runtime surprise.
    """
    s = get_settings()
    pairs: dict[Component, tuple[str, str]] = {
        Component.ASSESSMENT: (
            s.langfuse_assessment_public_key,
            s.langfuse_assessment_secret_key,
        ),
        Component.ADVICE: (
            s.langfuse_advice_public_key,
            s.langfuse_advice_secret_key,
        ),
        Component.KNOWLEDGE: (
            s.langfuse_knowledge_public_key,
            s.langfuse_knowledge_secret_key,
        ),
        Component.REGISTRY: (
            s.langfuse_registry_public_key,
            s.langfuse_registry_secret_key,
        ),
        Component.COMPANION: (
            s.langfuse_companion_public_key,
            s.langfuse_companion_secret_key,
        ),
    }
    public_key, secret_key = pairs[component]
    return LangfuseCredentials(
        host=s.langfuse_host, public_key=public_key, secret_key=secret_key
    )
