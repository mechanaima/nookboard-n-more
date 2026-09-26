"""Top-level launch shim for the Nookboard MCP server."""
import sys

from app.nookboard_mcp import run_stdio


if __name__ == "__main__":
    try:
        run_stdio()
    except RuntimeError as exc:
        print(f"nookboard MCP startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
