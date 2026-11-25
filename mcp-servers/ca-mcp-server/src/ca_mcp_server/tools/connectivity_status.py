"""
connectivity_status — Check network reachability between managed hosts.

Access: read-only
Security: each hostname validated against RFC-1123 pattern and inventory.
          List size capped at _MAX_HOSTS to prevent resource exhaustion.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ca_mcp_server.inventory import InventoryError, get_inventory

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

_MAX_HOSTS = 20  # maximum concurrent ping targets per call


async def _ping(host: str, count: int = 3, timeout: int = 5) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        "ping", "-c", str(count), "-W", str(timeout), "--", host,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout * count + 5)
    except TimeoutError:
        proc.kill()
        return {"host": host, "reachable": False, "rtt_ms": None, "raw": "ping timed out"}

    reachable = proc.returncode == 0
    output = stdout.decode().strip()
    rtt: str | None = None
    if reachable:
        for line in output.splitlines():
            if "min/avg/max" in line:
                rtt = line.split("=")[-1].strip()
                break
    return {"host": host, "reachable": reachable, "rtt_ms": rtt, "raw": output}


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def connectivity_status(
        hosts: list[str],
        count: int = 3,
    ) -> dict[str, Any]:
        """
        Check ICMP reachability for a list of managed hosts.

        Args:
            hosts: List of hostnames or IPs to probe (max 20, must be in inventory).
            count: Number of ICMP packets per host (default 3, max 5).

        Returns:
            Dict with results per host and a summary — or a rejection error.
        """
        inv = get_inventory()

        # Cap packet count
        count = min(count, 5)

        # Validate the full list before making any calls
        try:
            inv.require_host_list(hosts, max_size=_MAX_HOSTS)
        except InventoryError as exc:
            return {"error": str(exc), "rejected_before_upstream_call": True}

        results = await asyncio.gather(*[_ping(h, count) for h in hosts])
        reachable = sum(1 for r in results if r["reachable"])
        return {
            "results": list(results),
            "summary": {
                "total": len(hosts),
                "reachable": reachable,
                "unreachable": len(hosts) - reachable,
            },
        }
