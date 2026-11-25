"""Smoke test: ca_mcp_server package is importable and version is correct."""

import ca_mcp_server


def test_version() -> None:
    assert ca_mcp_server.__version__ == "0.1.0"
