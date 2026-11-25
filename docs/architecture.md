# Architecture — custos-arcae-hermes-agents

## Overview

Hermes is a **configuration-driven agent fleet**. This repository is the single source
of truth for agent identity, authority, and governance. The runtime that executes agents
is deployed separately and reads its configuration from this repo at startup.

```
┌─────────────────────────────────────────────────────────────────┐
│                     custos-arcae-hermes-agents                  │
│                                                                 │
│  agents/          skills/         policies/       schemas/      │
│  ├─ infra-monitor ├─ incident-    ├─ tool-access  ├─ tool-access│
│  ├─ patch-orch    │  triage       ├─ approval-    ├─ policy     │
│  ├─ backup-verif  ├─ backup-      │  gates        └─ agent-     │
│  ├─ capacity-plan │  review       ├─ autonomous-     config     │
│  ├─ alert-router  ├─ capacity-    │  actions                    │
│  └─ audit-logger  │  review       ├─ escalation-                │
│                   └─ patch-       │  matrix                     │
│                      planning     └─ cross-project-             │
│                                        isolation                │
│                                                                 │
│  mcp-servers/ca-mcp-server/                                     │
│  └─ Exposes: proxmox_status, pbs_status,                        │
│              host_health, connectivity_status                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Runtime (deployed separately)            │
│  Reads agents/*/policy.yaml + system-prompt.md at startup       │
│  Calls ca-mcp-server tools over MCP stdio transport             │
│  Enforces policies/ before every action                         │
│  Writes every action to audit-logger                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     custos-arcae infrastructure                 │
│  Proxmox VE cluster  │  PBS  │  Managed hosts  │  Network       │
└─────────────────────────────────────────────────────────────────┘
```

## Design decisions

### Why configuration-as-code?

Agent behaviour must be reviewable, versionable, and auditable. Storing system prompts
and policies as files in Git means every change goes through a pull request with
CODEOWNERS enforcement. There is no runtime API that can silently expand agent authority.

### Why separate agents from skills?

- **Agents** (`agents/`) are persistent identities with defined autonomy boundaries.
- **Skills** (`skills/`) are stateless procedures any authorised agent may invoke.

This separation allows a new agent to reuse existing skills without duplicating logic,
and allows skills to be updated independently of agent identity.

### Why MCP for tooling?

The Model Context Protocol provides a standard, auditable interface between agents and
infrastructure. Every tool call is a structured message that can be logged, rate-limited,
and blocked by policy without changing agent code.

### Why JSON Schema for policies?

Machine validation catches policy errors at PR time rather than at runtime. A malformed
`policy.yaml` that accidentally grants `autonomous` access to a write tool will fail CI
before it can be merged.

## Data flow

```
infra-monitor ──alert──► alert-router ──route──► human-operator
                                │
                                └──────────────► audit-logger (every alert)

patch-orchestrator ──plan──► human-operator (approval gate)
                     ◄──approved──
                     ──execute──► ca-mcp-server ──► Proxmox / PBS / hosts
                     ──log──────► audit-logger

capacity-planner ──report──► alert-router ──► audit-logger
backup-verifier  ──report──► alert-router ──► audit-logger
```

## Trust boundaries

| Boundary | Enforcement |
|---|---|
| Agent → infrastructure | `policies/tool-access.yaml` + MCP tool scope |
| Agent → other project | `policies/cross-project-isolation.yaml` (default deny) |
| Agent → human approval | `policies/approval-gates.yaml` |
| Agent → audit log | `agents/audit-logger/policy.yaml` (append-only, no agent can write directly) |
| Repo → production | CI policy validation + CODEOWNERS two-reviewer rule |
