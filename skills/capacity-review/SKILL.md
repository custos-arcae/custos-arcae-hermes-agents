# Skill: capacity-review

## Purpose

Produce a forward-looking capacity assessment across compute, storage, and network
resources, with projected exhaustion dates and actionable recommendations.

## When to invoke

- Weekly (scheduled)
- On demand when `capacity-planner` raises a warning or critical alert
- Before any planned infrastructure change (new VMs, storage expansion, migration)

## Procedure

### Step 1 — Snapshot current utilisation

Query `proxmox_status`, `pbs_status`, and `host_health` for:

| Resource | Metric to collect |
|---|---|
| Storage | Free %, total, used, growth rate (30 d) |
| Memory | Used %, total, peak (24 h) |
| CPU | Load 5 m as % of cores, peak (24 h) |
| Network | Uplink utilisation %, error rate |
| VM density | Count per node vs. soft/hard limit |

### Step 2 — Calculate trends

For each resource with ≥ 7 data points, compute:
- 7-day and 30-day moving average
- Linear projection to exhaustion using 30-day growth rate
- Acceleration flag if current growth > 2× the 30-day average

### Step 3 — Classify each resource

| Status | Condition |
|---|---|
| `healthy` | Projected exhaustion > 90 days |
| `watch` | Projected exhaustion 30–90 days |
| `action-required` | Projected exhaustion < 30 days |
| `critical` | Currently at or beyond threshold |

### Step 4 — Produce report

```
CAPACITY REVIEW — YYYY-MM-DD
Resource         Host/Datastore        Current   Projected   Status
storage          pbs-01/datastore-main  78% free  2027-02-14  watch
memory           pve-01                 62% used  —           healthy
cpu              pve-02                 41% load  —           healthy
vm-density       pve-01                 38/40     —           watch

Recommendations:
  1. [action-required items first]
  2. [watch items]
```

### Step 5 — Log and route

Write the report via `audit-logger`. Route any `action-required` or `critical` items
via `alert-router` to `human-operator`.

## Exit criteria

- Report is written to audit log
- All `action-required` and `critical` items are escalated

## Do NOT

- Resize, migrate, or provision any resource
- Modify thresholds without a policy change approved by two reviewers
- Suppress a `critical` finding
