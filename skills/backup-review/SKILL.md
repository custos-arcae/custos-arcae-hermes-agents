# Skill: backup-review

## Purpose

Systematically review the state of all backups across the PBS estate, identify gaps,
and produce a concise health report for the operator.

## When to invoke

- After every nightly backup window
- On demand when `backup-verifier` raises a warning or critical alert
- As part of a monthly infrastructure health review

## Procedure

### Step 1 — Collect job status

Query `pbs_status` for all backup jobs. Record: job name, last run timestamp,
result (pass/fail/skipped), duration, and data size.

### Step 2 — Identify gaps

Flag any job where:
- Result is `fail` or `skipped`
- Last successful run is older than `max_backup_age` (see `agents/backup-verifier/policy.yaml`)
- Duration has increased > 50 % over the 7-day rolling average

### Step 3 — Check datastore health

Query `pbs_status` for each datastore. Flag:
- Free space < 20 %
- Any uncorrectable errors reported
- Verify task not completed in the last 7 days

### Step 4 — Restore readiness

Confirm:
- Last successful restore test is within `restore_test_interval`
- Scratch target is available for the next scheduled restore test

### Step 5 — Produce report

```
BACKUP REVIEW — YYYY-MM-DD
Jobs total: N | Pass: N | Fail: N | Skipped: N
Datastores healthy: N / N
Restore test due: YYYY-MM-DD
Gaps: [list or "none"]
Recommendation: [action or "no action required"]
```

### Step 6 — Log and route

Write the report via `audit-logger`. If gaps exist, raise an alert via `alert-router`.

## Exit criteria

- Report is written to audit log
- Any identified gaps are routed to the appropriate responder

## Do NOT

- Modify, delete, or prune any backup data
- Trigger a restore to a production host
- Suppress a gap finding without human approval
