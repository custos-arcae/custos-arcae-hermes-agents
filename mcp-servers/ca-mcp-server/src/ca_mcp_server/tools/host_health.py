"""
host_health — Collect SMART, temperature, load, and journal data from managed hosts.

Access: read-only
Security: hostname validated against RFC-1123 pattern and inventory before SSH.
          SSH argument injection blocked (no leading '-'; no shell metacharacters).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ca_mcp_server.inventory import InventoryError, get_inventory

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

_SSH_TIMEOUT = 20  # seconds per command


async def _ssh(host: str, command: str) -> str:
    """Run a read-only command on a managed host over SSH."""
    known_hosts = os.environ.get("SSH_KNOWN_HOSTS", os.path.expanduser("~/.ssh/known_hosts"))
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"ConnectTimeout={_SSH_TIMEOUT}",
        "-o", "BatchMode=yes",
        "--",          # end of ssh options; host is treated as positional arg
        host,
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SSH_TIMEOUT * 3)
    except TimeoutError:
        proc.kill()
        raise RuntimeError(f"SSH to {host} timed out after {_SSH_TIMEOUT * 3}s.") from None
    if proc.returncode != 0:
        raise RuntimeError(f"SSH to {host} failed (rc={proc.returncode}): {stderr.decode().strip()}")
    return stdout.decode().strip()


async def _collect(host: str) -> dict[str, Any]:
    uptime = await _ssh(host, "uptime -p")
    load = await _ssh(host, "cat /proc/loadavg")
    mem = await _ssh(host, "free -h | grep Mem")
    disk = await _ssh(host, "df -h --output=target,pcent,avail | grep -v tmpfs")
    temp = await _ssh(host, "sensors 2>/dev/null | grep -E 'Core|Tctl|temp1' || echo 'no sensor data'")
    smart = await _ssh(host, "smartctl --scan 2>/dev/null | head -5 || echo 'smartctl not available'")
    errors = await _ssh(
        host,
        "journalctl -p err -n 20 --no-pager --since '24 hours ago' 2>/dev/null || echo 'journal unavailable'",
    )
    return {
        "host": host,
        "uptime": uptime,
        "load_avg": load,
        "memory": mem,
        "disk": disk,
        "temperature": temp,
        "smart_scan": smart,
        "journal_errors_24h": errors,
    }


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def host_health(host: str) -> dict[str, Any]:
        """
        Collect health data from a managed host.

        Args:
            host: Hostname or IP of a host in the permitted inventory.

        Returns:
            Dict with: uptime, load_avg, memory, disk, temperature,
            smart_scan, journal_errors_24h — or a rejection error.
        """
        inv = get_inventory()
        try:
            inv.require_host_target(host)
        except InventoryError as exc:
            return {"error": str(exc), "rejected_before_upstream_call": True}

        try:
            return await _collect(host)
        except RuntimeError as exc:
            # exc.args contains hostname (internal) and stderr — no credentials exposed
            return {"error": f"SSH collection failed: {exc}"}
