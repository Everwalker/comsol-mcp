#!/usr/bin/env python3
"""Source-bound G01 metadata observation; imports production code, never runs main."""
from __future__ import annotations

import argparse
import asyncio
import asyncio.selector_events
import builtins
import hashlib
import importlib
import inspect
import json
import os
import socket
import stat
import sys
import threading
from urllib.parse import urljoin, urlsplit
from pathlib import Path
from typing import Any

_INITIAL_SYS_PATH = list(sys.path)


def _bootstrap_binding(argv: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read the CLI binding, then freeze -I's exact sys.path before support imports."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--binding-sha256", required=True)
    args, _ = parser.parse_known_args(argv)
    path = Path(args.binding)
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("current binding must be a regular non-symlink file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    os.set_inheritable(fd, False)
    try:
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
            raise RuntimeError("current binding identity changed before read")
        parts: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            parts.append(chunk)
        raw = b"".join(parts)
        after_fd = os.fstat(fd)
        after_path = path.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_mode)
        if identity != (after_fd.st_dev, after_fd.st_ino, after_fd.st_size, after_fd.st_mtime_ns, after_fd.st_mode):
            raise RuntimeError("current binding changed while reading")
        if identity != (after_path.st_dev, after_path.st_ino, after_path.st_size, after_path.st_mtime_ns, after_path.st_mode):
            raise RuntimeError("current binding path changed while reading")
        if len(raw) != opened.st_size or hashlib.sha256(raw).hexdigest() != args.binding_sha256:
            raise RuntimeError("current binding length or SHA-256 differs from CLI grant")
    finally:
        os.close(fd)
    binding = json.loads(raw)
    runtime = binding.get("runtime", {})
    expected_sys_path = runtime.get("sys_path")
    if not isinstance(expected_sys_path, list) or _INITIAL_SYS_PATH != expected_sys_path:
        raise RuntimeError("isolated interpreter sys.path differs from current binding")
    harness_root = Path(binding.get("harness_root", "")).resolve(strict=True)
    source_root = Path(binding.get("source", {}).get("root", "")).resolve(strict=True)
    if Path(__file__).resolve(strict=True).parent != harness_root:
        raise RuntimeError("harness script is outside the bound harness root")
    if Path(args.binding).resolve(strict=True) != path.absolute():
        raise RuntimeError("binding path contains a symlink component")
    fixed_path = [*expected_sys_path, str(harness_root), str(source_root)]
    if len(fixed_path) != len(set(fixed_path)):
        raise RuntimeError("fixed sys.path contains duplicate roots")
    sys.path[:] = fixed_path
    sys.dont_write_bytecode = True
    ref = {
        "path": str(path), "size_bytes": len(raw), "sha256": args.binding_sha256,
        "device": opened.st_dev, "inode": opened.st_ino, "mtime_ns": opened.st_mtime_ns,
        "mode": opened.st_mode,
    }
    return binding, ref


_BOOT_BINDING, _BOOT_BINDING_REF = _bootstrap_binding(sys.argv[1:])
from t038_support import (  # noqa: E402
    NotReady,
    PROVIDER_IMPORT_ROOTS,
    install_effect_guard,
    install_import_origin_guard,
    json_bytes,
    read_json_bound,
    verify_bound_files,
    verify_runtime_binding,
    verify_source_binding,
    write_new_verified,
)


def _deny_thread_start(*_args: Any, **_kwargs: Any) -> None:
    raise NotReady("G01 metadata observation attempted to start a thread")


def _safe_schema(schema: Any) -> dict[str, Any]:
    import jsonschema
    from referencing import Registry, Resource
    from referencing.exceptions import NoSuchResource
    from referencing.jsonschema import DRAFT202012, specification_with

    if not isinstance(schema, dict):
        raise NotReady("observed schema is not a JSON object")
    validator = jsonschema.validators.validator_for(schema)
    validator.check_schema(schema)
    dialect = schema.get("$schema")
    try:
        specification = specification_with(dialect, default=DRAFT202012) if isinstance(dialect, str) else DRAFT202012
    except Exception as exc:
        raise NotReady("schema dialect has no installed referencing specification") from exc

    def deny_retrieval(uri: str):
        raise NoSuchResource(ref=uri)

    try:
        root_resource = Resource.from_contents(schema, default_specification=specification)
        root_uri = root_resource.id() or ""
        registry = Registry(retrieve=deny_retrieval).with_resource(root_uri, root_resource).crawl()
        resolver = registry.resolver_with_root(root_resource)
    except Exception as exc:
        raise NotReady("schema could not create a local-only reference registry") from exc

    resolved_refs: list[dict[str, str]] = []
    visited: set[tuple[int, str]] = set()

    def walk(current: Any, current_resolver: Any, base_uri: str) -> None:
        if not isinstance(current, dict):
            return
        marker = (id(current), base_uri)
        if marker in visited:
            return
        visited.add(marker)
        ref = current.get("$ref")
        if ref is not None:
            if not isinstance(ref, str):
                raise NotReady("schema contains a non-string $ref")
            parts = urlsplit(ref)
            if (ref and not ref.startswith("#")) or parts.scheme or parts.netloc or parts.path or parts.query:
                raise NotReady("schema contains a non-local $ref")
            try:
                current_resolver.lookup(ref)
            except Exception as exc:
                raise NotReady(f"local schema reference does not resolve: {ref!r}") from exc
            resolved_refs.append({"ref": ref, "base_uri": base_uri})
        try:
            children = specification.subresources_of(current)
            for child in children:
                child_resource = Resource.from_contents(child, default_specification=specification)
                child_resolver = current_resolver.in_subresource(child_resource)
                child_id = child_resource.id()
                child_base = urljoin(base_uri, child_id) if child_id is not None else base_uri
                walk(child, child_resolver, child_base)
        except NotReady:
            raise
        except Exception as exc:
            raise NotReady("schema subresource traversal failed") from exc

    walk(schema, resolver, root_uri)
    return {
        "validator": f"{validator.__module__}.{validator.__name__}",
        "reference_count": len(resolved_refs),
        "references": resolved_refs,
        "retrieval": "DENIED",
        "cycle_policy": "each schema object/base pair visited once; references are resolved but targets are not recursively expanded",
    }


def _callable_mapping_rows(registrations: list[dict[str, Any]], expected_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected_by_name = {row["name"]: row for row in expected_rows}
    actual_by_name = {row["public_name"]: row for row in registrations}
    mismatches: list[dict[str, Any]] = []
    joined: list[dict[str, Any]] = []
    for name in sorted(set(expected_by_name) | set(actual_by_name)):
        expected = expected_by_name.get(name)
        actual = actual_by_name.get(name)
        mismatch_reasons: list[str] = []
        if expected is None:
            mismatch_reasons.append("unexpected_public_name")
        if actual is None:
            mismatch_reasons.append("missing_public_name")
        if expected is not None and actual is not None:
            if actual["callable"] != expected.get("callable"):
                mismatch_reasons.append("callable_identity")
            if actual["source"] != expected.get("source"):
                mismatch_reasons.append("source_path")
            expected_operation = expected.get("operation_id") or name
            if actual["operation"] != expected_operation:
                mismatch_reasons.append("operation_mapping")
        row = {
            "public_name": name,
            "expected_callable": expected.get("callable") if expected else None,
            "actual_callable": actual.get("callable") if actual else None,
            "expected_source": expected.get("source") if expected else None,
            "actual_source": actual.get("source") if actual else None,
            "expected_operation": (expected.get("operation_id") or name) if expected else None,
            "actual_operation": actual.get("operation") if actual else None,
            "function_definition_line": actual.get("function_definition_line") if actual else None,
            "static_registration_line": expected.get("line") if expected else None,
            "static_registration_source": expected.get("source") if expected else None,
            "match": not mismatch_reasons,
        }
        joined.append(row)
        if mismatch_reasons:
            mismatches.append({"public_name": name, "reasons": mismatch_reasons, **row})
    return joined, mismatches


def _install_finite_loop_bootstrap(on_loop_bound: Any) -> dict[str, Any]:
    """Permit the owned self-pipe only during the first explicit Runner.get_loop."""
    original_socketpair = socket.socketpair
    original_get_loop = asyncio.Runner.get_loop
    original_loop_init = asyncio.selector_events.BaseSelectorEventLoop.__init__
    state: dict[str, Any] = {
        "armed": False,
        "used": False,
        "runner": None,
        "loop": None,
        "sockets": (),
        "fds": (),
        "original_get_loop": original_get_loop,
        "original_loop_init": original_loop_init,
    }
    expected_code = asyncio.selector_events.BaseSelectorEventLoop._make_self_pipe.__code__

    def guarded_socketpair(*args: Any, **kwargs: Any):
        caller = inspect.currentframe().f_back
        if (
            args or kwargs or not state["armed"] or state["used"] or caller is None
            or caller.f_code is not expected_code
        ):
            raise NotReady("socketpair denied outside the one selector self-pipe construction")
        loop = caller.f_locals.get("self")
        if loop is None:
            raise NotReady("selector self-pipe caller has no loop owner")
        pair = original_socketpair()
        if len(pair) != 2 or any(sock.family != socket.AF_UNIX or sock.type & 0xF != socket.SOCK_STREAM for sock in pair):
            for sock in pair:
                sock.close()
            raise NotReady("selector self-pipe is not the expected local stream pair")
        state.update({"used": True, "loop": loop, "sockets": pair, "fds": tuple(s.fileno() for s in pair)})
        return pair

    def tracked_loop_init(loop: Any, *args: Any, **kwargs: Any) -> None:
        if not state["armed"] or state["loop"] is not None:
            raise NotReady("selector loop construction occurred outside Runner.get_loop")
        original_loop_init(loop, *args, **kwargs)
        if state["loop"] is not loop or loop._ssock is not state["sockets"][0] or loop._csock is not state["sockets"][1]:
            raise NotReady("selector did not retain the exact admitted self-pipe")

    def tracked_get_loop(runner: asyncio.Runner):
        if state["loop"] is not None:
            loop = original_get_loop(runner)
            if loop is not state["loop"]:
                raise NotReady("Runner changed its bound event loop")
            return loop
        if state["armed"] or not isinstance(runner, asyncio.Runner):
            raise NotReady("unexpected nested or non-Runner loop construction")
        state["runner"] = runner
        state["armed"] = True
        socket.socketpair = guarded_socketpair
        try:
            loop = original_get_loop(runner)
            if not state["used"] or state["loop"] is not loop:
                raise NotReady("Runner.get_loop did not construct one observed selector self-pipe")
            if getattr(loop, "_ssock", None) is not state["sockets"][0] or getattr(loop, "_csock", None) is not state["sockets"][1]:
                raise NotReady("Runner loop owner differs from the exact socketpair")
            state["runner_id"] = id(runner)
            state["loop_type"] = f"{type(loop).__module__}.{type(loop).__qualname__}"
            return loop
        finally:
            state["armed"] = False
            socket.socketpair = original_socketpair
            asyncio.Runner.get_loop = original_get_loop
            asyncio.selector_events.BaseSelectorEventLoop.__init__ = original_loop_init  # type: ignore[method-assign]
            if state["loop"] is not None:
                on_loop_bound(state["loop"])

    socket.socketpair = guarded_socketpair
    asyncio.selector_events.BaseSelectorEventLoop.__init__ = tracked_loop_init  # type: ignore[method-assign]
    asyncio.Runner.get_loop = tracked_get_loop  # type: ignore[method-assign]

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event in {
            "socket.connect", "socket.bind", "socket.listen", "socket.getaddrinfo",
            "socket.gethostbyname", "socket.sendto", "subprocess.Popen", "os.system",
            "os.fork", "os.posix_spawn", "ctypes.dlopen", "os.kill", "os.killpg",
            "signal.signal", "signal.set_wakeup_fd",
        } or event.startswith("os.spawn") or event.startswith("os.exec"):
            raise NotReady(f"blocked effect during G01: {event}")
        if event == "socket.__new__":
            frame = inspect.currentframe()
            found = False
            while frame is not None:
                if frame.f_code is guarded_socketpair.__code__:
                    found = True
                    break
                frame = frame.f_back
            if not (state["armed"] and found):
                raise NotReady("unowned socket construction denied during G01")

    sys.addaudithook(audit)
    threading.Thread.start = _deny_thread_start  # type: ignore[method-assign]
    return state


def _verify_loop(state: dict[str, Any], loop: asyncio.AbstractEventLoop) -> dict[str, Any]:
    if not state["used"] or state["loop"] is not loop:
        raise NotReady("FastMCP.list_tools did not use the observed selector loop")
    if getattr(loop, "_ssock", None) is not state["sockets"][0] or getattr(loop, "_csock", None) is not state["sockets"][1]:
        raise NotReady("selector loop does not own the exact admitted self-pipe pair")
    return {
        "loop_type": f"{type(loop).__module__}.{type(loop).__qualname__}",
        "loop_id": id(loop),
        "socket_fds": list(state["fds"]),
        "socket_families": [int(sock.family) for sock in state["sockets"]],
        "bootstrap_pair_count": 1,
        "runner_id": state.get("runner_id"),
        "bootstrap_disarmed_before_observation": not state["armed"],
    }


def main() -> int:
    # Normalize only the macOS startup key observed in the preserved v4 child.
    # Never read or record its value; strict comparison still checks every other key.
    startup_platform_key = "__CF_USER_TEXT_ENCODING"
    startup_platform_key_present = startup_platform_key in os.environ
    os.environ.pop(startup_platform_key, None)
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", required=True)
    parser.add_argument("--binding-sha256", required=True)
    parser.add_argument("--attempt-device", required=True, type=int)
    parser.add_argument("--attempt-inode", required=True, type=int)
    parser.add_argument("--attempt-mode", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    result: dict[str, Any] = {
        "schema": "T038_G01_METADATA_OBSERVER_V1",
        "status": "NOT_READY",
        "scope": "actual production registration and FastMCP metadata; no main or tool call",
        "checks": {},
        "startup_environment_normalization": {
            "key": startup_platform_key,
            "present_at_child_start": startup_platform_key_present,
            "value_read_or_recorded": False,
            "action": "os.environ.pop(key, None) before exact environment comparison and before SDK/product imports",
        },
    }
    try:
        binding, binding_ref = read_json_bound(args.binding, args.binding_sha256)
        if binding_ref != _BOOT_BINDING_REF or binding != _BOOT_BINDING:
            raise NotReady("current binding changed between bootstrap and guarded read")
        if binding.get("schema") != "T038_CURRENT_BINDING_V1":
            raise NotReady("wrong current binding schema")
        source = verify_source_binding(binding)
        runtime = verify_runtime_binding(binding)
        fixed_files = [
            binding["expected_registry"], binding["source_selection"],
            binding["runtime_path_readback"], binding["environment_readback"],
            binding["entrypoint"], binding["gateway"], *binding["sdk_files"],
            *binding.get("runtime_files", []), *binding["harness_files"],
        ]
        fixed_refs = verify_bound_files(fixed_files)
        schema_rows = [row for row in binding["harness_files"] if Path(row["path"]).name == "CURRENT_BINDING_SCHEMA.json"]
        if len(schema_rows) != 1:
            raise NotReady("binding must name exactly one frozen current-binding schema")
        schema_bytes, schema_ref = read_json_bound(schema_rows[0]["path"], schema_rows[0]["sha256"])
        import jsonschema
        jsonschema.Draft202012Validator(schema_bytes).validate(binding)
        expected_registry, expected_ref = read_json_bound(
            binding["expected_registry"]["path"], binding["expected_registry"]["sha256"]
        )
        if (
            expected_registry.get("schema") != "T038_STATIC_EXPECTED_REGISTRY_V1"
            or expected_registry.get("source_root") != binding["source"]["root"]
            or expected_registry.get("source_fingerprint") != source["fingerprint"]
            or expected_registry.get("source_member_count") != source["member_count"]
        ):
            raise NotReady("static registry expectation is not bound to the current 207-member source")
        if expected_registry.get("status") != "PASS_STATIC_EXPECTED_REGISTRY_ONLY":
            # Earlier frozen format versions use descriptive status strings;
            # the records remain data, while their unique all112_rows are used.
            if not isinstance(expected_registry.get("all112_rows"), list):
                raise NotReady("static registry record lacks all112_rows")
        expected_rows = expected_registry.get("all112_rows")
        if not isinstance(expected_rows, list) or len(expected_rows) != 112:
            raise NotReady("expected registry must contain exactly 112 rows")
        expected_names = [row.get("name") for row in expected_rows if isinstance(row, dict)]
        if len(expected_names) != 112 or len(set(expected_names)) != 112:
            raise NotReady("expected registry names are missing or duplicated")

        root = Path(binding["source"]["root"]).resolve(strict=True)
        harness = Path(binding["harness_root"]).resolve(strict=True)
        output_parent = binding["output_parent"]
        parent_path = Path(output_parent["path"]).resolve(strict=True)
        parent_info = parent_path.lstat()
        if (parent_info.st_dev, parent_info.st_ino, parent_info.st_mode) != (
            output_parent["device"], output_parent["inode"], output_parent["mode"]
        ):
            raise NotReady("selected T038 output parent differs from its held identity binding")
        results_root = output.parent
        results_info = results_root.lstat()
        if stat.S_ISLNK(results_info.st_mode) or not stat.S_ISDIR(results_info.st_mode) or results_root.name != "results":
            raise NotReady("G01 output must be written under the dedicated results directory")
        attempt_root = results_root.parent
        attempt_info = attempt_root.lstat()
        if stat.S_ISLNK(attempt_info.st_mode) or not stat.S_ISDIR(attempt_info.st_mode):
            raise NotReady("G01 attempt path must be a real directory")
        if (attempt_info.st_dev, attempt_info.st_ino, attempt_info.st_mode) != (
            args.attempt_device, args.attempt_inode, args.attempt_mode
        ):
            raise NotReady("G01 attempt directory differs from its parent-held identity")
        if attempt_root.resolve(strict=True).parent != parent_path:
            raise NotReady("G01 attempt directory is not directly under the selected output parent")
        if results_root.resolve(strict=True).parent != attempt_root.resolve(strict=True):
            raise NotReady("G01 results directory is not a direct attempt child")
        if output.exists() or output.is_symlink():
            raise NotReady("G01 output must be fresh")
        env_record, env_record_ref = read_json_bound(
            binding["environment_readback"]["path"], binding["environment_readback"]["sha256"]
        )
        runtime_record = env_record.get("runtime_path_metadata", {})
        if (
            runtime_record.get("executable") != binding["runtime"]["executable"]
            or runtime_record.get("prefix") != binding["runtime"]["prefix"]
            or runtime_record.get("python_version") != binding["runtime"]["python_version"]
            or runtime_record.get("sys_path") != binding["runtime"]["sys_path"]
            or runtime_record.get("site_packages") != binding["runtime"]["site_packages"]
            or env_record.get("output_parent") != output_parent
            or env_record.get("fixed_environment") != binding["fixed_environment"]
        ):
            raise NotReady("current environment/output readback differs from the harness binding")
        clean_env = dict(binding["fixed_environment"])
        clean_env.update({
            "HOME": str(attempt_root / "home"),
            "TMPDIR": str(attempt_root / "tmp"),
            "XDG_CACHE_HOME": str(attempt_root / "cache"),
            "XDG_CONFIG_HOME": str(attempt_root / "config"),
            "XDG_DATA_HOME": str(attempt_root / "data"),
            "COMSOL_SERVER_MCP_HOME": str(attempt_root / "server-home"),
        })
        derived_dirs: dict[str, dict[str, Any]] = {}
        derived_dirs["OUTPUT_RESULTS"] = {
            "path": str(results_root), "device": results_info.st_dev, "inode": results_info.st_ino,
            "mode": results_info.st_mode, "mtime_ns": results_info.st_mtime_ns,
        }
        for key in ("HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "COMSOL_SERVER_MCP_HOME"):
            value = Path(clean_env[key])
            info = value.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or value.resolve(strict=True).parent != attempt_root.resolve(strict=True):
                raise NotReady(f"G01 derived environment path is not an owned attempt directory: {key}")
            derived_dirs[key] = {
                "path": str(value), "device": info.st_dev, "inode": info.st_ino,
                "mode": info.st_mode, "mtime_ns": info.st_mtime_ns,
            }
        if dict(os.environ) != clean_env:
            raise NotReady("inherited process environment differs from the frozen values and attempt-owned directories")
        os.environ.clear()
        os.environ.update(clean_env)
        sys.dont_write_bytecode = True
        allowed_import_roots = [str(root), str(harness), *binding["runtime"]["sys_path"]]
        import_guard = install_import_origin_guard(allowed_import_roots, PROVIDER_IMPORT_ROOTS)
        fixed_file_paths = [row["path"] for row in fixed_files]
        effect_guard = install_effect_guard(
            [str(root), str(harness), *binding["runtime"]["sys_path"], args.binding,
             str(results_root), *fixed_file_paths],
            [str(results_root)],
        )

        state = _install_finite_loop_bootstrap(lambda _loop: None)
        registration_rows: list[dict[str, Any]] = []
        gateway_module = importlib.import_module("comsol_mcp._mcp_gateway")
        original_init = gateway_module.GatewayRegistry.__init__
        original_add_tool = gateway_module.GatewayRegistry.add_tool
        original_dispatcher = (original_init.__defaults__ or (None,))[0]
        if original_dispatcher is None:
            raise NotReady("production GatewayRegistry default dispatcher is unavailable")

        def deny_dispatch(*_args: Any, **_kwargs: Any) -> None:
            raise NotReady("G01 observer attempted business dispatch")

        def injecting_init(registry: Any, mcp: Any, dispatcher: Any = original_dispatcher) -> None:
            if dispatcher is not original_dispatcher:
                raise NotReady("production passed an unexpected custom dispatcher")
            original_init(registry, mcp, dispatcher=deny_dispatch)

        def tracked_add_tool(registry: Any, function: Any, **options: Any) -> None:
            public_name = options.get("name") or function.__name__
            operation = options.get("operation_id") or public_name
            source_path = inspect.getsourcefile(function)
            if source_path is None:
                raise NotReady(f"registered callable has no source file: {public_name}")
            try:
                source_relative = Path(source_path).resolve(strict=True).relative_to(root).as_posix()
                _, function_definition_line = inspect.getsourcelines(function)
            except (ValueError, OSError) as exc:
                raise NotReady(f"registered callable origin is outside the bound candidate: {public_name}") from exc
            original_add_tool(registry, function, **options)
            if registry.functions.get(operation) is function:
                registration_rows.append({
                    "public_name": public_name,
                    "operation": operation,
                    "callable": f"{function.__module__}.{function.__qualname__}",
                    "source": source_relative,
                    "function_definition_line": function_definition_line,
                })

        gateway_module.GatewayRegistry.__init__ = injecting_init
        gateway_module.GatewayRegistry.add_tool = tracked_add_tool
        try:
            app = importlib.import_module("comsol_mcp.mcp_server")
        finally:
            gateway_module.GatewayRegistry.__init__ = original_init
            gateway_module.GatewayRegistry.add_tool = original_add_tool
        if Path(app.__file__).resolve(strict=True) != (root / "comsol_mcp" / "mcp_server.py").resolve(strict=True):
            raise NotReady("production entrypoint imported from a different source tree")
        if getattr(app._gateway, "profile", None) != "full":
            raise NotReady("production registry is not using the full profile")
        if app._gateway.dispatcher is not deny_dispatch:
            raise NotReady("business dispatcher was not denied before GatewayRegistry construction")
        observed_tools: list[Any] = []

        async def observe() -> None:
            observed_tools.extend(await app.mcp.list_tools())

        runner = asyncio.Runner()
        loop = None
        try:
            loop = runner.get_loop()
            with runner:
                loop_state = _verify_loop(state, loop)
                if state["armed"]:
                    raise NotReady("selector bootstrap remains armed during metadata observation")
                runner.run(observe())
        finally:
            runner.close()
        if loop is None or not loop.is_closed() or any(sock.fileno() != -1 for sock in state["sockets"]):
            raise NotReady("G01 observer loop or owned self-pipe did not close cleanly")

        tools: list[dict[str, Any]] = []
        schema_reference_checks: list[dict[str, Any]] = []
        for tool in observed_tools:
            raw = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            input_check = _safe_schema(raw.get("inputSchema"))
            output_check = None
            if raw.get("outputSchema") is not None:
                output_check = _safe_schema(raw["outputSchema"])
            schema_reference_checks.append({
                "name": raw.get("name"), "input": input_check, "output": output_check,
            })
            tools.append(raw)
        actual_names = [tool.get("name") for tool in tools]
        name_check = len(actual_names) == len(set(actual_names)) and sorted(actual_names) == sorted(expected_names)
        if not name_check:
            raise NotReady("actual FastMCP names differ from the 112-name static registry")

        callable_rows, callable_mismatches = _callable_mapping_rows(registration_rows, expected_rows)
        callable_check = not callable_mismatches and len(registration_rows) == 112 and name_check
        blocked_roots = {value.casefold() for value in PROVIDER_IMPORT_ROOTS}
        unexpected_provider_modules = sorted(
            name for name in sys.modules if name.partition(".")[0].casefold() in blocked_roots
        )
        if unexpected_provider_modules:
            raise NotReady("provider/native modules appeared in sys.modules")

        attempt_ref = {
            "path": str(attempt_root), "device": attempt_info.st_dev,
            "inode": attempt_info.st_ino, "mode": attempt_info.st_mode,
        }
        results_ref = {
            "path": str(results_root), "device": results_info.st_dev,
            "inode": results_info.st_ino, "mode": results_info.st_mode,
        }
        for ref in (attempt_ref, results_ref):
            info = Path(ref["path"]).lstat()
            if stat.S_ISLNK(info.st_mode) or (info.st_dev, info.st_ino, info.st_mode) != (
                ref["device"], ref["inode"], ref["mode"]
            ):
                raise NotReady("owned G01 output directory identity changed during observation")
        for ref in derived_dirs.values():
            info = Path(ref["path"]).lstat()
            if stat.S_ISLNK(info.st_mode) or (info.st_dev, info.st_ino, info.st_mode) != (
                ref["device"], ref["inode"], ref["mode"]
            ):
                raise NotReady("owned G01 derived directory identity changed during observation")
        current_parent = parent_path.lstat()
        if (current_parent.st_dev, current_parent.st_ino, current_parent.st_mode) != (
            output_parent["device"], output_parent["inode"], output_parent["mode"]
        ):
            raise NotReady("selected output parent identity changed during G01")

        source_after = verify_source_binding(binding)
        fixed_refs_after = verify_bound_files(fixed_files)
        binding_after, binding_ref_after = read_json_bound(args.binding, args.binding_sha256)
        if source_after != source or fixed_refs_after != fixed_refs or binding_after != binding or binding_ref_after != binding_ref:
            raise NotReady("bound source, runtime inputs, harness, or current binding changed during G01")
        result.update({
            "status": "PASS_G01_METADATA_OBSERVER" if callable_check else "FAIL_G01_CALLABLE_ORIGIN_MISMATCH",
            "binding": binding_ref,
            "source": {"root": source["root"], "fingerprint": source["fingerprint"], "member_count": source["member_count"]},
            "runtime": runtime,
            "expected_registry": expected_ref,
            "input_refs": fixed_refs,
            "input_refs_after": fixed_refs_after,
            "source_refs_after": source_after["members"],
            "binding_ref_after": binding_ref_after,
            "binding_schema": schema_ref,
            "output_parent": output_parent,
            "attempt_directory": attempt_ref,
            "results_directory": results_ref,
            "derived_directories": derived_dirs,
            "environment_readback": env_record_ref,
            "profile": "full",
            "tool_count": len(tools),
            "unique_name_set_match": name_check,
            "public_name_callable_match": callable_check,
            "callable_mismatches": callable_mismatches,
            "local_schema_validation": "PASS" if len(tools) == 112 else "FAIL",
            "local_schema_reference_resolution": {
                "status": "PASS",
                "retrieval": "DENIED",
                "reference_count": sum(
                    item[k]["reference_count"]
                    for item in schema_reference_checks for k in ("input", "output")
                    if item[k] is not None
                ),
                "per_tool": schema_reference_checks,
            },
            "loop": loop_state,
            "import_guard": import_guard,
            "effect_guard": effect_guard,
            "environment": clean_env,
            "isolated_sys_path": list(sys.path),
            "tools": tools,
            "registered_callables": callable_rows,
            "acceptance_limits": {
                "original50_compatibility": "OPEN",
                "28_schema_deltas": "OPEN",
                "profile_fallback_business_semantics": "OPEN",
                "MCP_tools_list_multipage": "OPEN",
                "original_T038_protocol_wire": "NOT_RUN",
            },
        })
    except BaseException as exc:
        result["status"] = "FAIL_G01_METADATA_OBSERVER" if not isinstance(exc, NotReady) else "NOT_READY_G01_METADATA_OBSERVER"
        result["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    try:
        write_new_verified(output, json_bytes(result))
    except BaseException as output_exc:
        original_error = result.get("error")
        if isinstance(original_error, dict):
            original_error = {
                "type": str(original_error.get("type", ""))[:1000],
                "message": str(original_error.get("message", ""))[:1000],
            }
        elif original_error is not None:
            original_error = str(original_error)[:1000]
        diagnostic = {
            "schema": "T038_G01_OUTPUT_FAILURE_DIAGNOSTIC_V1",
            "output_exception": {
                "type": type(output_exc).__name__[:1000],
                "message": str(output_exc)[:1000],
            },
            "original_result": {
                "status": str(result.get("status", ""))[:1000],
                "error": original_error,
            },
        }
        try:
            sys.stderr.write("T038_G01_OUTPUT_FAILURE " + json.dumps(diagnostic, sort_keys=True, separators=(",", ":")) + "\n")
        except BaseException:
            sys.stderr.write('T038_G01_OUTPUT_FAILURE {"diagnostic":"serialization_failure"}\n')
        return 3
    if result["status"] != "PASS_G01_METADATA_OBSERVER":
        sys.stderr.write(f"T038_G01 {result['status']}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
