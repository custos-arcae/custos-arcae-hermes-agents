# custos-arcae-hermes-agents

Configuration-as-code repository for the **Hermes** AI agent fleet operating across the
`custos-arcae` infrastructure estate.

This repo defines *what* each agent is, *what* it may do, and *how* it is governed.
Runtime orchestration lives elsewhere; this is the single source of truth for agent behaviour.

---

## Repository layout

```
agents/           ← One directory per agent (system-prompt, policy, learned skills)
skills/           ← Shared reusable procedures callable by any agent
mcp-servers/      ← Model Context Protocol servers exposing infrastructure tools
policies/         ← Global governance: approval gates, autonomy, escalation, isolation
schemas/          ← JSON Schemas that machine-validate every YAML policy file
profiles/         ← Human-facing identity and config templates (SOUL.md per profile)
tests/            ← Policy conformance and integration tests
docs/             ← Architecture, deployment, threat model, ADRs
```

## Agents

| Agent | Responsibility |
|---|---|
| `infra-monitor` | Watches Proxmox, PBS, and network switches; raises alerts |
| `patch-orchestrator` | Schedules and applies patches under approval |
| `backup-verifier` | Confirms backups complete and restore paths are healthy |
| `capacity-planner` | Alerts when resources approach defined thresholds |
| `alert-router` | Routes alerts to the correct project Hermes instance |
| `audit-logger` | Appends immutable action records; never deletes |

## Quick start

```bash
# Validate all policy files against their schemas
python -m pytest tests/unit -q

# Run the MCP server in development mode
cd mcp-servers/ca-mcp-server
uv pip install -e ".[dev]"
uv run ca-mcp-server --dev
```

## Requirements

- Python **3.12+**
- [uv](https://github.com/astral-sh/uv)

## Governance

See [`AGENTS.md`](AGENTS.md) for AI operating rules and [`SCOPE.md`](SCOPE.md) for repository boundaries.
