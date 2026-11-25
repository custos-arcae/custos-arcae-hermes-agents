"""
Unit/integration tests for the four read-only MCP tools and the inventory
authorization model.

These run entirely in-process with mocked upstreams (respx for HTTP, patched
asyncio subprocess for SSH/ping), so they need no live services or credentials
and are deterministic for CI.

Authorization model under test (deny-by-default, independent per category):
  * empty category authorizes zero targets
  * absent / unreadable / malformed / invalid inventory → safe actionable denial
  * HERMES_HOSTS_ALLOWED never widens an explicitly configured inventory
  * denials happen before any HTTP request or subprocess launch
  * tool errors never echo the access-control list
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp.server.mcpserver import MCPServer

from ca_mcp_server import inventory as inv_mod
from ca_mcp_server.inventory import Inventory, InventoryError
from ca_mcp_server.server import _register_tools
from ca_mcp_server.tools import connectivity_status, host_health
from ca_mcp_server.tools import pbs_status as pbs_mod
from ca_mcp_server.tools import proxmox_status as pvm_mod

# ── Helpers ─────────────────────────────────────────────────────────────────

EXPECTED_TOOLS = {"proxmox_status", "pbs_status", "host_health", "connectivity_status"}


def _build_server() -> MCPServer:
    mcp = MCPServer(name="test")
    pvm_mod.register(mcp)
    pbs_mod.register(mcp)
    host_health.register(mcp)
    connectivity_status.register(mcp)
    return mcp


def _payload(res: Any) -> dict[str, Any]:
    """Extract the tool's dict return value from a CallToolResult."""
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict):
        return sc.get("result", sc)
    text = res.content[0].text  # type: ignore[index]
    return json.loads(text)


def _write_inventory(tmp_path: Path, body: str) -> str:
    p = tmp_path / "inv.yaml"
    p.write_text(body)
    return str(p)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with no inventory and no upstream config."""
    inv_mod.reset_inventory()
    for var in (
        "HERMES_INVENTORY_FILE",
        "HERMES_HOSTS_ALLOWED",
        "PROXMOX_API_URL",
        "PROXMOX_API_TOKEN",
        "PBS_API_URL",
        "PBS_API_TOKEN",
        "CA_MCP_PILOT",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    inv_mod.reset_inventory()


# ── Registration invariants ───────────────────────────────────────────────────


async def test_exactly_four_tools_registered() -> None:
    mcp = _build_server()
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_all_tools_marked_read_only() -> None:
    mcp = _build_server()
    tools = await mcp.list_tools()
    for t in tools:
        assert t.annotations is not None, t.name
        assert t.annotations.read_only_hint is True, t.name
        assert t.annotations.destructive_hint is not True, t.name


async def test_no_write_capable_tools() -> None:
    mcp = _build_server()
    tools = await mcp.list_tools()
    write_verbs = ("create", "delete", "remove", "start", "stop", "restart",
                   "shutdown", "set", "update", "patch", "apply", "migrate",
                   "write", "exec", "run", "reboot", "power")
    for t in tools:
        lowered = t.name.lower()
        assert not any(v in lowered for v in write_verbs), f"possible write tool: {t.name}"
        assert lowered in EXPECTED_TOOLS


# ── Pilot-mode registration boundary ──────────────────────────────────────────


async def test_full_mode_registers_all_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CA_MCP_PILOT", raising=False)
    srv = MCPServer(name="t")
    _register_tools(srv)
    assert {t.name for t in await srv.list_tools()} == EXPECTED_TOOLS


async def test_pilot_mode_registers_only_proxmox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CA_MCP_PILOT", "1")
    srv = MCPServer(name="t")
    _register_tools(srv)
    assert {t.name for t in await srv.list_tools()} == {"proxmox_status"}


# ── Inventory authorization model ─────────────────────────────────────────────


def test_absent_inventory_denies_all() -> None:
    inv = Inventory()  # empty, no sources
    with pytest.raises(InventoryError):
        inv.require_host_target("pve-01")  # valid format but no allowlist
    with pytest.raises(InventoryError):
        inv.require_path_param("node", "pve-01")


def test_empty_category_authorizes_zero_targets() -> None:
    # datastores configured-but-empty denies every datastore, even well-formed
    inv = Inventory(nodes=frozenset({"pve-01"}), file_present=True)
    inv.require_path_param("node", "pve-01")  # allowed
    with pytest.raises(InventoryError):
        inv.require_path_param("datastore", "ds-main")  # empty category → deny


def test_categories_are_independent() -> None:
    inv = Inventory(hosts=frozenset({"h1"}), nodes=frozenset({"n1"}), file_present=True)
    inv.require_host_target("h1")
    inv.require_path_param("node", "n1")
    with pytest.raises(InventoryError):
        inv.require_path_param("datastore", "n1")  # datastore category is empty
    with pytest.raises(InventoryError):
        inv.require_host_target("n1")  # n1 is a node, not a host


def test_path_traversal_blocked_before_membership() -> None:
    inv = Inventory(nodes=frozenset({"pve-01"}), file_present=True)
    for bad in ("../etc/passwd", "a/b", "no spaces", "-lead", "x;rm", ".."):
        with pytest.raises(InventoryError):
            inv.require_path_param("node", bad)


def test_host_injection_blocked() -> None:
    inv = Inventory(hosts=frozenset({"pve-01.example.com", "10.0.0.5"}), file_present=True)
    for bad in ("-L", "evil; rm -rf /", "host name", "1.2.3.4; nc", "$(id)"):
        with pytest.raises(InventoryError):
            inv.require_host_target(bad)


def test_error_does_not_leak_acl_contents() -> None:
    inv = Inventory(hosts=frozenset({"secret-host-a", "secret-host-b"}), file_present=True)
    with pytest.raises(InventoryError) as ei:
        inv.require_host_target("rogue")
    msg = str(ei.value)
    assert "secret-host-a" not in msg and "secret-host-b" not in msg


def test_host_list_capped() -> None:
    inv = Inventory(hosts=frozenset({f"h{i}" for i in range(30)}), file_present=True)
    with pytest.raises(InventoryError):
        inv.require_host_list([f"h{i}" for i in range(30)], max_size=20)


def test_ensure_single_node_accepts_exactly_one() -> None:
    inv = Inventory(nodes=frozenset({"pve-01"}), file_present=True)
    inv.ensure_single_node()  # exactly one → no raise


def test_ensure_single_node_rejects_zero_and_many_without_leak() -> None:
    # zero nodes
    inv0 = Inventory(nodes=frozenset(), file_present=True)
    with pytest.raises(InventoryError) as e0:
        inv0.ensure_single_node()
    # two nodes
    inv2 = Inventory(nodes=frozenset({"pve-01", "pve-02"}), file_present=True)
    with pytest.raises(InventoryError) as e2:
        inv2.ensure_single_node()
    # never echoes node names or the count
    for ei in (e0, e2):
        msg = str(ei.value)
        assert "pve-01" not in msg and "pve-02" not in msg
        assert "exactly one node" in msg.lower()


def test_ensure_single_node_respects_load_error() -> None:
    inv = Inventory(load_error="boom")
    with pytest.raises(InventoryError):
        inv.ensure_single_node()


# ── Inventory loading (file + env sources) ────────────────────────────────────


def test_load_from_inventory_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(
        tmp_path,
        "proxmox:\n  nodes:\n    - name: pve-01\npbs:\n  datastores:\n    - name: ds-main\n"
        "hosts:\n  inventory:\n    - hostname: h1\n",
    )
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    inv = Inventory.load()
    assert inv.load_error is None
    assert inv.counts() == {"hosts": 1, "nodes": 1, "datastores": 1}


def test_missing_inventory_file_is_load_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_INVENTORY_FILE", "/no/such/inventory.yaml")
    inv = Inventory.load()
    assert inv.load_error is not None
    with pytest.raises(InventoryError):
        inv.require_path_param("node", "pve-01")


def test_malformed_inventory_is_load_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "proxmox:\n  nodes:\n    notamapping\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    inv = Inventory.load()
    assert inv.load_error is not None
    with pytest.raises(InventoryError):
        inv.require_path_param("node", "pve-01")


def test_invalid_yaml_is_load_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "proxmox: [unclosed\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    inv = Inventory.load()
    assert inv.load_error is not None


def test_top_level_not_mapping_is_load_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "- just\n- a\n- list\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    inv = Inventory.load()
    assert inv.load_error is not None


def test_hosts_allowed_only_used_without_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOSTS_ALLOWED", "pve-01,pve-02")
    inv = Inventory.load()
    assert inv.load_error is None
    inv.require_host_target("pve-01")
    with pytest.raises(InventoryError):
        inv.require_host_target("rogue")


def test_hosts_allowed_does_not_widen_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "hosts:\n  inventory:\n    - hostname: pve-01\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("HERMES_HOSTS_ALLOWED", "rogue")  # must be ignored when file present
    inv = Inventory.load()
    inv.require_host_target("pve-01")
    with pytest.raises(InventoryError):
        inv.require_host_target("rogue")


# ── proxmox_status ────────────────────────────────────────────────────────────


async def test_proxmox_cluster_overview_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # node=None is an unscoped overview: allowed, governed by the read-only token.
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET-TOKEN")
    mcp = _build_server()
    with respx.mock(assert_all_called=True) as rx:
        rx.get("https://pve.test:8006/api2/json/nodes").mock(
            return_value=httpx.Response(200, json={"data": [{"node": "pve-01"}]})
        )
        rx.get("https://pve.test:8006/api2/json/cluster/resources").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        res = await mcp.call_tool("proxmox_status", {})
    payload = _payload(res)
    assert payload["nodes"] == [{"node": "pve-01"}]


async def test_proxmox_authorized_node_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "proxmox:\n  nodes:\n    - name: pve-01\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=True) as rx:
        rx.get("https://pve.test:8006/api2/json/nodes/pve-01/status").mock(
            return_value=httpx.Response(200, json={"data": {"uptim": 1}})
        )
        rx.get("https://pve.test:8006/api2/json/nodes/pve-01/qemu").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        rx.get("https://pve.test:8006/api2/json/nodes/pve-01/storage").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        res = await mcp.call_tool("proxmox_status", {"node": "pve-01"})
    assert _payload(res)["node"] == {"uptim": 1}


async def test_proxmox_out_of_inventory_denied_no_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _write_inventory(tmp_path, "proxmox:\n  nodes:\n    - name: pve-01\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("proxmox_status", {"node": "rogue-node"})
        assert route.call_count == 0
    payload = _payload(res)
    assert payload["rejected_before_upstream_call"] is True
    assert "rogue-node" in payload["error"]
    assert "pve-01" not in payload["error"]  # no ACL leak


async def test_proxmox_empty_nodes_denies_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "proxmox:\n  nodes: []\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("proxmox_status", {"node": "pve-01"})
        assert route.call_count == 0
    assert _payload(res)["rejected_before_upstream_call"] is True


async def test_proxmox_invalid_inventory_denies_before_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_INVENTORY_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("proxmox_status", {"node": "pve-01"})
        assert route.call_count == 0
    payload = _payload(res)
    assert payload["rejected_before_upstream_call"] is True
    assert "inventory" in payload["error"].lower()


async def test_proxmox_traversal_rejected_no_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("proxmox_status", {"node": "../../etc/passwd"})
        assert route.call_count == 0
    assert _payload(res)["rejected_before_upstream_call"] is True


async def test_proxmox_vmid_out_of_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "proxmox:\n  nodes:\n    - name: pve-01\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("proxmox_status", {"node": "pve-01", "vmid": 0})
        assert route.call_count == 0
    payload = _payload(res)
    assert payload["rejected_before_upstream_call"] is True
    assert "vmid" in payload["error"]


async def test_proxmox_vmid_without_node_rejected_no_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    # node omitted but vmid given → ambiguous request; reject before any HTTP (all modes)
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("proxmox_status", {"vmid": 100})
        assert route.call_count == 0
    payload = _payload(res)
    assert payload["rejected_before_upstream_call"] is True
    assert "vmid" in payload["error"] and "node" in payload["error"]


async def test_pilot_omitted_node_rejected_no_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # pilot mode → node required; cluster overview is unreachable, zero HTTP
    p = _write_inventory(tmp_path, "proxmox:\n  nodes:\n    - name: pve-01\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    monkeypatch.setenv("CA_MCP_PILOT", "1")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("proxmox_status", {})
        assert route.call_count == 0
    payload = _payload(res)
    assert payload["rejected_before_upstream_call"] is True
    assert "cluster overview" in payload["error"].lower()


async def test_pilot_unauthorized_node_denied_no_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "proxmox:\n  nodes:\n    - name: pve-01\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    monkeypatch.setenv("CA_MCP_PILOT", "1")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("proxmox_status", {"node": "pve-99"})
        assert route.call_count == 0
    payload = _payload(res)
    assert payload["rejected_before_upstream_call"] is True
    assert "pve-01" not in payload["error"]  # no ACL leak


async def test_pilot_authorized_node_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "proxmox:\n  nodes:\n    - name: pve-01\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    monkeypatch.setenv("CA_MCP_PILOT", "1")
    mcp = _build_server()
    with respx.mock(assert_all_called=True) as rx:
        rx.get("https://pve.test:8006/api2/json/nodes/pve-01/status").mock(
            return_value=httpx.Response(200, json={"data": {"uptime": 1}})
        )
        rx.get("https://pve.test:8006/api2/json/nodes/pve-01/qemu").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        rx.get("https://pve.test:8006/api2/json/nodes/pve-01/storage").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        res = await mcp.call_tool("proxmox_status", {"node": "pve-01"})
    assert _payload(res)["node"] == {"uptime": 1}


async def test_pilot_multi_node_inventory_rejected_no_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Second configured node breaks the single-node invariant → reject before any HTTP
    p = _write_inventory(tmp_path, "proxmox:\n  nodes:\n    - name: pve-01\n    - name: pve-02\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    monkeypatch.setenv("CA_MCP_PILOT", "1")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("proxmox_status", {"node": "pve-01"})
        assert route.call_count == 0
    payload = _payload(res)
    assert payload["rejected_before_upstream_call"] is True
    assert "exactly one node" in payload["error"].lower()


async def test_nonpilot_cluster_overview_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    # Outside pilot mode the cluster overview (node omitted) remains available.
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=True) as rx:
        rx.get("https://pve.test:8006/api2/json/nodes").mock(
            return_value=httpx.Response(200, json={"data": [{"node": "pve-01"}, {"node": "pve-02"}]})
        )
        rx.get("https://pve.test:8006/api2/json/cluster/resources").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        res = await mcp.call_tool("proxmox_status", {})
    assert len(_payload(res)["nodes"]) == 2


async def test_proxmox_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=True) as rx:
        rx.get("https://pve.test:8006/api2/json/nodes").mock(side_effect=httpx.ConnectTimeout("timed out"))
        res = await mcp.call_tool("proxmox_status", {})
    assert "timed out" in _payload(res)["error"].lower()


async def test_proxmox_auth_failure_is_credential_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXMOX_API_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_API_TOKEN", "user@pve!read=SUPERSECRET-TOKEN")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        rx.get("https://pve.test:8006/api2/json/nodes").mock(return_value=httpx.Response(401, text="unauthorized"))
        res = await mcp.call_tool("proxmox_status", {})
    payload = _payload(res)
    assert "401" in payload["error"]
    assert "SUPERSECRET-TOKEN" not in payload["error"]


# ── pbs_status ────────────────────────────────────────────────────────────────


async def test_pbs_overview_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PBS_API_URL", "https://pbs.test:8007")
    monkeypatch.setenv("PBS_API_TOKEN", "user@pbs!read=PBSSECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=True) as rx:
        rx.get("https://pbs.test:8007/api2/json/config/datastore").mock(
            return_value=httpx.Response(200, json={"data": [{"store": "ds-main"}]})
        )
        rx.get("https://pbs.test:8007/api2/json/config/jobs").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        rx.get("https://pbs.test:8007/api2/json/status/datastore").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        res = await mcp.call_tool("pbs_status", {})
    assert _payload(res)["datastores"] == [{"store": "ds-main"}]


async def test_pbs_authorized_datastore_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "pbs:\n  datastores:\n    - name: ds-main\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("PBS_API_URL", "https://pbs.test:8007")
    monkeypatch.setenv("PBS_API_TOKEN", "user@pbs!read=PBSSECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=True) as rx:
        rx.get("https://pbs.test:8007/api2/json/config/datastore/ds-main").mock(
            return_value=httpx.Response(200, json={"data": {"store": "ds-main"}})
        )
        rx.get("https://pbs.test:8007/api2/json/status/datastore/ds-main").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        rx.get("https://pbs.test:8007/api2/json/admin/datastore/ds-main/verify").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )
        res = await mcp.call_tool("pbs_status", {"datastore": "ds-main"})
    assert _payload(res)["datastore"] == {"store": "ds-main"}


async def test_pbs_out_of_inventory_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_inventory(tmp_path, "pbs:\n  datastores:\n    - name: ds-main\n")
    monkeypatch.setenv("HERMES_INVENTORY_FILE", p)
    monkeypatch.setenv("PBS_API_URL", "https://pbs.test:8007")
    monkeypatch.setenv("PBS_API_TOKEN", "user@pbs!read=PBSSECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("pbs_status", {"datastore": "ds-secret"})
        assert route.call_count == 0
    payload = _payload(res)
    assert payload["rejected_before_upstream_call"] is True
    assert "ds-main" not in payload["error"]


async def test_pbs_datastore_traversal_rejected_no_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PBS_API_URL", "https://pbs.test:8007")
    monkeypatch.setenv("PBS_API_TOKEN", "user@pbs!read=PBSSECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("pbs_status", {"datastore": "../secrets"})
        assert route.call_count == 0
    assert _payload(res)["rejected_before_upstream_call"] is True


async def test_pbs_job_id_injection_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PBS_API_URL", "https://pbs.test:8007")
    monkeypatch.setenv("PBS_API_TOKEN", "user@pbs!read=PBSSECRET")
    mcp = _build_server()
    with respx.mock(assert_all_called=False) as rx:
        route = rx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"data": []}))
        res = await mcp.call_tool("pbs_status", {"job_id": "1; rm -rf /"})
        assert route.call_count == 0
    assert _payload(res)["rejected_before_upstream_call"] is True


# ── host_health (SSH) ─────────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, out: bytes = b"ok", rc: int = 0) -> None:
        self._out, self._err, self.returncode = out, b"", rc

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, self._err

    def kill(self) -> None:  # pragma: no cover
        pass


def _install_fake_ssh(monkeypatch: pytest.MonkeyPatch, seen: list[list[Any]]) -> None:
    async def fake_exec(*a: Any, **k: Any) -> _FakeProc:
        seen.append(list(a))
        return _FakeProc(out=b"up 1 hour")

    monkeypatch.setattr(host_health.asyncio, "create_subprocess_exec", fake_exec)


async def test_host_health_no_inventory_denies_no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[Any]] = []
    _install_fake_ssh(monkeypatch, seen)
    mcp = _build_server()
    res = await mcp.call_tool("host_health", {"host": "pve-01"})
    assert seen == []
    payload = _payload(res)
    assert payload["rejected_before_upstream_call"] is True
    assert "inventory" in payload["error"].lower()


async def test_host_health_invalid_target_rejected_no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[Any]] = []
    _install_fake_ssh(monkeypatch, seen)
    monkeypatch.setenv("HERMES_HOSTS_ALLOWED", "pve-01")
    mcp = _build_server()
    res = await mcp.call_tool("host_health", {"host": "-oProxyCommand=evil"})
    assert seen == []
    assert _payload(res)["rejected_before_upstream_call"] is True


async def test_host_health_out_of_inventory_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[Any]] = []
    _install_fake_ssh(monkeypatch, seen)
    monkeypatch.setenv("HERMES_HOSTS_ALLOWED", "pve-01,pve-02")
    mcp = _build_server()
    res = await mcp.call_tool("host_health", {"host": "rogue.example.com"})
    assert seen == []
    assert _payload(res)["rejected_before_upstream_call"] is True


async def test_host_health_authorized_uses_double_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[Any]] = []
    _install_fake_ssh(monkeypatch, seen)
    monkeypatch.setenv("HERMES_HOSTS_ALLOWED", "pve-01")
    mcp = _build_server()
    res = await mcp.call_tool("host_health", {"host": "pve-01"})
    payload = _payload(res)
    assert payload["host"] == "pve-01"
    assert seen, "authorized host must reach the SSH stage"
    for argv in seen:
        assert "--" in argv
        assert argv.index("--") < argv.index("pve-01")


# ── connectivity_status (ping) ────────────────────────────────────────────────


async def test_connectivity_oversized_list_rejected_no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_exec(*a: Any, **k: Any) -> _FakeProc:
        nonlocal called
        called = True
        return _FakeProc()

    monkeypatch.setattr(connectivity_status.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("HERMES_HOSTS_ALLOWED", ",".join(f"h{i}.local" for i in range(30)))
    mcp = _build_server()
    res = await mcp.call_tool("connectivity_status", {"hosts": [f"h{i}.local" for i in range(30)]})
    assert called is False
    assert _payload(res)["rejected_before_upstream_call"] is True


async def test_connectivity_no_inventory_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*a: Any, **k: Any) -> _FakeProc:
        raise AssertionError("subprocess must not run without inventory")

    monkeypatch.setattr(connectivity_status.asyncio, "create_subprocess_exec", fake_exec)
    mcp = _build_server()
    res = await mcp.call_tool("connectivity_status", {"hosts": ["pve-01"]})
    assert _payload(res)["rejected_before_upstream_call"] is True


async def test_connectivity_invalid_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*a: Any, **k: Any) -> _FakeProc:
        raise AssertionError("subprocess must not run for invalid host")

    monkeypatch.setattr(connectivity_status.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("HERMES_HOSTS_ALLOWED", "pve-01")
    mcp = _build_server()
    res = await mcp.call_tool("connectivity_status", {"hosts": ["pve-01", "bad host; rm"]})
    assert _payload(res)["rejected_before_upstream_call"] is True


async def test_connectivity_authorized_reports_predefined_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*a: Any, **k: Any) -> _FakeProc:
        return _FakeProc(out=b"3 packets transmitted, 3 received\nrtt min/avg/max = 0.1/0.2/0.3 ms", rc=0)

    monkeypatch.setattr(connectivity_status.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("HERMES_HOSTS_ALLOWED", "pve-01,pve-02")
    mcp = _build_server()
    res = await mcp.call_tool("connectivity_status", {"hosts": ["pve-01", "pve-02"]})
    payload = _payload(res)
    assert payload["summary"] == {"total": 2, "reachable": 2, "unreachable": 0}
    assert all(r["reachable"] for r in payload["results"])
