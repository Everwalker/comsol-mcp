"""COMSOL MCP package."""

def main() -> None:
    """Load the stdio entrypoint without registering tools in daemon imports."""
    from .mcp_server import main as run
    run()

__all__ = ["main"]
