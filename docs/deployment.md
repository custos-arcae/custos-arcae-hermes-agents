# Deployment — custos-arcae-hermes-agents

## Prerequisites

- Python **3.12+**
- [uv](https://github.com/astral-sh/uv) for environment and dependency management
- Network access to Proxmox VE API, PBS API, and managed hosts (SSH)
- Environment variables configured (see below)

## Environment variables

All secrets and connection details are sourced from environment variables.
Never commit real values to Git. Use a vault or secrets manager in production.

| Variable | Required by | Description |
|---|---|---|
| `PROXMOX_API_URL` | ca-mcp-server | Base URL of Proxmox VE API |
| `PROXMOX_API_TOKEN` | ca-mcp-server | API token (`user@realm!tokenid=UUID`) |
| `PBS_API_URL` | ca-mcp-server | Base URL of PBS API |
| `PBS_API_TOKEN` | ca-mcp-server | PBS API token |
| `SSH_KNOWN_HOSTS` | ca-mcp-server | Path to known_hosts file for managed hosts |
| `HERMES_INVENTORY_FILE` | ca-mcp-server | Path to a populated inventory YAML (shape of `profiles/infra-ops/config.example.yaml`); the **authoritative** source for the hosts / nodes / datastores allowlists. If set and unusable, the server is in load-error state and denies every target |
| `HERMES_HOSTS_ALLOWED` | ca-mcp-server | Comma-separated host allowlist. Applies to the **hosts** category **only**, and **only when `HERMES_INVENTORY_FILE` is unset**; never widens a configured inventory |
| `HERMES_AUDIT_PATH` | audit-logger | Path to the append-only audit log |
| `HERMES_OPERATOR_NAME` | alert-router | Operator identity for escalation |
| `HERMES_OPERATOR_CHANNEL` | alert-router | Contact channel for escalation |
| `HERMES_DEBUG` | all agents | Set to `1` for verbose logging |

## Target scoping (cross-project isolation)

`ca-mcp-server` enforces `policies/cross-project-isolation.yaml` at runtime in
`src/ca_mcp_server/inventory.py`, **before** any upstream request. Authorization is
**deny-by-default**, evaluated **independently per category** (hosts, Proxmox nodes,
PBS datastores):

- **Empty category ⇒ zero authorized targets.** An absent, empty, or unloaded
  allowlist never falls back to "allow anything". There is no format-only mode.
- **Format guards always apply**, independent of any allowlist: Proxmox `node` and
  PBS `datastore` are checked against a safe path-parameter pattern (blocks `..`,
  `/`, `;`, whitespace, leading `-`); `host_health` / `connectivity_status` targets
  are checked against an RFC-1123 hostname/IP pattern and passed after a `--`
  option terminator, so a value can never be parsed as an SSH/ping flag. `job_id`
  is format-checked only (opaque ids, not enumerated in the inventory).
- **A missing, unreadable, malformed, or structurally invalid inventory is a load
  error**: the server still starts and lists tools, but every targeted call is
  rejected with a safe, actionable diagnostic that never echoes the ACL contents.
- **Inventory scope is per server process.** `get_inventory()` is a lazily-loaded
  singleton, fixed for the lifetime of one `ca-mcp-server` stdio process. It carries
  **no caller identity** — the MCP transport is identity-less, so per-agent
  `policy.yaml` / `permitted_agents` files do **not** select a target set here. Every
  connected agent shares the one process inventory; finer per-agent scoping must be
  enforced by the Hermes agent runtime, not inside the server.

### `HERMES_HOSTS_ALLOWED` — when it may contribute targets

- It is a convenience source for the **hosts** category **only**, and **only** when
  **no** `HERMES_INVENTORY_FILE` is configured. Nodes and datastores then have no
  source at all and are denied.
- When `HERMES_INVENTORY_FILE` is set, `HERMES_HOSTS_ALLOWED` is **ignored** — it can
  never silently widen an explicitly configured inventory.
- With neither set, the server is in load-error state and denies every target.

## Installing ca-mcp-server

The SDK version used in testing is **pinned** in `mcp-servers/ca-mcp-server/uv.lock`
(`mcp==2.3.0`); `pyproject.toml` bounds it to `mcp>=2.0,<3.0`. Reproduce the exact
tested environment with a frozen sync (this is what CI runs):

```bash
cd mcp-servers/ca-mcp-server
uv sync --frozen --extra dev   # creates .venv (gitignored) from uv.lock; adds respx/pytest/mypy/ruff
```

> Requires the **MCP v2** `MCPServer` API (`from mcp.server.mcpserver import
> MCPServer`, mcp 2.3.0). The server does **not** run on the pre-rename `mcp` 1.x
> `FastMCP` import path.

## Running ca-mcp-server

The server uses **stdio transport** (MCP standard). It is started by the agent runtime,
not directly by an operator.

```bash
# Manual start for testing
ca-mcp-server

# Verbose logging
ca-mcp-server --dev
```

## Deploying agent configuration

The agent runtime reads configuration directly from this repository at startup:

1. `profiles/infra-ops/SOUL.md` — base identity context
2. `agents/<name>/system-prompt.md` — per-agent instructions
3. `agents/<name>/policy.yaml` — per-agent autonomy boundaries
4. `policies/*.yaml` — global governance rules
5. `skills/*/SKILL.md` — shared procedures

No build step is required for configuration changes. A runtime restart picks up the
latest committed state of `main`.

## Rollback

Configuration rollback is a Git operation:

```bash
git revert <commit-sha>
git push origin main
# Restart the agent runtime to pick up the reverted config
```

## Health check

After deployment, verify:

```bash
# Policy/schema conformance (root venv, no server deps needed) — 19 tests
pytest tests/unit -v

# MCP server: registration, deny-by-default target scoping, pilot-mode lockdown
# (incl. the single-node invariant), and end-to-end stdio round-trip against a local
# HTTP fixture (no live services or credentials) — 56 tests
cd mcp-servers/ca-mcp-server
uv run pytest -v
```

> Note: a bare `echo '{"method":"tools/list"}' | ca-mcp-server` will **not** return
> tool data — the MCP stdio protocol requires a prior `initialize` handshake and
> newline-delimited JSON-RPC framing. Use the `tests/test_stdio.py` client, or the
> MCP Inspector, to exercise a running server.

## Read-only Hermes MCP lab pilot procedure

**Scope of the first live pilot: exactly ONE scoped lookup** — a single Proxmox VE
*or* PBS API status call against one allowlisted target, with a read-only token.
`host_health` and `connectivity_status` (SSH + ICMP) are **out of the first pilot**:
they widen the credential surface (SSH keys, `known_hosts`, egress to arbitrary
hosts) and are covered by the local fixture tests only. The six `agents/*` directories
are **role definitions** — they are not wired into any runtime here and grant no
authorization.

1. Create an isolated lab env file (never committed) exporting the upstream vars for
   the one chosen endpoint plus an inventory file authorizing a single node:
   ```bash
   export HERMES_INVENTORY_FILE=/secure/path/lab-inventory.yaml  # one node only
   export PROXMOX_API_URL=https://pve-lab.custos-arcae.internal:8006
   export PROXMOX_API_TOKEN=<read-only-lab-token>   # user@realm!id=... (read only)
   # export PBS_API_URL=... ; export PBS_API_TOKEN=...   # only if PBS is the chosen target
   ```
   `lab-inventory.yaml` must list the single authorized node, e.g.
   `proxmox:\n  nodes:\n    - name: pve-01`.
2. **Denied path first (proves the boundary):** call `proxmox_status` with a node
   *not* in the inventory. Confirm the result is a **structured tool result**
   (`isError == false`) carrying `rejected_before_upstream_call: true`, that the
   error names only the requested node and **never lists the allowlist**, and that
   **zero** upstream requests were issued (check Proxmox access logs).
3. **Authorized path:** call `proxmox_status` for the one allowlisted node. Confirm
   TLS verification is on (`verify=True`), the 30s timeout is honored, and no token
   appears in any output or error text.
4. Restart with the Hermes runtime and confirm only the four tools are advertised and
   all carry `readOnlyHint` (discovery is unaffected by the empty/absent inventory —
   authorization is enforced at call time).
5. Record results in the audit log; capture which checks passed vs were blocked.

### Denial contract (as seen by the client)

A rejected call is **not** a JSON-RPC/protocol error. The tool returns a normal
`CallToolResult` with `isError == false` whose JSON body contains
`{"error": "...", "rejected_before_upstream_call": true}`. This lets the model read
and explain the denial instead of the whole tool call failing at the transport layer.
It is asserted end-to-end in `tests/test_stdio.py` (`test_stdio_authorized_call_and_denied_call`).

### Read-only boundary — three distinct layers

- **MCP metadata (`readOnlyHint` / `destructiveHint`):** an annotation only; clients
  may use it for UI/risk hints. It does not make a call safe.
- **Actual behaviour:** every tool issues only HTTP `GET`s (proxmox/pbs) or
  read-only subprocesses (`ping -c`, `ssh` running `uptime`/`cat`/`free`/`df`/
  `sensors`/`smartctl --scan`/`journalctl -p err`). No mutating verb or command.
- **Upstream credential permissions:** the real ceiling. `PROXMOX_API_TOKEN` /
  `PBS_API_TOKEN` **must be issued read-only** on the Proxmox/PBS side; the server
  cannot prevent a write-scoped token from being misused, so token scoping is an
  operator control, not a code control.

### Monitoring reuse (why the first pilot skips host_health)

For hypervisors already managed by Proxmox, `/nodes/{node}/status` and
`/cluster/resources` already return uptime, loadavg, cpu, and memory, so
`host_health`'s uptime/load/memory/disk for those hosts can be served by
`proxmox_status` over the existing read-only token — no SSH. `host_health` adds
SMART, temperature, and journal data that Proxmox does **not** expose, and
`connectivity_status` adds ICMP reachability that Proxmox does not perform; both
need SSH/ICMP credentials and are therefore deferred past the first pilot.

## Pilot mode (single-tool, single-node lockdown)

The first pilot is narrower than the full integration. It is switched on with the
environment variable **`CA_MCP_PILOT`** (truthy = `1`/`true`/`yes`/`on`), read in
`src/ca_mcp_server/runtime.py`. Same wheel, two behaviours:

| | General integration (unset) | Pilot (`CA_MCP_PILOT=1`) |
|---|---|---|
| Tools registered | `proxmox_status`, `pbs_status`, `host_health`, `connectivity_status` | **`proxmox_status` only** |
| `proxmox_status` with `node` omitted | cluster overview (`/nodes`, `/cluster/resources`) | **rejected** before any HTTP — cluster overview unreachable |
| `proxmox_status` with `vmid` but no `node` | (also rejected — ambiguity fix applies in **all** modes) | rejected before any HTTP |
| Inventory node count | any (deny-by-default per node) | **must be exactly one** — a 0- or multi-node inventory is a misconfiguration and every targeted call is rejected before any HTTP |

- Registration gate: `server._register_tools()` returns after `proxmox_status` when
  `pilot_mode()` is true; the other three `register()` calls are skipped.
- Call gate: `proxmox_status` rejects `node is None` (pilot) and `vmid` without
  `node` (all modes) **before** the `httpx` client is constructed.
- Single-node gate: in pilot mode `proxmox_status` calls
  `inventory.ensure_single_node()`, which raises unless the `proxmox.nodes`
  allowlist contains **exactly one** entry. The rejection message never echoes
  node names or the configured count (it only points the operator at
  `HERMES_INVENTORY_FILE`). This means a second configured node is **not** accepted
  in pilot mode; outside pilot mode a multi-node inventory is unaffected.
- The **denial contract is unchanged**: a rejection is a normal `CallToolResult`
  with `isError == false` whose JSON body is
  `{"error": "...", "rejected_before_upstream_call": true}`; it names only the
  requested value and never lists the inventory. Proven end-to-end by
  `tests/test_stdio.py::test_stdio_pilot_mode_scope` and
  `tests/test_stdio.py::test_stdio_pilot_single_node_invariant` (authorized call
  reaches the fixture; omitted node, unauthorized node, vmid-without-node, and a
  two-node inventory each make **zero** upstream requests) and
  `tests/test_tools.py` (pilot registration = 1 tool; general registration = 4 tools;
  `ensure_single_node` accepts exactly one and rejects zero/many without leaking).

## Packaging infra-ops as a Hermes distribution

Three minimal, secret-free distribution files are checked in under
`profiles/infra-ops/`:

- `distribution.yaml` — Hermes manifest (schema verified against the installed
  `hermes_cli/profile_distribution.py`). Declares `env_requires` (the operator's
  `.env` values) and `distribution_owned` paths (`SOUL.md`, `config.yaml`,
  `skills`, `distribution.yaml`). Contains **no** secrets, hostnames, or machine paths.
- `distribution.config.yaml` — the **packaged MCP registration**. Staged as
  `config.yaml` in the distribution directory (see the assembly command below);
  tracked under a non-ignored name because `profiles/*/config.yaml` is gitignored to
  keep real hostnames/IPs out of Git. It contains only `mcp_servers.ca-infra`
  (`command: ca-mcp-server`, `env.CA_MCP_PILOT=1`, `enabled: true`) — no credentials.

> **Why `config.yaml` and not `mcp.json`:** Hermes v0.19.0 loads MCP servers **only**
> from `config.yaml`'s `mcp_servers` map at session start
> (`tools/mcp_tool.py::_load_mcp_config`). A standalone `mcp.json` is listed as
> distribution-owned and is *copied* into the profile, but **no runtime path reads
> it** — shipping one is a packaging gap, so the pilot registration lives in
> `config.yaml` and the inert `mcp.json` was removed.

`SOUL.md` (profile identity) and the reviewed `skills/` are **reused from the repo**
(not duplicated in `profiles/infra-ops/`), so a small **assembly command** stages the
distribution. Reproducible install into a **fresh isolated home**:

```bash
# 0. One-time: make the `ca-mcp-server` console script available on PATH
uv tool install --python 3.12 ./mcp-servers/ca-mcp-server   # -> ~/.local/bin/ca-mcp-server

# 1. Stage the distribution: reuse SOUL.md + skills, ship the manifest + config.yaml
STAGE=$(mktemp -d)/infra-ops-dist
mkdir -p "$STAGE"
cp profiles/infra-ops/SOUL.md profiles/infra-ops/distribution.yaml "$STAGE/"
cp profiles/infra-ops/distribution.config.yaml "$STAGE/config.yaml"   # staged as config.yaml
cp -r skills "$STAGE/skills"

# 2. Install into a throwaway HERMES_HOME (export it so the child process inherits it)
export HERMES_HOME=/tmp/hermes-accept           # NEVER the operator's real ~/.hermes
rm -rf "$HERMES_HOME" && mkdir -p "$HERMES_HOME"
printf 'y\n' | hermes-agent --accept-hooks profile install "$STAGE" --name infra-ops

# 3. Verify identity, skills, and the packaged MCP config landed
hermes-agent profile info infra-ops
find "$HERMES_HOME/profiles/infra-ops" -maxdepth 1   # SOUL.md, config.yaml, distribution.yaml, .env.EXAMPLE, skills/

# 4. Verify the PACKAGED session path (no manual `mcp add`) and pilot discovery = 1 tool
hermes-agent -p infra-ops mcp list            # ca-infra ... ca-mcp-server ... enabled (from config.yaml)
hermes-agent -p infra-ops mcp test ca-infra   # ✓ Connected · Tools discovered: 1 (proxmox_status)
```

Verified results (Hermes v0.19.0, isolated home `/tmp/hermes-accept`): the install
landed `SOUL.md`, `config.yaml`, `distribution.yaml`, generated `.env.EXAMPLE`, and
all four `skills/`; **re-running with `--force` is repeatable** and user data
(memories/sessions/.env) is preserved. `hermes -p infra-ops mcp list` shows
`ca-infra` **enabled straight from the packaged `config.yaml` — no `hermes mcp add`
was run**, and `mcp test ca-infra` spawns the server over stdio and discovers
**exactly 1 tool** (`proxmox_status`), confirming `CA_MCP_PILOT=1` took effect from
the distribution. This closes the packaged-session gate; the remaining session step
is a fixture/live tool *call*, which needs a provider (model) or an approved endpoint
(lab).

> **Do not** put an absolute `command:` path in `config.yaml` — it is machine-specific
> and would break portability. Use the `ca-mcp-server` script name and install the
> server so it is on `PATH`.

## Hermes runtime profile — verified status

Hermes Agent **v0.19.0 (2026.7.20)** is installed on this machine
(as `hermes-agent` on `PATH`; snap build with its own Python 3.12). Verification was
performed in an **isolated home** (`HERMES_HOME=/tmp/hermes-lab`) so the operator's
real `~/.hermes` was untouched. Confirmed against this repository's server:

- `hermes-agent mcp add ca-infra --command <venv>/bin/python --args -m ca_mcp_server.server`
  connected over stdio and discovered **exactly the four tools** (proxmox_status,
  pbs_status, host_health, connectivity_status); `hermes mcp test ca-infra` reported
  `✓ Connected`, `Auth: none`, `Tools discovered: 4`; `hermes mcp list` shows the
  server `enabled` (4/4 tools). Discovery succeeds even with no inventory —
  confirming authorization is enforced at call time, not at registration.
- Tool registration/discovery works **without** any upstream credentials (the
  server exposes the tool surface; a real call needs the env vars set). This is why
  the fixture and stdio tests need no live services.
- Hermes has **secret redaction ON by default** — token-shaped strings are masked
  in tool output/logs before the model sees them. This complements (does not
  replace) the credential-safe errors implemented in the tools.
- Per-server env for the stdio child is supplied via `hermes mcp add ... --env
  KEY=VALUE`; secrets live in `$HERMES_HOME/.env` and are resolved without being
  printed. The `${VAR}` placeholders in `profiles/infra-ops/config.example.yaml`
  are **operator-facing** — `ca-mcp-server` reads real process env vars, it does
  **not** parse `config.example.yaml`.

### Profile install — verified workflow (Hermes v0.19.0)

`hermes profile install <source>` requires a **`distribution.yaml`** manifest at the
source root (schema confirmed against the installed
`hermes_cli/profile_distribution.py`: keys `name` (required), `version`,
`description`, `hermes_requires`, `author`, `license`, `env_requires[]`
(`{name, description, required, default}`), `distribution_owned[]`; a `.env.template`
is renamed `.env.EXAMPLE` on install; symlinks are rejected; a distribution cannot be
installed as `default`). This was exercised end-to-end in an isolated home
(`HERMES_HOME=/tmp/hermes-lab`) with a source tree containing
`SOUL.md`, `skills/incident-triage/SKILL.md`, `config.yaml`, `distribution.yaml`:

```bash
export HERMES_HOME=/tmp/hermes-lab         # never the operator's real ~/.hermes
hermes-agent --accept-hooks profile install /path/to/dist --name infra-ops
hermes-agent profile info infra-ops        # shows manifest, version, source, env_requires
hermes-agent profile list                  # infra-ops@0.1.0 (distinct from default)
# repeatable: re-run with --force to refresh distribution-owned paths,
# user data (memories/sessions/.env) preserved
```

This succeeded and was re-run with `--force` (repeatable). The `infra-ops`
`distribution.yaml` and `distribution.config.yaml` (staged as `config.yaml`) are now
checked in under `profiles/infra-ops/` (see **Packaging infra-ops as a Hermes
distribution** above); `SOUL.md` and `skills/` are reused from the repo via the
assembly command. Pushing to the hosted repo and any further governance sign-off on
what the distribution ships remain owner actions.

### Acceptance steps — fixture-backed model session (no live infra)

Runs entirely against a local HTTP fixture; needs only an approved provider.
The packaged-session half is **already verified** (install → `mcp list` shows
`ca-infra` from the shipped `config.yaml`, `mcp test ca-infra` → 1 tool), including
with a **checkout-local** executable (`.venv/bin/ca-mcp-server` substituted into the
*installed* profile's `config.yaml`; the repo copy stays portable).

Remaining steps to close this gate:

1. `export HERMES_HOME=/tmp/hermes-accept`; install the distribution (the packaged
   `config.yaml` registers `ca-infra` with `CA_MCP_PILOT=1` automatically — no
   `hermes mcp add`). Confirm `mcp test ca-infra` reports **1 tool**.
2. Start a local Proxmox fixture on loopback and point the profile's `.env` at it:
   `PROXMOX_API_URL=http://127.0.0.1:<port>`, a synthetic `PROXMOX_API_TOKEN`, and
   `HERMES_INVENTORY_FILE` set to a **one-node** inventory.
3. Run a single-shot session and record the three things separately — tool discovery,
   the actual `proxmox_status` invocation, and the fixture request path:
   ```bash
   hermes-agent -p infra-ops chat -Q -v \
     -q "Call proxmox_status for node <authorized-node> and report uptime, cpu, memory."
   ```
   Confirm the model sees **only** `proxmox_status` and that the reported fields match
   the fixture payload (the MCP server logs the upstream path; Hermes writes MCP
   stderr to `$HERMES_HOME/logs/mcp-stderr.log`).
4. Keep **denied-call** verification at the deterministic stdio boundary
   (`tests/test_stdio.py`). A model *declining* to call a tool is **not** evidence of
   MCP authorization — only the zero-upstream-call assertions are. Do not treat a
   model refusal as passing the authorization gate.

**Status: PENDING — no approved provider.** Verified absence, checked without
exposing any credential: no provider env var is set (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `MISTRAL_API_KEY`,
`AZURE_OPENAI_API_KEY`, `LITELLM_API_KEY`, `HERMES_PROVIDER` all unset) and the
operator secret store is empty — `~/.hermes/config.yaml` and `~/.hermes/.env` do not
exist, so no `providers:` entry or key is configured anywhere. **Blocker:** the owner
must provision an approved provider (env var or `providers:` in Hermes config). No
model session was attempted, so no inference traffic left this machine.

### Acceptance steps — one-node live lab (real endpoint)

Run **only** when **all four** prerequisites are met: (a) a **passing** fixture-backed
model session, (b) an owner-approved **non-production** endpoint, (c) a scoped
**read-only** token for exactly one node, and (d) owner approval for the model
provider to receive the lab fields the tool returns (uptime/CPU/memory leave the
estate boundary). Steps:

1. Provision a **read-only** Proxmox token and issue it for exactly one lab node.
2. Set `HERMES_INVENTORY_FILE` to a file authorizing **only** that node; `CA_MCP_PILOT=1`.
3. **Denied first:** request a node not in the inventory → expect
   `rejected_before_upstream_call`, and confirm **zero** hits in the Proxmox access log.
4. **Authorized:** request the one node → confirm `verify=True`, 30s timeout honored,
   no token in output. Confirm cluster overview is unreachable (omit `node` → rejected).
5. **Compare with source:** independently call the same endpoint the tool used
   (`GET /api2/json/nodes/<node>/status` with the same read-only token, e.g. via
   `curl` outside Hermes) and confirm the tool's returned fields match the raw API
   response — proving the tool reports the fixture/live result accurately and does
   not fabricate or reshape values.
6. Record pass/blocked results in the audit log.

**Status: NOT RUN — prerequisites unmet.** Blocked first on (a): the fixture-backed
model session is itself PENDING for lack of an approved provider. No live endpoint was
contacted and no token exists. Blockers: approved provider, approved non-production
endpoint, scoped read-only token, and provider egress approval.

### Other pending items

- **Hosted CI: has never run.** The repo has no commits (`HEAD` unborn) and the
  configured remote `custos-arcae/custos-arcae-hermes-agents` returns *Repository not
  found*. `.github/workflows/ci.yml` is reproduced **locally** (see below) but is not
  yet exercised on GitHub. Blockers: owner must create the repo, set branch
  protection / 2-reviewer rules + teams, and push the initial commit.
- Per-agent `permitted_agents` enforcement — the MCP transport is identity-less, so
  the server exposes its registered tools to any connected agent; per-agent scoping is
  a Hermes/policy concern, not enforced inside `ca-mcp-server` (see enforcement map).

## Initial commit handoff — reviewed contents

Local state at handoff: branch `main`, **zero commits** (`HEAD` unborn), everything
untracked. `git add --dry-run --all .` (which respects `.gitignore`) yields **67
candidate files**. Reviewed and confirmed **included**: `.github/workflows/ci.yml`,
`.github/CODEOWNERS`, `.gitignore`, root `pyproject.toml` + `uv.lock`,
`mcp-servers/ca-mcp-server/{pyproject.toml,uv.lock,src,tests}`, `profiles/infra-ops/`
(`SOUL.md`, `distribution.yaml`, `distribution.config.yaml`, `config.example.yaml`),
`skills/`, `policies/`, `schemas/`, `agents/`, `tests/unit/`, `docs/`.

Scan of those 67 candidates:

| Criterion | Result |
|---|---|
| Credentials / key material | **Clean** — no token/password/private-key literals; only `${VAR}` placeholders and doc placeholders (`user@realm!tokenid=secret`, `=UUID`) |
| Generated state | **Clean** — no `.venv/`, `__pycache__/`, `*.pyc`, `*.egg-info`, `sessions/`, `memories/`, `state.db`, `auth.json`, `.env` |
| Real inventory | **Clean** — no node lists, IPs, or hostnames beyond the examples below; `cross-project-isolation.yaml` points `managed-hosts.inventory` at the template and notes real inventory is env-sourced |
| Machine-specific paths | **Clean** after removing the `/snap/...` reference from this doc; `distribution.config.yaml` uses the `ca-mcp-server` script name, not an absolute path |

> **Owner visibility caveat (not auto-fixed).** Three tracked files name the estate's
> internal DNS suffix as concrete endpoints —
> `policies/cross-project-isolation.yaml` (`https://pve.custos-arcae.internal:8006`,
> `https://pbs.custos-arcae.internal:8007`) and
> `profiles/infra-ops/config.example.yaml` / this doc (same suffix in comments). These
> are not secrets, but they disclose internal topology. They were **left as-is** because
> `policies/cross-project-isolation.yaml` is a governance file: per `SCOPE.md`
> change-control it needs security review, and `CODEOWNERS` assigns it to
> `@custos-arcae/security`. **Recommendation:** create the repository **private**. If it
> must ever be public, replace those endpoints with placeholders in a reviewed PR.

**No** remote creation, commit, push, or repository-rule change was performed — those
require explicit owner authorization.

## Reviewer-count enforcement — reality check

`SCOPE.md` and `AGENTS.md` describe a **two-reviewer rule** for autonomy-expanding
changes, and `.github/CODEOWNERS` lists `@custos-arcae/infra-ops` /
`@custos-arcae/security` on the relevant paths. **CODEOWNERS alone does not enforce
a reviewer count.** Enforcement additionally requires, on the GitHub repository:

- a branch-protection rule or ruleset on `main` with *require approvals* set to 2,
  and *require review from code owners* enabled;
- the `@custos-arcae/infra-ops` and `@custos-arcae/security` teams to exist.

At the time of this verification the remote repository
`custos-arcae/custos-arcae-hermes-agents` **does not exist yet** (`git ls-remote`
reports *Repository not found*; the GitHub API returns 404, and the org lists only
`custos-arcae-control` and `custos-arcae-datacenter`), so **no reviewer rule is
currently enforced**. Configuring these rules — plus creating the repo and pushing
the initial commit — is an **owner action**, intentionally left undone here.

**Reviewer availability is separate from rule configuration.** `gh api
orgs/custos-arcae/teams` returns **no teams at all**, so both `@custos-arcae/infra-ops`
and `@custos-arcae/security` referenced by `CODEOWNERS` are currently **unresolvable**.
Even after a branch-protection rule is added, owner review cannot be satisfied until
those teams exist and have members. Create the teams before relying on the
two-reviewer gate.

## Policy enforcement map

Which governance artefacts are actually enforced, and where:

| Policy / artefact | Enforcement point | Kind |
|---|---|---|
| `policies/cross-project-isolation.yaml` | `src/ca_mcp_server/inventory.py` (rejects out-of-scope/`..`/injection targets **before** any upstream call) | **Runtime-enforced** by MCP server |
| Read-only guarantee (no write tools) | `server.py` registers only the four read-only tools; each carries `readOnlyHint`; `tool-access.yaml` keeps write tools commented out | **Runtime-enforced** by MCP server (by absence) |
| Upstream auth / scoping of tokens | Per-server env via `hermes mcp add --env`; secrets resolved from `$HERMES_HOME/.env`; Hermes redaction on | **Runtime-enforced** by Hermes |
| `policies/tool-access.yaml` ↔ `agents/*/policy.yaml` consistency | `tests/unit/test_policy_conformance.py` (schema + tool-access coverage) | **Validation-only** (CI) |
| Agent `policy.yaml`, `schemas/*.json` | `jsonschema` validation in `tests/unit`; `main.py` structure check | **Validation-only** (CI) |
| `policies/approval-gates.yaml`, `autonomous-actions.yaml`, `escalation-matrix.yaml` | Consumed by the Hermes agent runtime / human process; **not** read by `ca-mcp-server` | **Documentation / Hermes-side** |
| `requires_audit_log: true` | `audit-logger` agent + server `instructions`; no logging middleware in `ca-mcp-server` | **Documentation / Hermes-side** |
| `permitted_agents` (which agent may call which tool) | Not enforced inside `ca-mcp-server` (transport is identity-less); must be enforced by the Hermes agent set | **Documentation / Hermes-side** |
