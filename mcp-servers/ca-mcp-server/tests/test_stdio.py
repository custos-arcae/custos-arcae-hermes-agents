"""
End-to-end stdio test for ca-mcp-server.

Starts the server through its real entry point (`python -m ca_mcp_server.server`)
over a stdio transport, connects with a genuine MCP ClientSession, and drives it
against a **local HTTP fixture** (127.0.0.1 only — no live services, no network
egress, credentials are fake). It verifies:

  * tool discovery returns exactly the four read-only tools (no write tools)
  * an authorized node produces a successful structured result
  * an out-of-scope node is DENIED before any upstream request is made
    (the fixture records zero additional calls for the denied request)
  * the DENIAL contract as seen by the client: it is a *structured tool result*
    (isError == False) carrying `rejected_before_upstream_call`, not a protocol
    error, and never leaks the access-control list.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC = str(Path(__file__).resolve().parents[1] / "src")
EXPECTED = {"proxmox_status", "pbs_status", "host_health", "connectivity_status"}
_STRIPPED = {
    "PROXMOX_API_URL", "PROXMOX_API_TOKEN", "PBS_API_URL", "PBS_API_TOKEN",
    "HERMES_HOSTS_ALLOWED", "HERMES_INVENTORY_FILE", "CA_MCP_PILOT",
}


# ── Local upstream fixture ────────────────────────────────────────────────────


class _FixtureHandler(BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        type(self).seen.append(self.path)
        body = json.dumps({"data": {"node": "pve-01", "status": "online"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: Any) -> None:  # silence stderr noise
        return


def _start_fixture() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    return srv, f"http://127.0.0.1:{port}"


# ── Harness ───────────────────────────────────────────────────────────────────


def _text(res: Any) -> str:
    return res.content[0].text if getattr(res, "content", None) else ""


def _server_params(extra_env: dict[str, str] | None = None) -> StdioServerParameters:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED}
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env or {})
    return StdioServerParameters(command=sys.executable, args=["-m", "ca_mcp_server.server"], env=env)


async def _run(actions, extra_env: dict[str, str] | None = None) -> None:
    params = _server_params(extra_env)
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        await actions(session)


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_stdio_tool_discovery() -> None:
    captured: dict[str, Any] = {}

    async def actions(session: ClientSession) -> None:
        listed = await session.list_tools()
        captured["names"] = {t.name for t in listed.tools}
        captured["annotations"] = {t.name: t.annotations for t in listed.tools}

    await _run(actions)
    assert captured["names"] == EXPECTED
    for name in EXPECTED:
        ann = captured["annotations"][name]
        assert ann is not None and ann.read_only_hint is True


async def test_stdio_authorized_call_and_denied_call(tmp_path: Path) -> None:
    srv, base_url = _start_fixture()
    try:
        inv = tmp_path / "inv.yaml"
        inv.write_text("proxmox:\n  nodes:\n    - name: pve-01\n")
        env = {
            "PROXMOX_API_URL": base_url,
            "PROXMOX_API_TOKEN": "user@pve!read=FAKE-NOT-A-REAL-SECRET",
            "HERMES_INVENTORY_FILE": str(inv),
        }
        result: dict[str, Any] = {}

        async def actions(session: ClientSession) -> None:
            _FixtureHandler.seen = []
            # authorized target → real upstream call, structured success
            ok = await session.call_tool("proxmox_status", {"node": "pve-01"})
            result["ok_error"] = ok.is_error
            result["ok_text"] = _text(ok)
            result["after_ok"] = list(_FixtureHandler.seen)

            # out-of-scope target → denied, ZERO new upstream calls
            _FixtureHandler.seen = []
            bad = await session.call_tool("proxmox_status", {"node": "pve-99"})
            result["bad_error"] = bad.is_error
            result["bad_text"] = _text(bad)
            result["after_bad"] = list(_FixtureHandler.seen)

        await _run(actions, env)

        # Success path
        assert result["ok_error"] is False
        payload = json.loads(result["ok_text"])
        assert payload["node"] == {"node": "pve-01", "status": "online"}
        assert any("pve-01/status" in p for p in result["after_ok"])

        # Denial contract: structured tool result, not a protocol error
        assert result["bad_error"] is False
        bad_payload = json.loads(result["bad_text"])
        assert bad_payload["rejected_before_upstream_call"] is True
        assert "pve-99" in bad_payload["error"]
        assert "pve-01" not in bad_payload["error"]  # ACL not leaked
        assert result["after_bad"] == []  # denial made NO upstream call
    finally:
        srv.shutdown()


async def test_stdio_denies_without_inventory() -> None:
    # No inventory configured at all → every targeted call is denied, never reaches upstream
    result: dict[str, Any] = {}

    async def actions(session: ClientSession) -> None:
        res = await session.call_tool("host_health", {"host": "pve-01"})
        result["is_error"] = res.is_error
        result["text"] = _text(res)

    await _run(actions)
    assert result["is_error"] is False
    payload = json.loads(result["text"])
    assert payload["rejected_before_upstream_call"] is True
    assert "inventory" in payload["error"].lower()


async def test_stdio_pilot_mode_scope(tmp_path: Path) -> None:
    """
    Pilot mode through the real stdio entry point against the local fixture:
      * discovery advertises only proxmox_status
      * one authorized node call succeeds (fixture sees it)
      * omitted node, unauthorized node, and vmid-without-node are all denied with
        ZERO new upstream requests (cluster overview is unreachable).
    """
    srv, base_url = _start_fixture()
    try:
        inv = tmp_path / "inv.yaml"
        inv.write_text("proxmox:\n  nodes:\n    - name: pve-01\n")
        env = {
            "PROXMOX_API_URL": base_url,
            "PROXMOX_API_TOKEN": "user@pve!read=FAKE-NOT-A-REAL-SECRET",
            "HERMES_INVENTORY_FILE": str(inv),
            "CA_MCP_PILOT": "1",
        }
        out: dict[str, Any] = {}

        async def actions(session: ClientSession) -> None:
            listed = await session.list_tools()
            out["tools"] = {t.name for t in listed.tools}

            _FixtureHandler.seen = []
            ok = await session.call_tool("proxmox_status", {"node": "pve-01"})
            out["ok_error"] = ok.is_error
            out["ok_text"] = _text(ok)
            out["after_ok"] = list(_FixtureHandler.seen)

            for key, args in (
                ("omitted", {}),
                ("unauth", {"node": "pve-99"}),
                ("vmid_only", {"vmid": 100}),
            ):
                _FixtureHandler.seen = []
                res = await session.call_tool("proxmox_status", args)
                out[f"{key}_error"] = res.is_error
                out[f"{key}_text"] = _text(res)
                out[f"{key}_seen"] = list(_FixtureHandler.seen)

        await _run(actions, env)

        # Discovery: only the single pilot tool is advertised
        assert out["tools"] == {"proxmox_status"}

        # Authorized call succeeds and reaches the fixture
        assert out["ok_error"] is False
        assert json.loads(out["ok_text"])["node"] == {"node": "pve-01", "status": "online"}
        assert any("pve-01/status" in p for p in out["after_ok"])

        # All three rejections: structured result, zero upstream traffic
        for key in ("omitted", "unauth", "vmid_only"):
            assert out[f"{key}_error"] is False
            payload = json.loads(out[f"{key}_text"])
            assert payload["rejected_before_upstream_call"] is True
            assert out[f"{key}_seen"] == []
            assert "pve-01" not in payload["error"]  # ACL never leaked
    finally:
        srv.shutdown()


async def test_stdio_pilot_single_node_invariant(tmp_path: Path) -> None:
    """Pilot mode authorizes EXACTLY one node: a two-node inventory is a
    misconfiguration and every targeted call is denied with zero upstream traffic."""
    srv, base_url = _start_fixture()
    try:
        inv = tmp_path / "inv.yaml"
        inv.write_text("proxmox:\n  nodes:\n    - name: pve-01\n    - name: pve-02\n")
        env = {
            "PROXMOX_API_URL": base_url,
            "PROXMOX_API_TOKEN": "user@pve!read=FAKE-NOT-A-REAL-SECRET",
            "HERMES_INVENTORY_FILE": str(inv),
            "CA_MCP_PILOT": "1",
        }
        out: dict[str, Any] = {}

        async def actions(session: ClientSession) -> None:
            _FixtureHandler.seen = []
            res = await session.call_tool("proxmox_status", {"node": "pve-01"})
            out["is_error"] = res.is_error
            out["text"] = _text(res)
            out["seen"] = list(_FixtureHandler.seen)

        await _run(actions, env)
        assert out["is_error"] is False
        payload = json.loads(out["text"])
        assert payload["rejected_before_upstream_call"] is True
        assert "exactly one node" in payload["error"].lower()
        assert out["seen"] == []  # zero upstream
    finally:
        srv.shutdown()
