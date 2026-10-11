"""Automated pytest suite for testing all plant health scenarios with mock LLM integration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fixtures.mock_llm_for_llm_pipeline_integration.test_scenarios import (  # noqa: E402
    SCENARIOS,
    run_scenario,
)


@pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
def test_all_scenarios_mock_execution(scenario_name: str) -> None:
    """Validate that every scenario passes end-to-end through the pipeline."""
    spec = SCENARIOS[scenario_name]
    result = run_scenario(scenario_name, spec, live_llm=False, verbose=False)

    assert result["passed"], f"Scenario {scenario_name} failed checks: {result['checks']}"
    assert result["actual_decisions"] == result["expected_decisions"]


@pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
def test_scenario_frontend_response_contract(scenario_name: str) -> None:
    """Verify that every scenario generates a valid Frontend Response Envelope.

    Contract must be identical to poc-ai-agent.
    """
    spec = SCENARIOS[scenario_name]
    result = run_scenario(scenario_name, spec, live_llm=False, verbose=False)

    for step in result["steps"]:
        fe_envelope = step["response_envelope"]
        fe_data = step["frontend_response"]

        # Validate response envelope structure
        assert fe_envelope["success"] is True
        assert fe_envelope["data"] == fe_data

        # Validate FE payload contract
        assert "plant_id" in fe_data and fe_data["plant_id"]
        assert "name" in fe_data and fe_data["name"]
        assert "species" in fe_data and fe_data["species"]
        assert "dayCount" in fe_data and isinstance(fe_data["dayCount"], int)
        assert "timestamp" in fe_data and fe_data["timestamp"]
        assert "health_status" in fe_data and fe_data["health_status"]
        assert "decision" in fe_data and fe_data["decision"]
        assert "companion_message" in fe_data and fe_data["companion_message"]

        if fe_data["decision"] == "CARE_ADVICE_REQUIRED":
            assert fe_data["care_plan"] is not None
            cp = fe_data["care_plan"]
            assert "id" in cp and cp["id"]
            assert "status_label" in cp and cp["status_label"]
            assert "assessment" in cp and cp["assessment"]
            assert "actions" in cp and len(cp["actions"]) > 0

            for action in cp["actions"]:
                assert "id" in action and action["id"]
                assert "priority" in action and isinstance(action["priority"], int)
                assert "label" in action and action["label"]
                assert "action" in action and action["action"]
                assert "type" in action and action["type"] in ("water", "move", "inspect", "other")
        else:
            assert fe_data["care_plan"] is None

        # Print FE response when pytest is executed with -s
        print(f"\n[FE Response Envelope: {scenario_name} Day {step['day']}]")
        print(json.dumps(fe_envelope, indent=2))
