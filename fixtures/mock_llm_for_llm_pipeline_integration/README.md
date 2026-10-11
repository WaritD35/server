# Server Mock LLM & Scenario Verification Suite

> **Purpose:** Portable, self-contained scenario testing environment for the `server` repository.
> Allows testing and inspecting LLM pipeline results (Assessment, Advice, 3NF Actions, and Companion Dialogue)
> across all plant health scenarios — both offline using canned fixtures and live using Ollama / OpenAI.
> **Includes exact Frontend (FE) response envelopes matching `poc-ai-agent`!**

## Directory Structure

```
fixtures/mock_llm_for_llm_pipeline_integration/
├── README.md                   # This documentation
├── test_scenarios.py           # Interactive test runner script (supports --fe, --verbose, --live-llm)
├── mock_model.py               # LangChain MockChatModel with structured output support
├── responses/                  # Canned LLM responses
│   ├── advice/                 # Mock CarePlans (diagnosis + 3NF action items)
│   │   ├── healthy_baseline.json
│   │   ├── leaf_yellowing_mild.json
│   │   ├── leaf_yellowing_severe.json
│   │   ├── overwatering_stress.json
│   │   ├── underwatering_drought.json
│   │   ├── fungal_infection.json
│   │   ├── pest_infestation.json
│   │   ├── light_stress.json
│   │   └── multi_symptom.json
│   └── companion/              # Mock Companion dialogue messages
│       ├── healthy_steady.json
│       ├── care_advice_message.json
│       ├── improvement_recovery.json
│       └── crisis_alert.json
├── scenarios/                  # Scenario observation sequences (raw input JSON)
│   ├── no_change.json
│   ├── new_symptom.json
│   ├── severity_increase.json
│   ├── improvement.json
│   ├── health_status_change.json
│   └── milestone_lifecycle.json
└── expected_outputs/           # Golden assertion specs for each scenario
    ├── scenario_no_change.json
    ├── scenario_new_symptom.json
    ├── scenario_severity_increase.json
    ├── scenario_improvement.json
    ├── scenario_health_status_change.json
    └── scenario_multi_day.json
```

## How to Run & View FE Responses

### 1. View Frontend Response Envelopes (Matching `poc-ai-agent`)
```bash
# Print formatted Frontend JSON envelopes for all steps of a scenario:
uv run python fixtures/mock_llm_for_llm_pipeline_integration/test_scenarios.py --scenario new_symptom --fe

# Run all 6 scenarios with frontend envelopes:
uv run python fixtures/mock_llm_for_llm_pipeline_integration/test_scenarios.py --fe

# Run with full verbose diagnostic AND frontend envelopes:
uv run python fixtures/mock_llm_for_llm_pipeline_integration/test_scenarios.py --scenario new_symptom --verbose
```

### 2. Run All 6 Scenarios Offline (No API keys or Ollama required)
```bash
uv run python fixtures/mock_llm_for_llm_pipeline_integration/test_scenarios.py

# Available scenarios:
#   no_change, new_symptom, severity_increase, improvement, health_status_change, multi_day
```

### 3. Run with Live Ollama or OpenAI
```bash
# Run against local Ollama (LLM_PROVIDER=ollama in .env)
uv run python fixtures/mock_llm_for_llm_pipeline_integration/test_scenarios.py --scenario new_symptom --live-llm --fe
```

### 4. Pytest Integration (Automated Test Suite)
```bash
# Run all scenario tests + FE contract tests:
uv run pytest tests/mlops/test_scenarios_mock.py -v

# Run with -s to print all FE JSON response payloads directly in terminal:
uv run pytest tests/mlops/test_scenarios_mock.py -s -k "test_scenario_frontend_response_contract and new_symptom"
```
