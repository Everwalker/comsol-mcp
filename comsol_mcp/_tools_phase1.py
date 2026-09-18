"""Fixed-recipe MCP entry for the phase-one Java client PoC."""
from pathlib import Path
from comsol_mcp._phase1_runtime import run_v64_poc, DEFAULT_COMSOL_ROOT, DEFAULT_JDK11

def runtime_poc_v64(run_directory: str, known_server_pid: int, known_port: int,
                    preferences_directory: str, comsol_root: str = str(DEFAULT_COMSOL_ROOT),
                    jdk11: str = str(DEFAULT_JDK11)) -> dict:
    """Attach to the registered local test server; never start/stop a user server."""
    return run_v64_poc(Path(run_directory), known_server_pid=known_server_pid, known_port=known_port,
        preferences_directory=preferences_directory, comsol_root=Path(comsol_root), jdk11=Path(jdk11))

def register(mcp_instance):
    mcp_instance.add_tool(runtime_poc_v64)
