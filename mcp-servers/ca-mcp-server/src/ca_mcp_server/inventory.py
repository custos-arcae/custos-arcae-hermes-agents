"""
inventory — Shared target authorization for all MCP tools.

Enforces the cross-project isolation boundary *before* any upstream call is made.

Authorization model (deny-by-default, evaluated independently per category):
  * Three target categories exist: managed **hosts**, Proxmox **nodes**, PBS
    **datastores**. Each is authorized independently. An **empty** category
    authorizes **zero** targets — it never falls back to "allow anything".
  * The authoritative source is ``HERMES_INVENTORY_FILE`` — a YAML file matching
    ``profiles/infra-ops/config.example.yaml``. When it is configured all three
    categories come from it and nothing else.
  * ``HERMES_HOSTS_ALLOWED`` (comma-separated) is a convenience source for the
    **hosts** category ONLY, and ONLY when no inventory file is configured. It
    never widens an explicitly configured inventory.
  * A missing, unreadable, malformed or structurally invalid inventory is a
    **load error**: every target is rejected with a safe, actionable diagnostic.
    The server still starts and can list tools; only actual calls are denied.
  * Path parameters are always checked against a safe character pattern to
    prevent API path traversal, independent of any allowlist.

Tool error messages never include the contents of the access-control list; they
state only whether a specific target is authorized.
"""

from __future__ import annotations

import os
import re
from typing import Any

import yaml

# ── Regex guards ──────────────────────────────────────────────────────────────
# Safe identifiers for URL path segments: no .., /, ;, whitespace, or leading -
_SAFE_PATH_PARAM: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")

# RFC-1123 hostnames or IPv4/IPv6 — blocks SSH/ping argument injection
# (e.g. a value starting with '-' would be parsed as a CLI flag)
_VALID_HOST_TARGET: re.Pattern[str] = re.compile(
    r"^(?:"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*"
    r"|(?:(?:\d{1,3}\.){3}\d{1,3})"
    r"|(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4})$"
)

# Maps a require_path_param() field name to the inventory category it authorizes.
# job_id is intentionally absent: PBS job ids are opaque and not enumerated in the
# inventory; they are format-checked only, and the (read-only) token scopes access.
_PATH_CATEGORY: dict[str, str] = {"node": "nodes", "datastore": "datastores"}

# Human-readable singular noun for diagnostics.
_CATEGORY_NOUN: dict[str, str] = {"hosts": "host", "nodes": "node", "datastores": "datastore"}


# ── Exceptions ────────────────────────────────────────────────────────────────


class InventoryError(Exception):
    """Raised when a requested target is not authorized or the inventory is unusable."""


# ── Inventory class ───────────────────────────────────────────────────────────


class Inventory:
    """
    Immutable authorization set for infrastructure targets.

    Empty category ⇒ zero authorized targets in that category (deny-by-default).
    A non-None ``load_error`` means the configured inventory could not be used and
    every target is rejected with that diagnostic.
    """

    __slots__ = ("_hosts", "_nodes", "_datastores", "_load_error", "_file_present", "_hosts_from_env")

    def __init__(
        self,
        hosts: frozenset[str] = frozenset(),
        nodes: frozenset[str] = frozenset(),
        datastores: frozenset[str] = frozenset(),
        *,
        load_error: str | None = None,
        file_present: bool = False,
        hosts_from_env: bool = False,
    ) -> None:
        self._hosts = hosts
        self._nodes = nodes
        self._datastores = datastores
        self._load_error = load_error
        self._file_present = file_present
        self._hosts_from_env = hosts_from_env

    # ── Loading ─────────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> Inventory:
        """Build an Inventory from the environment. Never raises; records load errors."""
        inventory_file = os.environ.get("HERMES_INVENTORY_FILE", "").strip()
        hosts_str = os.environ.get("HERMES_HOSTS_ALLOWED", "").strip()

        if inventory_file:
            return cls._from_file(inventory_file)

        # No inventory file: hosts may come from HERMES_HOSTS_ALLOWED only.
        # Nodes and datastores have no source → empty → deny all (proxmox/pbs
        # per-node/datastore lookups are refused; only unscoped cluster/PBS
        # overviews are possible, governed by the read-only token).
        hosts = frozenset(h.strip() for h in hosts_str.split(",") if h.strip())
        if hosts:
            return cls(hosts=hosts, hosts_from_env=True)
        return cls(load_error="no inventory configured (set HERMES_INVENTORY_FILE)")

    @classmethod
    def _from_file(cls, path: str) -> Inventory:
        if not os.path.isfile(path):
            return cls(load_error=f"HERMES_INVENTORY_FILE '{path}' does not exist or is not a file")
        try:
            with open(path) as f:
                raw = f.read()
        except OSError as exc:
            return cls(load_error=f"HERMES_INVENTORY_FILE '{path}' is not readable ({exc.strerror or 'error'})")
        try:
            cfg = yaml.safe_load(raw)
        except yaml.YAMLError:
            return cls(load_error=f"HERMES_INVENTORY_FILE '{path}' is not valid YAML")
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            return cls(load_error=f"HERMES_INVENTORY_FILE '{path}' must be a mapping at the top level")

        try:
            hosts = _extract_names(cfg, ("hosts", "inventory"), "hostname")
            nodes = _extract_names(cfg, ("proxmox", "nodes"), "name")
            datastores = _extract_names(cfg, ("pbs", "datastores"), "name")
        except _InvalidInventory as exc:
            return cls(load_error=f"HERMES_INVENTORY_FILE '{path}' is malformed: {exc}")

        return cls(
            hosts=frozenset(hosts),
            nodes=frozenset(nodes),
            datastores=frozenset(datastores),
            file_present=True,
        )

    # ── Internal ────────────────────────────────────────────────────────────

    def _guard_load(self) -> None:
        if self._load_error is not None:
            raise InventoryError(
                "Target authorization unavailable: the inventory could not be loaded. "
                "Ask the operator to fix HERMES_INVENTORY_FILE."
            )

    def _configured(self, category: str) -> bool:
        if category == "hosts":
            return self._file_present or self._hosts_from_env
        return self._file_present

    def _deny(self, category: str, value: str) -> None:
        noun = _CATEGORY_NOUN.get(category, category)
        if not self._configured(category):
            raise InventoryError(
                f"No {noun} inventory is configured; refusing '{value}'. "
                f"Set HERMES_INVENTORY_FILE to authorize {noun}s."
            )
        raise InventoryError(f"'{value}' is not an authorized {noun}.")

    # ── Validators ──────────────────────────────────────────────────────────

    def require_path_param(self, name: str, value: str) -> None:
        """Validate a value interpolated into an API URL path (format + membership)."""
        self._guard_load()
        if not _SAFE_PATH_PARAM.match(value):
            raise InventoryError(
                f"Disallowed characters in {name} '{value}'. "
                f"Allowed: alphanumeric, '.', '-', '_; max 63 chars."
            )
        category = _PATH_CATEGORY.get(name)
        if category is None:
            return  # format-only (e.g. job_id)
        allowlist = getattr(self, f"_{category}")
        if value not in allowlist:
            self._deny(category, value)

    def require_host_target(self, host: str) -> None:
        """Validate a hostname/IP that will be passed to SSH or ping."""
        self._guard_load()
        if not _VALID_HOST_TARGET.match(host):
            raise InventoryError(
                f"Invalid host target '{host}'. "
                f"Must be a valid RFC-1123 hostname or IPv4/IPv6 address."
            )
        if host not in self._hosts:
            self._deny("hosts", host)

    def require_host_list(self, hosts: list[str], max_size: int = 20) -> None:
        """Validate every host in a list; cap list length to prevent resource exhaustion."""
        self._guard_load()
        if len(hosts) > max_size:
            raise InventoryError(f"Host list size {len(hosts)} exceeds maximum {max_size}.")
        for h in hosts:
            self.require_host_target(h)

    def ensure_single_node(self) -> None:
        """
        Pilot invariant: the inventory must authorize **exactly one** Proxmox node.

        Raises if the inventory is unloadable, has no node source, or lists any
        number of nodes other than one. The message never echoes node names or the
        configured count — it only points the operator at the fix.
        """
        self._guard_load()
        if len(self._nodes) != 1:
            raise InventoryError(
                "Pilot mode authorizes exactly one node, but the configured inventory "
                "does not. Narrow HERMES_INVENTORY_FILE to a single 'proxmox.nodes' entry."
            )

    # ── Introspection (used by diagnostics/tests; never echoes the ACL) ──────

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def counts(self) -> dict[str, int]:
        return {"hosts": len(self._hosts), "nodes": len(self._nodes), "datastores": len(self._datastores)}


class _InvalidInventory(Exception):
    """Internal: structural problem while extracting a category."""


def _extract_names(cfg: dict[str, Any], path: tuple[str, str], key: str) -> set[str]:
    """Return the set of `key` values from a list-of-mappings at cfg[path[0]][path[1]]."""
    section = cfg.get(path[0])
    if section is None:
        return set()
    if not isinstance(section, dict):
        raise _InvalidInventory(f"'{path[0]}' must be a mapping")
    entries = section.get(path[1])
    if entries is None:
        return set()
    if not isinstance(entries, list):
        raise _InvalidInventory(f"'{path[0]}.{path[1]}' must be a list")
    names: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise _InvalidInventory(f"'{path[0]}.{path[1]}' entries must be mappings")
        value = item.get(key)
        if isinstance(value, str) and value:
            names.add(value)
    return names


# ── Module-level singleton (lazy) ─────────────────────────────────────────────

_inventory: Inventory | None = None


def get_inventory() -> Inventory:
    """Return the lazily-initialised Inventory singleton (per server process)."""
    global _inventory  # noqa: PLW0603
    if _inventory is None:
        _inventory = Inventory.load()
    return _inventory


def reset_inventory() -> None:
    """Clear the singleton (used in tests)."""
    global _inventory  # noqa: PLW0603
    _inventory = None
