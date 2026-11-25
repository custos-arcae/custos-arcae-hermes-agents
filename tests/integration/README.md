# Integration tests

The real end-to-end stdio integration test for the MCP server lives with the
server package, where its dependencies are installed:

    mcp-servers/ca-mcp-server/tests/test_stdio.py

It starts `ca-mcp-server` through its normal entry point over a stdio transport
and drives it with a genuine MCP `ClientSession`, using no live services or
credentials. It is executed by the `mcp-server-tests` CI job
(`pytest tests` in `mcp-servers/ca-mcp-server`).

Root-level `pytest` (`tests/unit`) covers policy/schema conformance only.
