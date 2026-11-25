# Skill: incident-triage

## Purpose

Rapidly classify an incoming incident, determine severity, identify the affected
component, and route to the correct responder — all within 5 minutes of alert receipt.

## When to invoke

- `infra-monitor` raises a `critical` or `warning` alert
- A human operator reports unexpected behaviour
- Two or more agents report overlapping faults within a 10-minute window

## Procedure

### Step 1 — Classify

| Signal | Classification |
|---|---|
| Host unreachable | `connectivity` |
| Backup job failed | `backup` |
| Storage < 10 % free | `capacity` |
| VM/CT unexpected stop | `workload` |
| Patch required by policy | `maintenance` |
| Unknown / ambiguous | `unclassified` → escalate |

### Step 2 — Assess severity

| Severity | Criteria |
|---|---|
| `critical` | Service down, data at risk, or security boundary breached |
| `warning` | Degraded but functional; action needed within 24 h |
| `info` | Anomaly observed; no immediate action required |

### Step 3 — Identify scope

Record: affected host(s), affected service(s), number of users/workloads impacted,
time of first observation.

### Step 4 — Route

Use the routing table in `agents/alert-router/policy.yaml`. If no match exists,
escalate to `human-operator`.

### Step 5 — Log

Write a triage entry via `audit-logger` including: incident ID, classification,
severity, scope, route taken, and timestamp.

## Exit criteria

- Incident is classified and routed, OR
- Incident is escalated to `human-operator` with a full triage summary

## Do NOT

- Attempt remediation during triage
- Suppress or downgrade severity without human approval
- Route to a destination outside `custos-arcae`
