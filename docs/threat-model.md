# Threat Model — custos-arcae-hermes-agents

## Scope

This threat model covers the Hermes agent fleet as defined in this repository.
It does not cover the underlying Proxmox/PBS infrastructure (see `custos-arcae-datacenter`).

---

## Assets

| Asset | Sensitivity | Location |
|---|---|---|
| Proxmox API tokens | Critical | Environment variables only |
| PBS API tokens | Critical | Environment variables only |
| SSH private keys | Critical | Operator machine; never in this repo |
| Audit log | High | `HERMES_AUDIT_PATH`; append-only |
| Agent policy files | High | This repo; Git-controlled |
| Infrastructure topology | Medium | This repo (hostnames, VLANs, IPs) |
| Backup data | Critical | PBS datastores; agents have read-only access |

---

## Threat actors

| Actor | Capability | Motivation |
|---|---|---|
| External attacker | Network access to management VLAN | Disruption, data exfiltration |
| Compromised agent | Full tool access within its policy scope | Escalation of privilege, data exfiltration |
| Malicious insider | Git write access to this repo | Expand agent autonomy, disable controls |
| Supply chain | PyPI package compromise | Code execution in ca-mcp-server |

---

## Threats and mitigations

### T1 — Agent autonomy expansion via policy PR

**Threat:** An attacker with Git write access submits a PR that adds a write tool to
`autonomous` in an agent's `policy.yaml`.

**Mitigation:**
- CODEOWNERS requires two reviewers for `agents/*/policy.yaml`
- `policies/approval-gates.yaml#autonomy-expansion` requires two-reviewer sign-off
- CI runs `test_agent_policy_valid` — schema rejects unknown tool names
- `test_autonomous_actions_no_overlap_with_approval_gates` catches gate bypass

**Residual risk:** Low — requires compromise of two reviewer accounts simultaneously.

---

### T2 — Credential exposure via audit log

**Threat:** A tool returns a response containing an API token or password, and
`audit-logger` writes it to the audit store in plaintext.

**Mitigation:**
- `audit-logger` `redact_secret` action is mandatory before every write
- `agents/audit-logger/policy.yaml` forbids writing unredacted secrets
- `AGENTS.md` §6 forbids logging secrets at the agent instruction level

**Residual risk:** Medium — depends on redaction coverage; new secret formats may be missed.

---

### T3 — Cross-project lateral movement

**Threat:** A compromised agent attempts to access resources in `custos-arcae-datacenter`
or another project.

**Mitigation:**
- `policies/cross-project-isolation.yaml` sets `default_policy: deny`
- API tokens are scoped to `custos-arcae` resources only
- Network VLANs are not routable between projects
- `AGENTS.md` §5 forbids cross-project access at the instruction level

**Residual risk:** Low — requires both token scope bypass and network route.

---

### T4 — Audit log tampering

**Threat:** An attacker modifies or deletes audit entries to hide malicious activity.

**Mitigation:**
- `audit-logger` is the only agent with write access to the audit store
- `agents/audit-logger/policy.yaml` forbids edit, overwrite, and delete
- SHA-256 checksum on every entry; mismatch triggers critical escalation
- Storage-level write-once (S3 Object Lock or equivalent) recommended in production

**Residual risk:** Low if object-lock storage is used; Medium with file-based storage.

---

### T5 — Supply chain attack via PyPI dependency

**Threat:** A malicious version of `mcp`, `httpx`, or `pydantic` is published to PyPI
and installed in `ca-mcp-server`.

**Mitigation:**
- `uv.lock` pins exact versions (generate with `uv lock`)
- CI installs from lockfile only
- Dependencies are limited to well-known, actively maintained packages
- No arbitrary code execution at install time (no `setup.py` scripts)

**Residual risk:** Medium — lockfile must be kept current and reviewed.

---

### T6 — Escalation suppression

**Threat:** An attacker modifies `escalation-matrix.yaml` to route critical alerts
to a dead channel, preventing the operator from being notified.

**Mitigation:**
- `policies/escalation-matrix.yaml` is under CODEOWNERS
- `test_escalation_matrix_has_default` ensures a fallback rule always exists
- `alert-router` forbidden list prohibits suppressing critical alerts without logging
- Audit log records every routing decision

**Residual risk:** Low — requires Git compromise plus CI bypass.

---

### T7 — SSH key compromise via host_health tool

**Threat:** The `host_health` tool is used to execute arbitrary commands on managed hosts
if the SSH key is compromised.

**Mitigation:**
- `host_health` only runs a fixed set of read-only commands (hardcoded in the module)
- SSH key is scoped to a dedicated `hermes-readonly` user with no sudo
- `StrictHostKeyChecking=yes` prevents MITM
- `BatchMode=yes` prevents interactive prompt exploitation

**Residual risk:** Medium — SSH key theft still allows read access to all managed hosts.

---

## Risk summary

| ID | Threat | Likelihood | Impact | Risk |
|---|---|---|---|---|
| T1 | Autonomy expansion via PR | Low | Critical | Medium |
| T2 | Credential in audit log | Medium | High | High |
| T3 | Cross-project movement | Low | Critical | Medium |
| T4 | Audit log tampering | Low | Critical | Medium |
| T5 | Supply chain | Medium | High | High |
| T6 | Escalation suppression | Low | High | Medium |
| T7 | SSH key compromise | Medium | High | High |

**Priority actions:**
1. Generate and commit `uv.lock` for `ca-mcp-server` (mitigates T5)
2. Implement S3 Object Lock or equivalent for audit store (mitigates T4)
3. Add secret-pattern scanning to `audit-logger` redaction logic (mitigates T2)
4. Rotate SSH keys on a 90-day schedule (mitigates T7)
