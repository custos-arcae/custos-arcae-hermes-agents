# ca-mcp-server

Model Context Protocol server for the `custos-arcae` infrastructure estate.

Exposes read-only and governed-write tools to Hermes agents over MCP (stdio transport).

## Tools

| Tool | Module | Access | Description |
|---|---|---|---|
| `proxmox_status` | `tools/proxmox_status.py` | read-only | Proxmox VE node, VM, CT, and storage status |
| `pbs_status` | `tools/pbs_status.py` | read-only | PBS datastore health and backup job status |
| `host_health` | `tools/host_health.py` | read-only | SMART, temperature, load, and journal data |
| `connectivity_status` | `tools/connectivity_status.py` | read-only | Network reachability between managed hosts |

## Setup

```bash
cd mcp-servers/ca-mcp-server
uv pip install -e ".[dev]"
```

## Running

```bash
# Stdio transport (default — used by MCP clients)
ca-mcp-server

# Development mode with verbose logging
ca-mcp-server --dev
```

## Configuration

The server reads connection details from environment variables (never from files in this repo):

| Variable | Description |
|---|---|
| `PROXMOX_API_URL` | Base URL of the Proxmox VE API |
| `PROXMOX_API_TOKEN` | API token in `user@realm!tokenid=UUID` format |
| `PBS_API_URL` | Base URL of the PBS API |
| `PBS_API_TOKEN` | PBS API token |
| `SSH_KNOWN_HOSTS` | Path to the known-hosts file for host connections |

## Testing

```bash
uv run pytest tests -v
uv run ruff check src tests
uv run mypy src
```

## Security

- All tools are **read-only** unless explicitly marked otherwise in `policies/tool-access.yaml`
- Credentials are sourced from environment variables only — never from config files in Git
- Every tool invocation is logged by `audit-logger` before execution
- Cross-project API calls are blocked by `policies/cross-project-isolation.yaml`
