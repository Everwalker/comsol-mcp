from __future__ import annotations

import binascii
import hashlib
import json
import os
import struct
import zlib
from contextlib import nullcontext
from pathlib import Path

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_registry import registry_describe, validate_call


class _SnapshotAdapter:
    def __init__(self):
        self.server_id = "artifact-preview-test-server"

    def model_snapshot(self, tag):
        return {
            "model_tag": tag,
            "server_instance_id": self.server_id,
            "external_event_counter": 0,
            "fingerprint": "artifact-preview-test-fingerprint",
        }


def _new_daemon(tmp_path: Path):
    project_container = tmp_path / "projects"
    project_container.mkdir(parents=True)
    ledger = SessionLedger("artifact-preview-test-session", "artifact-preview-test-server")
    service = ExecutionService(ledger, _SnapshotAdapter(), project_root=project_container)
    daemon = ControlDaemon(tmp_path / "control", service=service, registry={}, worker=None,
                           project_root=project_container)
    daemon.backend.endpoint_key = "127.0.0.1:2036"
    response = daemon.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": "artifact-preview-project",
            "workspace": "artifact-preview-project",
            "policy": {"permissions": ["inspect", "project_write", "compute"]},
        },
        "execution": {"request_id": "create-preview-project", "idempotency_key": "create-preview-project"},
    })
    assert response["success"] is True
    project = response["data"]["project"]
    project_root = Path(project["workspace"])
    (project_root / "inputs").mkdir(parents=True)
    return daemon, project["project_id"], project_root


def _register(daemon: ControlDaemon, project_id: str, relative: str, *, key: str = "register-preview") -> str:
    response = daemon.dispatch({
        "operation": "artifact.register",
        "arguments": {
            "project_id": project_id,
            "idempotency_key": key,
            "request_id": key + "-request",
            "path": relative,
            "role": "preview_fixture",
            "classification": "synthetic",
        },
        "execution": {"project_id": project_id, "idempotency_key": key, "request_id": key + "-request"},
    })
    assert response["success"] is True
    return response["data"]["artifact_id"]


def _read(daemon, operation, project_id, artifact_id, *, options=None, entrypoint="direct", request_id=None):
    arguments = {"project_id": project_id, "artifact_id": artifact_id}
    if options is not None:
        arguments["options"] = options
    execution = {"project_id": project_id}
    if request_id is not None:
        arguments["request_id"] = request_id
        execution["request_id"] = request_id
    if entrypoint == "direct":
        request = {"operation": operation, "arguments": arguments, "execution": execution}
    else:
        request = {
            "operation": entrypoint,
            "arguments": {"operation_id": operation, "arguments": arguments},
            "execution": execution,
        }
    return daemon.dispatch(request)


def _error_code(response):
    return response.get("error", {}).get("code") or response.get("data", {}).get("error", {}).get("code")


def _store_snapshot(store):
    tables = ("operations", "jobs", "job_events", "artifacts", "revisions", "projects")
    snapshot = {}
    with store.lock:
        for table in tables:
            rows = store.db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            snapshot[table] = [tuple(row) for row in rows]
    return snapshot


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(payload, binascii.crc32(kind)) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _paeth(left: int, up: int, upper_left: int) -> int:
    predictor = left + up - upper_left
    choices = ((abs(predictor - left), left), (abs(predictor - up), up),
               (abs(predictor - upper_left), upper_left))
    return min(choices, key=lambda item: item[0])[1]


def _png_filtered_rows(width: int, rows: list[bytes], *, color_type: int = 6, filters=None) -> bytes:
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    filtered = bytearray()
    prior = bytes(width * channels)
    selected_filters = filters or [0] * len(rows)
    for row, filter_type in zip(rows, selected_filters):
        encoded = bytearray(len(row))
        for index, value in enumerate(row):
            left = row[index - channels] if index >= channels else 0
            up = prior[index]
            upper_left = prior[index - channels] if index >= channels else 0
            predictor = (
                0 if filter_type == 0 else left if filter_type == 1 else up if filter_type == 2
                else (left + up) // 2 if filter_type == 3 else _paeth(left, up, upper_left)
            )
            encoded[index] = (value - predictor) & 0xFF
        filtered.extend(bytes((filter_type,)) + encoded)
        prior = row
    return bytes(filtered)


def _png(width: int, height: int, rows: list[bytes], *, color_type: int = 6,
         filters=None, ancillary: list[tuple[bytes, bytes]] | None = None) -> bytes:
    payload = bytearray(b"\x89PNG\r\n\x1a\n")
    payload.extend(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)))
    for kind, data in ancillary or []:
        payload.extend(_png_chunk(kind, data))
    payload.extend(_png_chunk(b"IDAT", zlib.compress(_png_filtered_rows(width, rows, color_type=color_type, filters=filters), 9)))
    payload.extend(_png_chunk(b"IEND", b""))
    return bytes(payload)


def _independent_decode_rgba_png(payload: bytes):
    """Independent checker for the production encoder's filter-0 RGBA output."""
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    chunks = []
    idat = bytearray()
    while offset < len(payload):
        length = struct.unpack_from(">I", payload, offset)[0]
        kind = payload[offset + 4:offset + 8]
        start = offset + 8
        end = start + length
        expected_crc = struct.unpack_from(">I", payload, end)[0]
        assert (binascii.crc32(payload[start:end], binascii.crc32(kind)) & 0xFFFFFFFF) == expected_crc
        chunks.append(kind)
        if kind == b"IHDR":
            width, height, depth, color, compression, filter_method, interlace = struct.unpack(">IIBBBBB", payload[start:end])
            assert (depth, color, compression, filter_method, interlace) == (8, 6, 0, 0, 0)
        elif kind == b"IDAT":
            idat.extend(payload[start:end])
        elif kind == b"IEND":
            assert length == 0 and end + 4 == len(payload)
        offset = end + 4
    raw = zlib.decompress(idat)
    stride = width * 4
    pixels = bytearray()
    for row in range(height):
        start = row * (stride + 1)
        assert raw[start] == 0
        pixels.extend(raw[start + 1:start + 1 + stride])
    return width, height, bytes(pixels), chunks


def _expected_sample(source_rgba: bytes, width: int, height: int, target_width: int, target_height: int) -> bytes:
    pixels = bytearray()
    for target_y in range(target_height):
        source_y = min(height - 1, ((2 * target_y + 1) * height) // (2 * target_height))
        for target_x in range(target_width):
            source_x = min(width - 1, ((2 * target_x + 1) * width) // (2 * target_width))
            offset = (source_y * width + source_x) * 4
            pixels.extend(source_rgba[offset:offset + 4])
    return bytes(pixels)


def _compressed_zero_png(width: int, height: int, *, extra_decompressed: int = 0) -> bytes:
    row_bytes = width * 4 + 1
    compressor = zlib.compressobj(level=1)
    compressed = bytearray()
    row = bytes(row_bytes)
    for _ in range(height):
        compressed.extend(compressor.compress(row))
    if extra_decompressed:
        chunk = bytes(64 * 1024)
        remaining = extra_decompressed
        while remaining:
            part = chunk[:min(remaining, len(chunk))]
            compressed.extend(compressor.compress(part))
            remaining -= len(part)
    compressed.extend(compressor.flush())
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + _png_chunk(b"IDAT", bytes(compressed))
            + _png_chunk(b"IEND", b""))


def test_registered_png_preview_routes_decode_resize_and_return_real_pixels(tmp_path, monkeypatch):
    from jsonschema import validate

    daemon, project_id, root = _new_daemon(tmp_path)
    width, height = 4, 5
    rows = []
    rgba = bytearray()
    for y in range(height):
        row = bytearray()
        for x in range(width):
            pixel = bytes((x * 40 + y, y * 50 + x, (x + y) * 21, 255 - x * 12 - y * 9))
            row.extend(pixel)
            rgba.extend(pixel)
        rows.append(bytes(row))
    source_bytes = _png(width, height, rows, filters=[0, 1, 2, 3, 4])
    source_path = root / "inputs" / "real-pixels.png"
    source_path.write_bytes(source_bytes)
    try:
        artifact_id = _register(daemon, project_id, "inputs/real-pixels.png")
        # A successful artifact preview is a pure host control read and needs no Worker.
        daemon.backend.service = None
        daemon.backend.worker = None
        baseline = _store_snapshot(daemon.store)
        options = {"schema_version": 1, "kind": "image", "max_width": 2, "max_height": 2}
        direct = _read(daemon, "artifact_preview", project_id, artifact_id, options=options)
        registry = _read(daemon, "artifact.preview", project_id, artifact_id, options=options, entrypoint="registry_call")
        operation = _read(daemon, "artifact.preview", project_id, artifact_id, options=options, entrypoint="operation_call")
        canonical = _read(daemon, "artifact.preview", project_id, artifact_id, options=options)
        for response in (direct, registry, operation, canonical):
            assert response["success"] is True
            data = response["data"]
            assert data["artifact_id"] == artifact_id
            assert data["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
            assert data["media_type"] == "image/png"
            assert data["original_width"] == width and data["original_height"] == height
            assert data["preview_width"] == data["preview_height"] == 2
            assert data["downsampled"] is True
            assert data["sampling_method"] == "nearest_source_pixel_center"
            assert data["provenance"]["pinned_descriptor_sha256_verified_before_and_after"] is True
            preview_png = __import__("base64").b64decode(data["preview_png_base64"], validate=True)
            assert hashlib.sha256(preview_png).hexdigest() == data["preview_sha256"]
            decoded_width, decoded_height, decoded_rgba, chunks = _independent_decode_rgba_png(preview_png)
            assert (decoded_width, decoded_height) == (2, 2)
            assert b"IDAT" in chunks and chunks[-1] == b"IEND"
            assert decoded_rgba == _expected_sample(bytes(rgba), width, height, 2, 2)
            validate(instance=data, schema=registry_describe("artifact.preview")["data_schema"])
        assert direct["data"]["preview_png_base64"] == registry["data"]["preview_png_base64"]
        assert registry["data"]["preview_png_base64"] == operation["data"]["preview_png_base64"]
        assert source_path.read_bytes() == source_bytes
        assert _store_snapshot(daemon.store) == baseline

        capture_dir = os.environ.get("ARTIFACT_PREVIEW_CAPTURE_DIR")
        if capture_dir:
            output = Path(capture_dir)
            output.mkdir(parents=True, exist_ok=True)
            actual_png = __import__("base64").b64decode(direct["data"]["preview_png_base64"], validate=True)
            (output / "source_fixture.png").write_bytes(source_bytes)
            (output / "preview_from_control_daemon.png").write_bytes(actual_png)
            _, _, actual_pixels, _ = _independent_decode_rgba_png(actual_png)
            report = {
                "schema": "artifact-preview-independent-pixel-check/1",
                "artifact_id": artifact_id,
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "preview_sha256": hashlib.sha256(actual_png).hexdigest(),
                "source_dimensions": [width, height],
                "preview_dimensions": [2, 2],
                "sampled_source_coordinates_xy": [[1, 1], [3, 1], [1, 3], [3, 3]],
                "expected_rgba_hex": _expected_sample(bytes(rgba), width, height, 2, 2).hex(),
                "observed_rgba_hex": actual_pixels.hex(),
                "independent_png_crc_and_pixel_check": "PASS",
            }
            (output / "pixel_verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    finally:
        daemon.close()


def test_csv_preview_uniformly_samples_real_multiline_unicode_and_formula_strings(tmp_path):
    from jsonschema import validate

    daemon, project_id, root = _new_daemon(tmp_path)
    content = ('id,温度 [°C],备注\r\n'
               '1,22.5,"=SUM(1,2)"\r\n'
               '2,,empty\r\n'
               '3,20,middle\r\n'
               '4,"","line1\nline2, ""quoted"" ☃"\r\n').encode("utf-8")
    (root / "inputs" / "table.csv").write_bytes(content)
    try:
        artifact_id = _register(daemon, project_id, "inputs/table.csv")
        daemon.backend.service = None
        response = _read(daemon, "artifact.preview", project_id, artifact_id, options={
            "kind": "data", "max_rows": 2, "max_columns": 3, "row_selection": "uniform",
        })
        assert response["success"] is True
        data = response["data"]
        assert data["media_type"] == "text/csv"
        assert data["columns"] == ["id", "温度 [°C]", "备注"]
        assert data["units"] == [None, None, None]
        assert data["columns_origin"] == "csv_header"
        assert data["rows"] == [["1", "22.5", "=SUM(1,2)"], ["4", "", 'line1\nline2, "quoted" ☃']]
        assert data["sampling"] == {
            "row_selection": "uniform", "column_selection": "first", "total_rows": 4,
            "total_columns": 3, "selected_row_indices": [0, 3],
            "selected_column_indices": [0, 1, 2], "truncated": True,
        }
        assert data["downsampled"] is True and data["formula_strings_are_data"] is True
        validate(instance=data, schema=registry_describe("artifact.preview")["data_schema"])
    finally:
        daemon.close()


def test_json_table_preview_preserves_scalars_units_and_first_indices(tmp_path):
    from jsonschema import validate

    daemon, project_id, root = _new_daemon(tmp_path)
    payload = {
        "columns": ["温度", "enabled", "label"],
        "units": ["K", None, "raw"],
        "rows": [[298.15, True, "✓"], [None, False, ""], [301, True, "last"]],
    }
    source = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (root / "inputs" / "table.json").write_bytes(source)
    try:
        artifact_id = _register(daemon, project_id, "inputs/table.json")
        daemon.backend.service = None
        response = _read(daemon, "artifact.preview", project_id, artifact_id, options={
            "kind": "data", "max_rows": 2, "max_columns": 2, "row_selection": "first",
        }, request_id="json-preview-request")
        assert response["success"] is True
        data = response["data"]
        assert data["media_type"] == "application/json"
        assert data["columns"] == ["温度", "enabled"]
        assert data["units"] == ["K", None]
        assert data["rows"] == [[298.15, True], [None, False]]
        assert data["sampling"]["selected_row_indices"] == [0, 1]
        assert data["sampling"]["selected_column_indices"] == [0, 1]
        assert data["sampling"]["total_rows"] == 3 and data["sampling"]["total_columns"] == 3
        assert data["sampling"]["truncated"] is True
        assert data["request_id"] == "json-preview-request"
        validate(instance=data, schema=registry_describe("artifact.preview")["data_schema"])
    finally:
        daemon.close()


def test_registry_exposes_closed_preview_schemas_without_editing_catalog():
    from jsonschema import ValidationError, validate

    descriptor = registry_describe("artifact.preview")
    assert descriptor["implementation_status"] == "SUPPORTED_UNVERIFIED"
    assert descriptor["runtime_dispatch_contract"]["engine_queue"].startswith("bypassed")
    validate(instance={"project_id": "p", "artifact_id": "a" * 64,
                       "options": {"schema_version": 1, "kind": "image", "max_width": 512}},
             schema=descriptor["input_schema"])
    with pytest.raises(ValidationError):
        validate(instance={"project_id": "p", "artifact_id": "a" * 64,
                           "options": {"kind": "image", "unsafe": True}},
                 schema=descriptor["input_schema"])
    catalog = json.loads(Path("comsol_mcp/data/g2/02_ACTION_CATALOG.json").read_text(encoding="utf-8"))
    source_entry = next(item for item in catalog["operations"] if item["operation_id"] == "artifact.preview")
    assert source_entry["implementation_status"] == "PROPOSED_NOT_IMPLEMENTED"


def test_unknown_options_are_refused_before_scoped_metadata_lookup(tmp_path, monkeypatch):
    daemon, project_id, root = _new_daemon(tmp_path)
    source = b"a,b\n1,2\n"
    (root / "inputs" / "table.csv").write_bytes(source)
    try:
        artifact_id = _register(daemon, project_id, "inputs/table.csv")
        monkeypatch.setattr(daemon.store, "get_project_artifact_metadata",
                            lambda *_args, **_kwargs: pytest.fail("metadata lookup preceded option validation"))
        response = _read(daemon, "artifact.preview", project_id, artifact_id,
                         options={"unknown": True}, entrypoint="registry_call")
        assert response["success"] is False
        assert _error_code(response) == "INVALID_REQUEST"
    finally:
        daemon.close()


def test_permission_is_checked_before_preview_metadata_or_file_access(tmp_path, monkeypatch):
    daemon, project_id, root = _new_daemon(tmp_path)
    (root / "inputs" / "table.csv").write_bytes(b"a,b\n1,2\n")
    try:
        artifact_id = _register(daemon, project_id, "inputs/table.csv")
        daemon.project_authority.permission_provider = lambda: set()
        monkeypatch.setattr(daemon.store, "get_project_artifact_metadata",
                            lambda *_args, **_kwargs: pytest.fail("artifact metadata lookup preceded permission denial"))
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "PERMISSION_DENIED"
    finally:
        daemon.close()


def test_same_hash_from_another_project_does_not_authorize_preview(tmp_path):
    daemon, project_id, root = _new_daemon(tmp_path)
    source = b"a,b\n1,2\n"
    (root / "inputs" / "table.csv").write_bytes(source)
    try:
        artifact_id = _register(daemon, project_id, "inputs/table.csv")
        other = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "foreign-project", "workspace": "foreign-project",
                          "policy": {"permissions": ["inspect", "project_write"]}},
            "execution": {"request_id": "create-foreign-project", "idempotency_key": "create-foreign-project"},
        })
        assert other["success"] is True
        other_id = other["data"]["project"]["project_id"]
        foreign_root = Path(other["data"]["project"]["workspace"])
        (foreign_root / "inputs").mkdir()
        (foreign_root / "inputs" / "same-bytes.csv").write_bytes(source)
        response = _read(daemon, "artifact.preview", other_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "ARTIFACT_NOT_FOUND"
        assert hashlib.sha256((foreign_root / "inputs" / "same-bytes.csv").read_bytes()).hexdigest() == artifact_id
    finally:
        daemon.close()


@pytest.mark.parametrize("field,value", [
    ("inode", 9_999_999),
    ("mtime_ns", 9_999_999),
])
def test_stale_registered_identity_is_rejected(tmp_path, field, value):
    from comsol_mcp._artifact_store import local_artifact_host_identity, local_engine_host_identity

    daemon, project_id, root = _new_daemon(tmp_path)
    (root / "inputs" / "table.csv").write_bytes(b"a,b\n1,2\n")
    try:
        artifact_id = _register(daemon, project_id, "inputs/table.csv")
        record = daemon.store.get_metadata("artifacts", artifact_id)
        altered = json.loads(json.dumps(record))
        altered["file_identity"][field] = value
        with daemon.store.lock:
            daemon.store.db.execute("UPDATE artifacts SET metadata=? WHERE artifact_id=?",
                                    (json.dumps(altered, sort_keys=True), artifact_id))
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "ARTIFACT_IDENTITY_MISMATCH"
        assert str(root) not in json.dumps(response)
        assert daemon.store.get_metadata("artifacts", artifact_id) == altered
    finally:
        daemon.close()


@pytest.mark.parametrize("replacement", ["symlink", "hardlink"])
def test_symlink_and_hardlink_replacements_are_rejected(tmp_path, replacement):
    daemon, project_id, root = _new_daemon(tmp_path)
    source_path = root / "inputs" / "identity.csv"
    source_path.write_bytes(b"a,b\n1,2\n")
    try:
        artifact_id = _register(daemon, project_id, "inputs/identity.csv")
        record = daemon.store.get_metadata("artifacts", artifact_id)
        managed = root / record["path"]
        managed.unlink()
        if replacement == "symlink":
            outside = tmp_path / "outside.csv"
            outside.write_bytes(source_path.read_bytes())
            managed.symlink_to(outside)
            expected = "ACCESS_VIOLATION"
        else:
            os.link(source_path, managed)
            expected = "ARTIFACT_IDENTITY_MISMATCH"
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == expected
        assert str(root) not in json.dumps(response)
    finally:
        daemon.close()


def test_source_change_during_parse_is_detected_after_preview(tmp_path, monkeypatch):
    import comsol_mcp._artifact_preview_io as preview_io

    daemon, project_id, root = _new_daemon(tmp_path)
    source_path = root / "inputs" / "during-parse.csv"
    source_path.write_bytes(b"a,b\n1,2\n")
    try:
        artifact_id = _register(daemon, project_id, "inputs/during-parse.csv")
        record = daemon.store.get_metadata("artifacts", artifact_id)
        managed = root / record["path"]
        original_build = preview_io.build_preview_data

        def mutate_after_pinned_read(source, **kwargs):
            managed.write_bytes(b"a,b\n9,8\n")
            return original_build(source, **kwargs)

        monkeypatch.setattr(preview_io, "build_preview_data", mutate_after_pinned_read)
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "ARTIFACT_CHANGED"
        assert str(root) not in json.dumps(response)
    finally:
        daemon.close()


def test_source_size_budget_refuses_before_format_parsing(tmp_path):
    daemon, project_id, root = _new_daemon(tmp_path)
    source_path = root / "inputs" / "over-budget.csv"
    source_path.write_bytes(b"x" * (16 * 1024 * 1024 + 1))
    try:
        artifact_id = _register(daemon, project_id, "inputs/over-budget.csv")
        daemon.backend.service = None
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "INCOMPLETE"
        assert "absolute" not in response["error"]["message"].casefold()
    finally:
        daemon.close()


def test_preview_result_budget_returns_incomplete_without_partial_success(tmp_path):
    daemon, project_id, root = _new_daemon(tmp_path)
    content = "value\n" + ("x" * 6000 + "\n") * 430
    source = content.encode("utf-8")
    (root / "inputs" / "oversized-result.csv").write_bytes(source)
    try:
        artifact_id = _register(daemon, project_id, "inputs/oversized-result.csv")
        daemon.backend.service = None
        response = _read(daemon, "artifact.preview", project_id, artifact_id,
                         options={"kind": "data", "max_rows": 500, "max_columns": 1})
        assert response["success"] is False
        assert _error_code(response) == "INCOMPLETE"
        assert "data" not in response or "rows" not in response.get("data", {})
    finally:
        daemon.close()


@pytest.mark.parametrize("payload,expected", [
    (b'{"columns":["x"],"rows":[[1]],"columns":["y"]}', "ARTIFACT_FORMAT_INVALID"),
    (b'{"columns":["x"],"rows":[[NaN]]}', "ARTIFACT_FORMAT_INVALID"),
    (b'{"columns":["x","y"],"rows":[[1]]}', "ARTIFACT_FORMAT_INVALID"),
    (b'{"nested":{"x":1}}', "UNSUPPORTED_FORMAT"),
])
def test_json_format_and_table_shape_failures_are_typed(tmp_path, payload, expected):
    daemon, project_id, root = _new_daemon(tmp_path)
    (root / "inputs" / "invalid.json").write_bytes(payload)
    try:
        artifact_id = _register(daemon, project_id, "inputs/invalid.json")
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == expected
        assert str(root) not in json.dumps(response)
        assert payload.decode("utf-8") not in json.dumps(response)
    finally:
        daemon.close()


@pytest.mark.parametrize("payload", [
    b"a,b\n1\n",
    b'a,b\n1,"unterminated\n',
])
def test_csv_inconsistent_width_and_malformed_quotes_fail_truthfully(tmp_path, payload):
    daemon, project_id, root = _new_daemon(tmp_path)
    (root / "inputs" / "invalid.csv").write_bytes(payload)
    try:
        artifact_id = _register(daemon, project_id, "inputs/invalid.csv")
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "ARTIFACT_FORMAT_INVALID"
        assert payload.decode("utf-8") not in json.dumps(response)
    finally:
        daemon.close()


def test_png_crc_unknown_critical_and_format_mismatch_are_typed(tmp_path):
    daemon, project_id, root = _new_daemon(tmp_path)
    rgba = bytes((10, 20, 30, 40))
    source_png = _png(1, 1, [rgba])
    bad_crc = bytearray(source_png)
    bad_crc[-1] ^= 1
    bad_png = bytes(bad_crc)
    unknown_critical = (b"\x89PNG\r\n\x1a\n"
                        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                        + _png_chunk(b"ABCD", b"\x00")
                        + _png_chunk(b"IDAT", zlib.compress(b"\x00" + rgba))
                        + _png_chunk(b"IEND", b""))
    cases = [(bad_png, "bad-crc.png", "ARTIFACT_FORMAT_INVALID"),
             (unknown_critical, "unknown-critical.png", "UNSUPPORTED_FORMAT"),
             (b"a,b\n1,2\n", "mismatch.png", "ARTIFACT_FORMAT_MISMATCH")]
    try:
        registered = []
        for index, (source, name, expected) in enumerate(cases):
            (root / "inputs" / name).write_bytes(source)
            artifact_id = _register(daemon, project_id, "inputs/" + name, key=f"register-invalid-png-{index}")
            registered.append((artifact_id, source, expected))
        daemon.backend.service = None
        for artifact_id, source, expected in registered:
            response = _read(daemon, "artifact.preview", project_id, artifact_id)
            assert response["success"] is False
            assert _error_code(response) == expected
            assert source[:30].hex() not in json.dumps(response)
    finally:
        daemon.close()


def test_png_actual_expansion_and_dimension_limits_stop_before_unbounded_decode(tmp_path):
    daemon, project_id, root = _new_daemon(tmp_path)
    too_large_expanded = _compressed_zero_png(4096, 4096)
    too_large_actual = _compressed_zero_png(1, 1, extra_decompressed=65 * 1024 * 1024)
    cases = [(too_large_expanded, "expanded.png", "INCOMPLETE"),
             (too_large_actual, "bomb.png", "INCOMPLETE")]
    try:
        registered = []
        for index, (source, name, expected) in enumerate(cases):
            (root / "inputs" / name).write_bytes(source)
            artifact_id = _register(daemon, project_id, "inputs/" + name, key=f"register-png-budget-{index}")
            registered.append((artifact_id, expected))
        daemon.backend.service = None
        for artifact_id, expected in registered:
            response = _read(daemon, "artifact.preview", project_id, artifact_id)
            assert response["success"] is False
            assert _error_code(response) == expected
    finally:
        daemon.close()


def test_png_decoder_supports_all_declared_8bit_color_modes_and_trns():
    from comsol_mcp._g2_artifact_preview import _decode_png_and_sample, normalize_options

    options = normalize_options({"kind": "image", "max_width": 8, "max_height": 8})
    gray = _png(2, 1, [bytes((15, 25))], color_type=0, ancillary=[(b"tRNS", b"\x00\x0f")])
    rgb = _png(1, 1, [bytes((10, 20, 30))], color_type=2, ancillary=[(b"tRNS", b"\x00\x0a\x00\x14\x00\x1e")])
    palette = (b"\x89PNG\r\n\x1a\n"
               + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 3, 0, 0, 0))
               + _png_chunk(b"PLTE", bytes((1, 2, 3, 4, 5, 6)))
               + _png_chunk(b"tRNS", bytes((0, 128)))
               + _png_chunk(b"IDAT", zlib.compress(_png_filtered_rows(2, [bytes((0, 1))], color_type=3)))
               + _png_chunk(b"IEND", b""))
    gray_alpha = _png(1, 1, [bytes((77, 88))], color_type=4)
    rgba = _png(1, 1, [bytes((11, 22, 33, 44))], color_type=6)
    fixtures = [
        (gray, bytes((15, 15, 15, 0, 25, 25, 25, 255))),
        (rgb, bytes((10, 20, 30, 0))),
        (palette, bytes((1, 2, 3, 0, 4, 5, 6, 128))),
        (gray_alpha, bytes((77, 77, 77, 88))),
        (rgba, bytes((11, 22, 33, 44))),
    ]
    for fixture, expected in fixtures:
        width, height, pixels = _decode_png_and_sample(fixture, max_width=options["max_width"], max_height=options["max_height"])
        assert pixels == expected


def test_png_bomb_dimensional_pixel_count_limit_is_typed(tmp_path):
    daemon, project_id, root = _new_daemon(tmp_path)
    source = _compressed_zero_png(8192, 2049)
    (root / "inputs" / "too-many-pixels.png").write_bytes(source)
    try:
        artifact_id = _register(daemon, project_id, "inputs/too-many-pixels.png")
        daemon.backend.service = None
        response = _read(daemon, "artifact.preview", project_id, artifact_id)
        assert response["success"] is False
        assert _error_code(response) == "INCOMPLETE"
    finally:
        daemon.close()
