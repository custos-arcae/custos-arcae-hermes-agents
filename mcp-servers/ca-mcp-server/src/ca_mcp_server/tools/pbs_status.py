"""
pbs_status — Query Proxmox Backup Server datastore and job status.

Access: read-only
Env vars required: PBS_API_URL, PBS_API_TOKEN
Security: validates datastore name against path-traversal pattern and inventory.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ca_mcp_server.inventory import InventoryError, get_inventory

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _api_client() -> httpx.AsyncClient:
    base_url = os.environ.get("PBS_API_URL", "")
    token = os.environ.get("PBS_API_TOKEN", "")
    if not base_url:
        raise InventoryError("PBS_API_URL is not configured.")
    if not token:
        raise InventoryError("PBS_API_TOKEN is not configured.")
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"PBSAPIToken={token}"},
        verify=True,
        timeout=_TIMEOUT,
    )


async def _get(path: str) -> Any:
    async with _api_client() as client:
        resp = await client.get(f"/api2/json{path}")
        resp.raise_for_status()
        return resp.json()["data"]


def register(mcp: MCPServer) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    async def pbs_status(
        datastore: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Query Proxmox Backup Server status.

        Args:
            datastore: Optional datastore name to query a specific datastore.
            job_id:    Optional backup job ID (safe identifier) to query last run.

        Returns:
            Dict with job, datastore, or cluster-wide status — or a clear error on rejection.
        """
        inv = get_inventory()

        # ── Validate datastore name ────────────────────────────────────────────
        if datastore is not None:
            try:
                inv.require_path_param("datastore", datastore)
            except InventoryError as exc:
                return {"error": str(exc), "rejected_before_upstream_call": True}

        # ── Validate job_id ────────────────────────────────────────────────────
        if job_id is not None:
            try:
                inv.require_path_param("job_id", job_id)
            except InventoryError as exc:
                return {"error": str(exc), "rejected_before_upstream_call": True}

        try:
            if job_id is not None:
                return {
                    "job": await _get(f"/config/jobs/{job_id}"),
                    "last_run": await _get(f"/admin/tasks/{job_id}"),
                }
            if datastore is not None:
                return {
                    "datastore": await _get(f"/config/datastore/{datastore}"),
                    "status": await _get(f"/status/datastore/{datastore}"),
                    "verify": await _get(f"/admin/datastore/{datastore}/verify"),
                }
            return {
                "datastores": await _get("/config/datastore"),
                "jobs": await _get("/config/jobs"),
                "status": await _get("/status/datastore"),
            }
        except httpx.HTTPStatusError as exc:
            return {"error": f"Upstream HTTP {exc.response.status_code}: {exc.response.reason_phrase}"}
        except httpx.TimeoutException:
            return {"error": "PBS API request timed out after 30 seconds."}
        except httpx.RequestError as exc:
            return {"error": f"PBS API unreachable: {type(exc).__name__}"}
        except InventoryError as exc:
            # Upstream not configured; fail safely without exposing any secret.
            return {"error": str(exc), "rejected_before_upstream_call": True}
