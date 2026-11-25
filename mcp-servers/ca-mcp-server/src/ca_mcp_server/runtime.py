"""
runtime — process-wide mode flags for ca-mcp-server.

These flags change which tools are registered and how strictly they are scoped.
They are read from the environment so the same wheel serves both the general
integration (all four tools) and the locked single-tool pilot, without a code
difference.
"""

from __future__ import annotations

import os

# Env var that enables the single-tool / single-target pilot lockdown.
PILOT_ENV_VAR = "CA_MCP_PILOT"

_TRUTHY = {"1", "true", "yes", "on"}


def pilot_mode() -> bool:
    """
    True when the server must run in the locked pilot scope.

    Pilot scope:
      * only ``proxmox_status`` is registered / advertised (see ``server``);
      * ``proxmox_status`` requires an authorized ``node`` and can never reach the
        cluster overview (see ``tools.proxmox_status``).

    Reads the environment on every call (cheap) so tests can toggle it without
    re-importing. Off unless ``CA_MCP_PILOT`` is a truthy string.
    """
    return os.environ.get(PILOT_ENV_VAR, "").strip().lower() in _TRUTHY
