"""An experiment must run with no database — no Docker, no ``DATABASE_URL``.

Prompts, datasets, runs and scores all live in Langfuse, so a prompt experiment
has nothing to ask Postgres. Keeping it that way is what lets anyone iterate on
a prompt from a laptop with only two API keys.

The rule is easy to break by accident: one convenience import of a bounded
context, or of ``core.db`` (which builds an engine at import time), and the
experiment CLI starts needing infrastructure it never uses. This test fails at
the import, before anyone discovers it by having Docker stopped.
"""

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

#: Windows cannot start Python's networking modules without these.
_WINDOWS_ENV = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR") if k in os.environ}

PROBE = """
import sys
import mlops.knowledge.experiment  # noqa: F401

forbidden = sorted(
    name
    for name in sys.modules
    if name == "core.db"
    or name.startswith("sqlalchemy")
    or name.split(".")[0] in {
        "registry", "capture", "knowledge", "assessment",
        "advice", "companion", "orchestrator",
    }
)
print(",".join(forbidden))
"""


def test_the_experiment_cli_imports_no_database_and_no_bounded_context() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env={**_WINDOWS_ENV, "PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    pulled_in = [name for name in result.stdout.strip().split(",") if name]
    assert pulled_in == [], (
        "mlops.knowledge.experiment must not need a database or a bounded "
        f"context, but importing it pulled in: {pulled_in}"
    )
