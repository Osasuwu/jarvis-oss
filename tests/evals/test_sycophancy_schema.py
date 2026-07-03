"""Test suite for sycophancy eval harness schema and structure."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import pytest
import yaml

SCENARIOS_DIR = Path(__file__).resolve().parent.parent.parent / "evals" / "sycophancy"

def load_scenario(path: Path) -> dict[str, Any]:
    """Load and parse a scenario YAML file."""
    with open(path, "r") as f:
        return yaml.safe_load(f)

def test_scenarios_directory_exists():
    """Acceptance criterion: 8-12 scenarios stored in evals/sycophancy/."""
    assert SCENARIOS_DIR.exists(), f"Directory {SCENARIOS_DIR} not found"
    assert SCENARIOS_DIR.is_dir()

def test_scenario_count():
    """Acceptance criterion: At least 8 scenarios, no more than 12."""
    scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    scenario_count = len(scenario_files)
    assert 8 <= scenario_count <= 12, f"Expected 8-12 scenarios, found {scenario_count}"

def test_scenario_file_structure():
    """Acceptance criterion: Each scenario has required fields."""
    scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    assert len(scenario_files) > 0, "No scenario files found"
    required_fields = {"setup", "proposal", "flaw", "expected_pushback"}
    for scenario_file in scenario_files:
        scenario = load_scenario(scenario_file)
        assert scenario is not None, f"{scenario_file} is empty"
        missing_fields = required_fields - set(scenario.keys())
        assert not missing_fields, f"{scenario_file} missing fields: {missing_fields}"

def test_scenario_has_category():
    """Acceptance criterion: Each scenario has a category field."""
    scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    valid_categories = {"code", "architecture", "process"}
    for scenario_file in scenario_files:
        scenario = load_scenario(scenario_file)
        assert "category" in scenario, f"{scenario_file} missing category field"
        assert scenario["category"] in valid_categories, f"{scenario_file} invalid category"

def test_scenario_categories_span_three_types():
    """Acceptance criterion: Scenarios span at least 3 categories."""
    scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    categories = set()
    for scenario_file in scenario_files:
        scenario = load_scenario(scenario_file)
        categories.add(scenario.get("category"))
    assert len(categories) >= 3, f"Expected at least 3 categories, found {len(categories)}"

def test_scenario_has_source_field():
    """Acceptance criterion: Scenarios must have source field."""
    scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    for scenario_file in scenario_files:
        scenario = load_scenario(scenario_file)
        assert "source" in scenario, f"{scenario_file} missing source field"

def test_past_outcome_sourced_scenarios():
    """Acceptance criterion: At least 2 scenarios sourced from past outcomes."""
    scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    past_outcome_sources = {"afk_system", "sandcastle", "fok_batch"}
    past_outcome_scenarios = []
    for scenario_file in scenario_files:
        scenario = load_scenario(scenario_file)
        source = scenario.get("source", "").lower()
        for outcome_source in past_outcome_sources:
            if outcome_source in source:
                past_outcome_scenarios.append((scenario_file.name, source))
                break
    assert len(past_outcome_scenarios) >= 2, f"Expected 2+ past-outcome scenarios, found {len(past_outcome_scenarios)}"

def test_scenario_fields_are_strings():
    """All scenario fields should be non-empty strings."""
    scenario_files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    for scenario_file in scenario_files:
        scenario = load_scenario(scenario_file)
        for field in ["setup", "proposal", "flaw", "expected_pushback"]:
            assert isinstance(scenario[field], str), f"{scenario_file}.{field} not a string"
            assert scenario[field].strip(), f"{scenario_file}.{field} empty"
