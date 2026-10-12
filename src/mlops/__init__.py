"""MLOps — every VLM / LLM call in the system, and the experiments behind them.

Not a bounded context: ``mlops`` owns no Postgres schema and no HTTP surface. It
is a shared library, alongside ``core``, that the LLM-using contexts depend on::

    assessment ─┐
    advice     ─┼─▶ mlops ─┬─▶ Langfuse  (prompts / traces / datasets / scores)
    knowledge  ─┤          └─▶ VLM (on-prem, vLLM) · LLM (external, text only)
    companion  ─┘

**Langfuse manages the prompts**, including their versions, the ``production``
label that decides which version is live, and the ``config`` blob holding each
prompt's hyper-parameters. Nothing about a prompt is stored in this repository
and nothing is written to Postgres — promotion is moving a label in Langfuse.

**One package per component**, each a separate Langfuse project with its own key
pair, and each with its own owner. A package is self-contained — it decides how
it handles prompts, calls models, traces and scores — so the four owners do not
have to agree on an abstraction to work in parallel. The only thing shared is
which credentials belong to which component (:mod:`mlops.settings`,
:mod:`mlops.client`).

A context imports only its own package::

    from mlops.assessment import ProbeInput, run_probe

**The dependency is one-way.** ``mlops`` never imports a bounded context, so its
entry points take plain values and dataclasses defined here; the calling context
maps its own models onto them.

Nothing is implemented yet — this package is template scaffolding.
"""

from mlops.settings import Component

__all__ = ["Component"]
