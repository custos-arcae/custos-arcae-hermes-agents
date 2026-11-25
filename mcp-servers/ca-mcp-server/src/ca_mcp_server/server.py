"""
ca-mcp-server — MCP server entry point.

Registers all infrastructure tools and starts the stdio transport.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from ca_mcp_server.runtime import pilot_mode
from ca_mcp_server.tools.connectivity_status import register as reg_connectivity
from ca_mcp_server.tools.host_health import register as reg_host_health
from ca_mcp_server.tools.pbs_status import register as reg_pbs
from ca_mcp_server.tools.proxmox_status import register as reg_proxmox

logger = logging.getLogger(__name__)

mcp = MCPServer(
    name="ca-mcp-server",
    version="0.1.0",
    instructions=(
        "Infrastructure tools for the custos-arcae estate. "
        "All tools are read-only unless explicitly noted. "
        "Every invocation must be logged by audit-logger."
    ),
)


def _register_tools(server: MCPServer) -> None:
    """
    Register tools on *server*.

    Pilot mode (``CA_MCP_PILOT`` truthy) exposes the single read-only pilot tool
    ``proxmox_status`` only. Outside pilot mode the full set of four tools is
    registered, so the general integration is unaffected.
    """
    reg_proxmox(server)
    if pilot_mode():
        return
    reg_pbs(server)
    reg_host_health(server)
    reg_connectivity(server)


def main() -> None:
    dev_mode = "--dev" in sys.argv
    logging.basicConfig(
        level=logging.DEBUG if dev_mode else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    _register_tools(mcp)
    logger.info("ca-mcp-server starting (dev=%s, pilot=%s)", dev_mode, pilot_mode())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
