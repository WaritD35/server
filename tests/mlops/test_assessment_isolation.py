"""``mlops.assessment`` must import no bounded context, no database and no torch.

``assessment`` calls into ``mlops.assessment``, so an import the other way
closes a cycle. The VLM runtime adds a second trap: the in-process backend needs
torch, which only the opt-in ``vlm-local`` group installs. With
``VLM_PROVIDER=vllm`` — the server default — choosing a backend must never touch
it.
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
import mlops.assessment.backends
import mlops.assessment.experiment  # noqa: F401
import mlops.assessment.runtime  # noqa: F401

backend = mlops.assessment.backends.get_vlm_backend()
assert type(backend).__name__ == "VLLMBackend", type(backend)

forbidden = sorted(
    name
    for name in sys.modules
    if name == "core.db"
    or name.startswith("sqlalchemy")
    or name.split(".")[0] in {
        "registry", "knowledge", "assessment", "advice", "companion",
        "action", "capture", "orchestrator", "torch", "transformers",
    }
)
print(",".join(forbidden))
"""


def test_the_assessment_runtime_imports_no_bounded_context_database_or_torch() -> None:
    env = {
        **_WINDOWS_ENV,
        "PYTHONPATH": str(SRC),
        "PATH": "/usr/bin:/bin",
        "VLM_PROVIDER": "vllm",
        "VLM_API_BASE": "http://vlm.invalid/v1",
    }
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    pulled_in = [name for name in result.stdout.strip().split(",") if name]
    assert pulled_in == [], (
        "mlops.assessment must not need a database, a bounded context or torch, "
        f"but importing it pulled in: {pulled_in}"
    )
