# Scope — custos-arcae-hermes-agents

## What this repository owns

| Area | Description |
|---|---|
| Agent definitions | System prompts, personality, and autonomy policies for every Hermes agent |
| Shared skills | Reusable procedures (incident-triage, backup-review, capacity-review, patch-planning) |
| MCP tooling | `ca-mcp-server` — the Model Context Protocol server that exposes infrastructure read/write tools |
| Policy layer | Approval gates, autonomous actions, escalation matrix, cross-project isolation rules |
| Governance | JSON Schemas that machine-validate all YAML policy files |

## What this repository does NOT own

| Area | Lives in |
|---|---|
| Infrastructure-as-code (Ansible playbooks, Terraform) | `custos-arcae-datacenter` |
| Application workloads | individual project repos |
| Secrets / vault files | `.vault-password` is gitignored; vault lives on the operator machine |
| Evidence stores (console captures, SCP exports) | restricted evidence store named in the change plan |
| Runtime agent binaries / orchestration engine | deployed separately; this repo is the configuration source |

## Change-control rules

1. Every change to `policies/approval-gates.yaml` or `policies/autonomous-actions.yaml` requires explicit human review before merge.
2. Agent `policy.yaml` changes that **expand** autonomy (new tools, relaxed approval) are high-risk — two-reviewer rule.
3. Agent `policy.yaml` changes that **restrict** autonomy are low-risk — one-reviewer rule.
4. New MCP tools must have a matching entry in `schemas/tool-access.schema.json` and `policies/tool-access.yaml`.
5. Breaking changes to `ca-mcp-server` API must have an ADR in `docs/adr/`.
