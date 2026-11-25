# Skill: patch-planning

## Purpose

Produce a complete, reviewable patch plan for a set of hosts — including risk assessment,
rollback procedure, maintenance window, and dependency analysis — before any patch is applied.

## When to invoke

- `patch-orchestrator` receives a patch requirement alert
- A scheduled monthly patch cycle begins
- A security advisory (CVE) affects managed hosts

## Procedure

### Step 1 — Inventory affected hosts

Query `proxmox_status` and `host_health` to collect:
- Hostname, current OS version, running VMs/CTs
- Pending package updates (security vs. non-security)
- Last patch date and outcome

### Step 2 — Classify patches

| Class | Criteria | Default risk |
|---|---|---|
| `security-critical` | CVE with active exploit or CVSS ≥ 9.0 | high |
| `security` | CVE fix, CVSS < 9.0 | medium |
| `stable-update` | Minor version bump, no reboot required | low |
| `major-update` | Major version bump or kernel update | high |

### Step 3 — Dependency and order analysis

- Identify hosts that must be patched in sequence (e.g., cluster quorum nodes)
- Flag any host running a VM with no live-migration target
- Confirm PBS backup exists for every host in the plan (query `pbs_status`)

### Step 4 — Define maintenance window

- Prefer the standard window: Saturday 06:00–10:00 local time
- Security-critical patches may use an emergency window with operator approval
- One host at a time unless the plan explicitly justifies parallel execution

### Step 5 — Write the rollback procedure

For each host, document:
- Exact rollback command (e.g., `apt-get install <pkg>=<previous-version>`)
- Whether a reboot is required to roll back
- Verification step to confirm rollback succeeded

### Step 6 — Produce the plan document

```yaml
plan_id: PATCH-YYYYMMDD-NNN
created: ISO-8601
maintenance_window:
  start: ISO-8601
  end: ISO-8601
risk_assessment: low | medium | high
hosts:
  - name: hostname
    patch_class: security | stable-update | major-update
    current_version: x.y.z
    target_version: x.y.z
    vms_running: [list]
    live_migration_required: true | false
    backup_confirmed: true | false
    rollback_command: "..."
    requires_reboot: true | false
    post_patch_verification: "host_health check"
approval_required: true
```

### Step 7 — Submit for approval

Route the plan to `human-operator` via `alert-router`. Do not proceed until explicit
approval is recorded in the audit log.

## Exit criteria

- Plan document is written and submitted for approval
- Plan is logged via `audit-logger`
- No patches are applied until approval is received

## Do NOT

- Apply any patch before the plan is approved
- Schedule a patch window that conflicts with a backup window
- Skip rollback procedure documentation for any host
- Patch a host without a confirmed recent backup
