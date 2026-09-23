#!/usr/bin/env python3
"""COMSOL MCP Server - entrypoint and tool registration.

Server-first MCP bridge for COMSOL Multiphysics. It manages a COMSOL
Multiphysics Server process (or connects to an existing one), then drives the
same server-side model that a COMSOL Desktop client can visualize.
"""

from comsol_mcp._server import mcp, _setup_logging
from comsol_mcp._state import _write_status
from comsol_mcp._tools_connection import register as _reg_connection
from comsol_mcp._tools_workflow import register as _reg_workflow
from comsol_mcp._tools_model import register as _reg_model
from comsol_mcp._tools_params import register as _reg_params
from comsol_mcp._tools_geometry import register as _reg_geometry
from comsol_mcp._tools_snapshot import register as _reg_snapshot
from comsol_mcp._tools_physics import register as _reg_physics
from comsol_mcp._tools_solver import register as _reg_solver
from comsol_mcp._tools_phase1 import register as _reg_phase1
from comsol_mcp._tools_control import register as _reg_control
from comsol_mcp._g2_tools import register as _reg_g2
from comsol_mcp._tools_w18 import register as _reg_w18
from comsol_mcp._mcp_gateway import GatewayRegistry

# Register all tools on the shared FastMCP instance at import time.
_gateway = GatewayRegistry(mcp)
_reg_connection(_gateway)
_reg_workflow(_gateway)
_reg_model(_gateway)
_reg_params(_gateway)
_reg_geometry(_gateway)
_reg_snapshot(_gateway)
_reg_physics(_gateway)
_reg_solver(_gateway)
_reg_phase1(_gateway)
_reg_control(_gateway)
_reg_g2(_gateway)
_reg_w18(_gateway)


def main() -> None:
    _setup_logging()
    _write_status({"status": "ready"})
    mcp.run()


if __name__ == "__main__":
    main()
