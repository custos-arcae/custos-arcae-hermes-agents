"""
proxmox_status — Query Proxmox VE node, VM, CT, and storage status.

Access: read-only
Env vars required: PROXMOX_API_URL, PROXMOX_API_TOKEN
Security: validates node name against path-traversal pattern and inventory.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ca_mcp_server.inventory import InventoryError, get_inventory
from ca_mcp_server.runtime import pilot_mode

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _api_client() -> httpx.AsyncClient:
    base_url = os.environ.get("PROXMOX_API_URL", "")
    token = os.environ.get("PROXMOX_API_TOKEN", "")
    if not base_url:
        raise InventoryError("PROXMOX_API_URL is not configured.")
    if not token:
        raise InventoryError("PROXMOX_API_TOKEN is not configured.")
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"PVEAPIToken={token}"},
        verify=True,
        timeout=_TIMEOUT,
    )


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    async with _api_client() as client:
        resp = await client.get(f"/api2/json{path}", params=params)
        resp.raise_for_status()
        return resp.json()["data"]


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def proxmox_status(
        node: str | None = None,
        vmid: int | None = None,
    ) -> dict[str, Any]:
        """
        Query Proxmox VE status.

        Args:
            node: Optional node name to filter results (e.g. "pve-01").
            vmid: Optional VM/CT ID (positive integer) to query a single guest.

        Returns:
            Dict with keys: nodes, guests, storage — or a clear error on rejection.
        """
        inv = get_inventory()

        # ── Pilot lockdown: one authorized node only, never the cluster overview ──
        if pilot_mode():
            if node is None:
                return {
                    "error": "pilot mode requires an authorized 'node'; the cluster overview is disabled.",
                    "rejected_before_upstream_call": True,
                }
            # The pilot must authorize exactly one node; a multi-node inventory is a
            # misconfiguration and is rejected before any request (no name/count leak).
            try:
                inv.ensure_single_node()
            except InventoryError as exc:
                return {"error": str(exc), "rejected_before_upstream_call": True}

        # ── A vmid without a node is ambiguous: it would fall through to the cluster
        # overview and be silently ignored. Reject before any HTTP request (all modes).
        if vmid is not None and node is None:
            return {
                "error": f"vmid {vmid} requires a 'node'.",
                "rejected_before_upstream_call": True,
            }

        # ── Validate node name ────────────────────────────────────────────────
        if node is not None:
            try:
                inv.require_path_param("node", node)
            except InventoryError as exc:
                return {"error": str(exc), "rejected_before_upstream_call": True}

        # ── Validate vmid range ───────────────────────────────────────────────
        if vmid is not None and (vmid < 1 or vmid > 999999999):
            return {
                "error": f"vmid {vmid} out of range (1–999999999).",
                "rejected_before_upstream_call": True,
            }

        result: dict[str, Any] = {}
        try:
            if vmid is not None and node is not None:
                result["guest"] = await _get(f"/nodes/{node}/qemu/{vmid}/status/current")
            elif node is not None:
                result["node"] = await _get(f"/nodes/{node}/status")
                result["guests"] = await _get(f"/nodes/{node}/qemu")
                result["storage"] = await _get(f"/nodes/{node}/storage")
            else:
                result["nodes"] = await _get("/nodes")
                result["cluster_resources"] = await _get("/cluster/resources")
        except httpx.HTTPStatusError as exc:
            return {"error": f"Upstream HTTP {exc.response.status_code}: {exc.response.reason_phrase}"}
        except httpx.TimeoutException:
            return {"error": "Proxmox API request timed out after 30 seconds."}
        except httpx.RequestError as exc:
            # exc.args may contain the URL — safe to include since base_url is internal
            return {"error": f"Proxmox API unreachable: {type(exc).__name__}"}
        except InventoryError as exc:
            # Upstream not configured; fail safely without exposing any secret.
            return {"error": str(exc), "rejected_before_upstream_call": True}

        return result
