# ADR-001: Record Architecture Decisions

- **Status:** Accepted
- **Date:** 2026-11-25
- **Deciders:** custos-arcae/infra-ops

## Context

The Hermes agent fleet makes operational decisions that affect live infrastructure.
Without a record of *why* a decision was made, future operators cannot evaluate whether
a decision is still valid, and agents cannot be safely extended.

## Decision

All significant architecture and policy decisions will be recorded as Architecture
Decision Records (ADRs) in `docs/adr/`.

An ADR is required when:
- A new agent is added or an existing agent's scope changes materially
- A new MCP tool is introduced (especially any `governed` or write-capable tool)
- A policy default changes (e.g., `default_policy` in cross-project-isolation)
- A trust boundary is added, removed, or modified
- A dependency with security implications is added to `ca-mcp-server`

An ADR is **not** required for:
- Threshold value tuning within an existing policy
- Typo fixes or documentation improvements
- Test additions that do not change behaviour

## Format

Each ADR is a Markdown file named `NNN-short-title.md` where `NNN` is a zero-padded
sequence number. The format follows the Nygard ADR template:

```
# ADR-NNN: Title
- Status: Proposed | Accepted | Deprecated | Superseded by ADR-XXX
- Date: YYYY-MM-DD
- Deciders: team or role

## Context
## Decision
## Consequences
```

## Consequences

- Every significant change has a traceable rationale
- New contributors can understand the system's design history
- Superseded decisions are preserved (not deleted) for audit purposes
- ADRs are reviewed as part of the PR that implements the decision
