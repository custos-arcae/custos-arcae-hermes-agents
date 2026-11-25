"""
Policy conformance tests — validate every YAML policy file against its JSON Schema.

These tests run in CI on every pull request. A policy file that fails schema
validation must not be merged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import jsonschema
import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
POLICIES_DIR = REPO_ROOT / "policies"
AGENTS_DIR = REPO_ROOT / "agents"


def _load_schema(name: str) -> dict[str, Any]:
    with open(SCHEMAS_DIR / name) as f:
        return cast("dict[str, Any]", json.load(f))


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return cast("dict[str, Any]", yaml.safe_load(f))


# ── tool-access.yaml ──────────────────────────────────────────────────────────

def test_tool_access_valid() -> None:
    schema = _load_schema("tool-access.schema.json")
    data = _load_yaml(POLICIES_DIR / "tool-access.yaml")
    jsonschema.validate(data, schema)


# ── agent policy.yaml files ───────────────────────────────────────────────────

AGENT_POLICY_FILES = sorted(AGENTS_DIR.glob("*/policy.yaml"))


@pytest.mark.parametrize("policy_path", AGENT_POLICY_FILES, ids=lambda p: p.parent.name)
def test_agent_policy_valid(policy_path: Path) -> None:
    schema = _load_schema("agent-config.schema.json")
    data = _load_yaml(policy_path)
    jsonschema.validate(data, schema)


@pytest.mark.parametrize("policy_path", AGENT_POLICY_FILES, ids=lambda p: p.parent.name)
def test_agent_policy_name_matches_directory(policy_path: Path) -> None:
    """The 'agent' field in policy.yaml must match the parent directory name."""
    data = _load_yaml(policy_path)
    assert data["agent"] == policy_path.parent.name, (
        f"policy.yaml 'agent' field is '{data['agent']}' but directory is '{policy_path.parent.name}'"
    )


# ── approval-gates.yaml ───────────────────────────────────────────────────────

def test_approval_gates_has_required_fields() -> None:
    data = _load_yaml(POLICIES_DIR / "approval-gates.yaml")
    assert "version" in data
    assert "gates" in data
    for gate in data["gates"]:
        assert "id" in gate
        assert "action" in gate
        assert "reason" in gate
        assert "approver" in gate


# ── autonomous-actions.yaml ───────────────────────────────────────────────────

def test_autonomous_actions_no_overlap_with_approval_gates() -> None:
    """No action may appear in both autonomous-actions and approval-gates."""
    auto = _load_yaml(POLICIES_DIR / "autonomous-actions.yaml")
    gates = _load_yaml(POLICIES_DIR / "approval-gates.yaml")

    auto_ids: set[str] = {a["id"] for a in auto.get("autonomous_actions", [])}
    gate_ids: set[str] = {g["id"] for g in gates.get("gates", [])}
    overlap = auto_ids & gate_ids
    assert not overlap, f"Actions appear in both files: {overlap}"


# ── escalation-matrix.yaml ────────────────────────────────────────────────────

def test_escalation_matrix_has_default() -> None:
    data = _load_yaml(POLICIES_DIR / "escalation-matrix.yaml")
    assert "default" in data, "escalation-matrix.yaml must define a default rule"
    assert "rules" in data
    assert len(data["rules"]) > 0


# ── cross-project-isolation.yaml ──────────────────────────────────────────────

def test_cross_project_isolation_default_deny() -> None:
    data = _load_yaml(POLICIES_DIR / "cross-project-isolation.yaml")
    assert data.get("default_policy") == "deny", (
        "cross-project-isolation.yaml must set default_policy: deny"
    )
    assert "denied" in data
    assert len(data["denied"]) > 0


# ── tool-access ↔ agent policy consistency ───────────────────────────────────

def test_tool_access_covers_all_registered_tools() -> None:
    """Every tool referenced in an agent policy.yaml must exist in tool-access.yaml."""
    tool_access = _load_yaml(POLICIES_DIR / "tool-access.yaml")
    registered = {t["name"] for t in tool_access["tools"]}

    for policy_path in AGENT_POLICY_FILES:
        data = _load_yaml(policy_path)
        autonomy = data.get("autonomy", {})
        for level in ("autonomous", "notify", "approve"):
            for entry in autonomy.get(level, []):
                tool = entry.get("tool")
                if tool is not None:
                    assert tool in registered, (
                        f"agent '{policy_path.parent.name}' references tool '{tool}' "
                        f"in '{level}' but it is not in policies/tool-access.yaml"
                    )


def test_tool_access_permitted_agents_are_real() -> None:
    """Every agent listed in tool-access.yaml permitted_agents must have a policy file."""
    tool_access = _load_yaml(POLICIES_DIR / "tool-access.yaml")
    real_agents = {p.parent.name for p in AGENT_POLICY_FILES}

    for tool in tool_access["tools"]:
        for agent in tool.get("permitted_agents", []):
            assert agent in real_agents, (
                f"tool '{tool['name']}' permits agent '{agent}' "
                f"but agents/{agent}/policy.yaml does not exist"
            )
