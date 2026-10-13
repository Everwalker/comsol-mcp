"""Bounded stdlib previews for schema-v2 registered PNG and table artifacts.

The caller supplies bytes read from the same pinned descriptor that it hashes
before and after this module runs. This module never opens files or executes
content; it only decodes PNG pixels or parses UTF-8 CSV/JSON tables.
"""
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import math
import struct
import zlib
from pathlib import PurePosixPath
from threading import Lock
from typing import Any, Mapping, Sequence

from ._execution_contract import ExecutionContractError


PREVIEW_SCHEMA_VERSION = 1
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_SIDE = 8192
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
MAX_IMAGE_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_DATA_ROWS = 100_000
MAX_DATA_COLUMNS = 4096
MAX_DATA_CELLS = 250_000
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 250_000
MAX_PNG_CHUNKS = 100_000
MAX_CSV_CELL_CHARS = 128 * 1024

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_CRITICAL_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
_CSV_FIELD_LIMIT_LOCK = Lock()


def _error(code: str, message: str, *, stage: str = "preview") -> ExecutionContractError:
    return ExecutionContractError(code, message, stage=stage)


def _invalid_options(message: str) -> ExecutionContractError:
    return _error("INVALID_REQUEST", message, stage="validation")


def normalize_options(value: Any) -> dict[str, Any]:
    """Validate the closed options v1 object before looking up artifact data."""
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise _invalid_options("artifact.preview options must be an object")
    if any(not isinstance(key, str) for key in value):
        raise _invalid_options("artifact.preview option names must be strings")
    allowed = {
        "schema_version", "kind", "max_width", "max_height",
        "max_rows", "max_columns", "row_selection",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _invalid_options("artifact.preview options contain unsupported fields")
    version = value.get("schema_version", PREVIEW_SCHEMA_VERSION)
    if type(version) is not int or version != PREVIEW_SCHEMA_VERSION:
        raise _invalid_options("artifact.preview options schema_version must be 1")
    kind = value.get("kind", "auto")
    if not isinstance(kind, str) or kind not in {"auto", "image", "data"}:
        raise _invalid_options("artifact.preview kind must be auto, image, or data")
    out: dict[str, Any] = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "kind": kind,
        "max_width": _bounded_integer(value, "max_width", 512, 1, 1024),
        "max_height": _bounded_integer(value, "max_height", 512, 1, 1024),
        "max_rows": _bounded_integer(value, "max_rows", 100, 1, 500),
        "max_columns": _bounded_integer(value, "max_columns", 32, 1, 128),
        "row_selection": value.get("row_selection", "first"),
    }
    if not isinstance(out["row_selection"], str) or out["row_selection"] not in {"first", "uniform"}:
        raise _invalid_options("artifact.preview row_selection must be first or uniform")
    if kind == "image" and {"max_rows", "max_columns", "row_selection"}.intersection(value):
        raise _invalid_options("image previews do not accept data sampling options")
    if kind == "data" and {"max_width", "max_height"}.intersection(value):
        raise _invalid_options("data previews do not accept image sizing options")
    return out


def _bounded_integer(value: Mapping[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    candidate = value.get(name, default)
    if type(candidate) is not int or not minimum <= candidate <= maximum:
        raise _invalid_options(f"artifact.preview {name} must be an integer from {minimum} through {maximum}")
    return candidate


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _error("INCOMPLETE", "artifact preview result could not be represented within the output contract") from exc


def _preview_format(source: bytes, record: Mapping[str, Any], options: Mapping[str, Any]) -> str:
    suffix = ""
    registered_path = record.get("path")
    if isinstance(registered_path, str):
        suffix = PurePosixPath(registered_path).suffix.casefold()
    declared = record.get("artifact_type")
    mime = declared.strip().split(";", 1)[0].casefold() if isinstance(declared, str) else ""
    type_format: str | None = None
    if mime == "image/png":
        type_format = "png"
    elif mime == "text/csv":
        type_format = "csv"
    elif mime == "application/json" or (mime.startswith("application/") and mime.endswith("+json")):
        type_format = "json"
    elif mime.startswith("image/") or mime in {"text/html", "application/xhtml+xml", "image/svg+xml", "application/xml", "text/xml"}:
        type_format = "unsupported"

    suffix_format = {".png": "png", ".csv": "csv", ".json": "json"}.get(suffix)
    if suffix in {".html", ".htm", ".svg", ".xml", ".xhtml", ".js", ".mjs", ".pdf", ".jpg", ".jpeg", ".gif", ".webp"}:
        suffix_format = "unsupported"
    if type_format is not None and suffix_format is not None and type_format != suffix_format:
        raise _error("ARTIFACT_FORMAT_MISMATCH", "registered artifact format hints disagree")
    declared_format = type_format or suffix_format

    requested = options["kind"]
    has_png_signature = source.startswith(PNG_SIGNATURE)
    if declared_format == "unsupported":
        if has_png_signature and requested != "data":
            raise _error("ARTIFACT_FORMAT_MISMATCH", "PNG bytes conflict with the registered artifact format")
        raise _error("UNSUPPORTED_FORMAT", "registered artifact format is not supported for preview")
    if has_png_signature:
        if declared_format in {"csv", "json"} or requested == "data":
            raise _error("ARTIFACT_FORMAT_MISMATCH", "PNG bytes do not match the requested data preview")
        return "png"
    if declared_format == "png":
        raise _error("ARTIFACT_FORMAT_MISMATCH", "registered PNG artifact does not contain a PNG signature")
    if requested == "image":
        raise _error("ARTIFACT_FORMAT_MISMATCH", "image preview requires a supported PNG artifact")

    if declared_format in {"csv", "json"}:
        return declared_format
    if requested not in {"auto", "data"}:
        raise _error("INVALID_REQUEST", "artifact.preview kind is invalid")
    text = _decode_utf8(source)
    stripped = text.lstrip()
    if stripped.startswith("<"):
        raise _error("UNSUPPORTED_FORMAT", "HTML, SVG, and XML content is not a previewable artifact format")
    if stripped.startswith(("{", "[")):
        return "json"
    if not _has_unquoted_comma(text):
        raise _error("UNSUPPORTED_FORMAT", "artifact bytes do not identify a supported PNG, CSV, or JSON format")
    return "csv"


def _decode_utf8(source: bytes) -> str:
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _error("UNSUPPORTED_FORMAT", "artifact is not strict UTF-8 tabular data") from exc
    if text.lstrip().startswith("<"):
        raise _error("UNSUPPORTED_FORMAT", "HTML, SVG, and XML content is not a previewable artifact format")
    return text


def _has_unquoted_comma(text: str) -> bool:
    in_quotes = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            if in_quotes and index + 1 < len(text) and text[index + 1] == '"':
                index += 2
                continue
            in_quotes = not in_quotes
        elif character == "," and not in_quotes:
            return True
        index += 1
    return False


def build_preview_data(
    source: bytes,
    *,
    artifact_id: str,
    project_id: str,
    record: Mapping[str, Any],
    options: Mapping[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    if len(source) > MAX_SOURCE_BYTES:
        raise _error("INCOMPLETE", "artifact exceeds the 16 MiB source preview limit")
    if _sha256(source) != artifact_id:
        raise _error("ARTIFACT_CHANGED", "pinned artifact bytes changed while preview data was read")
    kind = _preview_format(source, record, options)
    common = {
        "preview_schema_version": PREVIEW_SCHEMA_VERSION,
        "kind": "image" if kind == "png" else "data",
        "project_id": project_id,
        "artifact_id": artifact_id,
        "source_sha256": artifact_id,
        "source_size_bytes": len(source),
        "media_type": {"png": "image/png", "csv": "text/csv", "json": "application/json"}[kind],
        "role": record["role"],
        "classification": record["classification"],
        "registration_schema_version": 2,
        "provenance": {
            "project_id": project_id,
            "artifact_id": artifact_id,
            "source_sha256": artifact_id,
            "source_size_bytes": len(source),
            "registration_schema_version": 2,
            "project_root_host_engine_scope_verified": True,
            "pinned_descriptor_sha256_verified_before_and_after": True,
        },
    }
    if request_id is not None:
        common["request_id"] = request_id
    if kind == "png":
        image_options = {
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "kind": "image",
            "max_width": options["max_width"],
            "max_height": options["max_height"],
        }
        width, height, pixels = _decode_png_and_sample(
            source, max_width=image_options["max_width"], max_height=image_options["max_height"],
        )
        preview_png = _encode_rgba_png(width, height, pixels)
        preview = {
            **common,
            "options_applied": image_options,
            "preview_media_type": "image/png",
            "original_width": _png_dimensions(source)[0],
            "original_height": _png_dimensions(source)[1],
            "preview_width": width,
            "preview_height": height,
            "downsampled": width < _png_dimensions(source)[0] or height < _png_dimensions(source)[1],
            "sampling_method": "nearest_source_pixel_center",
            "encoded_bytes_unchanged": preview_png == source,
            "preview_sha256": _sha256(preview_png),
            "preview_size_bytes": len(preview_png),
            "preview_png_base64": base64.b64encode(preview_png).decode("ascii"),
        }
    else:
        data_options = {
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "kind": "data",
            "max_rows": options["max_rows"],
            "max_columns": options["max_columns"],
            "row_selection": options["row_selection"],
        }
        if kind == "csv":
            columns, units, rows, origin = _parse_csv_table(source)
        else:
            columns, units, rows, origin = _parse_json_table(source)
        if len(rows) > MAX_DATA_ROWS or len(columns) > MAX_DATA_COLUMNS or len(rows) * len(columns) > MAX_DATA_CELLS:
            raise _error("INCOMPLETE", "tabular artifact exceeds the bounded row, column, or cell budget")
        row_indices = _select_indices(len(rows), data_options["max_rows"], data_options["row_selection"])
        column_indices = list(range(min(len(columns), data_options["max_columns"])))
        selected_rows = [[rows[row_index][column_index] for column_index in column_indices] for row_index in row_indices]
        selected_columns = [columns[index] for index in column_indices]
        selected_units = [units[index] for index in column_indices]
        truncated = len(row_indices) < len(rows) or len(column_indices) < len(columns)
        preview = {
            **common,
            "options_applied": data_options,
            "columns_origin": origin,
            "columns": selected_columns,
            "units": selected_units,
            "rows": selected_rows,
            "sampling": {
                "row_selection": data_options["row_selection"],
                "column_selection": "first",
                "total_rows": len(rows),
                "total_columns": len(columns),
                "selected_row_indices": row_indices,
                "selected_column_indices": column_indices,
                "truncated": truncated,
            },
            "downsampled": truncated,
            "formula_strings_are_data": True,
        }
    result_size = len(_canonical_json({"success": True, "data": preview}))
    if result_size > MAX_RESULT_BYTES:
        raise _error("INCOMPLETE", "encoded artifact preview exceeds the 2 MiB result limit")
    return preview


def _png_dimensions(source: bytes) -> tuple[int, int]:
    if len(source) < 33 or source[:8] != PNG_SIGNATURE or source[12:16] != b"IHDR":
        raise _error("ARTIFACT_FORMAT_INVALID", "PNG signature or initial IHDR chunk is invalid")
    return struct.unpack(">II", source[16:24])


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(chunk_type)
    crc = binascii.crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def _decode_png_and_sample(source: bytes, *, max_width: int, max_height: int) -> tuple[int, int, bytes]:
    if len(source) < 8 or source[:8] != PNG_SIGNATURE:
        raise _error("ARTIFACT_FORMAT_MISMATCH", "image preview requires PNG bytes")
    view = memoryview(source)
    offset = 8
    chunk_count = 0
    width = height = color_type = bit_depth = -1
    palette: bytes | None = None
    transparency: bytes | None = None
    idat: list[memoryview] = []
    seen: set[bytes] = set()
    seen_idat = False
    ended_idat = False
    seen_iend = False
    while offset < len(source):
        chunk_count += 1
        if chunk_count > MAX_PNG_CHUNKS:
            raise _error("INCOMPLETE", "PNG exceeds the bounded chunk count")
        if len(source) - offset < 12:
            raise _error("ARTIFACT_FORMAT_INVALID", "PNG chunk framing is incomplete")
        chunk_length = struct.unpack_from(">I", source, offset)[0]
        chunk_type_start = offset + 4
        chunk_type = bytes(view[chunk_type_start:chunk_type_start + 4])
        chunk_data_start = chunk_type_start + 4
        chunk_data_end = chunk_data_start + chunk_length
        chunk_end = chunk_data_end + 4
        if chunk_end > len(source) or chunk_data_end < chunk_data_start:
            raise _error("ARTIFACT_FORMAT_INVALID", "PNG chunk length is impossible")
        if any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in chunk_type) or chunk_type[2] & 0x20:
            raise _error("ARTIFACT_FORMAT_INVALID", "PNG chunk type code is invalid")
        expected_crc = struct.unpack_from(">I", source, chunk_data_end)[0]
        observed_crc = binascii.crc32(chunk_type)
        observed_crc = binascii.crc32(view[chunk_data_start:chunk_data_end], observed_crc) & 0xFFFFFFFF
        if observed_crc != expected_crc:
            raise _error("ARTIFACT_FORMAT_INVALID", "PNG chunk CRC validation failed")
        payload = view[chunk_data_start:chunk_data_end]

        if chunk_count == 1:
            if chunk_type != b"IHDR" or chunk_length != 13:
                raise _error("ARTIFACT_FORMAT_INVALID", "PNG must begin with one 13-byte IHDR chunk")
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", payload)
            if width < 1 or height < 1:
                raise _error("ARTIFACT_FORMAT_INVALID", "PNG dimensions must be positive")
            if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
                raise _error("INCOMPLETE", "PNG dimensions exceed the bounded pixel limits")
            if bit_depth != 8 or color_type not in {0, 2, 3, 4, 6} or compression != 0 or filter_method != 0 or interlace != 0:
                raise _error("UNSUPPORTED_FORMAT", "PNG requires non-interlaced 8-bit gray, RGB, palette, gray-alpha, or RGBA pixels")
            seen.add(b"IHDR")
        else:
            if chunk_type == b"IHDR" or (chunk_type in {b"PLTE", b"tRNS"} and chunk_type in seen):
                raise _error("ARTIFACT_FORMAT_INVALID", "PNG contains a duplicate singleton chunk")
            if chunk_type not in _PNG_CRITICAL_CHUNKS and not (chunk_type[0] & 0x20):
                raise _error("UNSUPPORTED_FORMAT", "PNG contains an unsupported critical chunk")
            if chunk_type == b"PLTE":
                if (seen_idat or b"tRNS" in seen or color_type in {0, 4}
                        or chunk_length < 3 or chunk_length > 768 or chunk_length % 3):
                    raise _error("ARTIFACT_FORMAT_INVALID", "PNG palette placement or size is invalid")
                palette = payload.tobytes()
            elif chunk_type == b"tRNS":
                if seen_idat or (color_type == 3 and b"PLTE" not in seen):
                    raise _error("ARTIFACT_FORMAT_INVALID", "PNG transparency chunk placement is invalid")
                transparency = payload.tobytes()
            elif chunk_type == b"IDAT":
                if ended_idat:
                    raise _error("ARTIFACT_FORMAT_INVALID", "PNG IDAT chunks must be consecutive")
                seen_idat = True
                idat.append(payload)
            elif chunk_type == b"IEND":
                if chunk_length != 0 or not seen_idat or chunk_end != len(source):
                    raise _error("ARTIFACT_FORMAT_INVALID", "PNG IEND is invalid or trailing content exists")
                seen_iend = True
            elif seen_idat:
                ended_idat = True
            if chunk_type in {b"PLTE", b"tRNS", b"IDAT", b"IEND"}:
                seen.add(chunk_type)
        if chunk_type != b"IDAT" and seen_idat:
            ended_idat = True
        offset = chunk_end
        if seen_iend:
            break
    if not seen_iend or offset != len(source) or not idat:
        raise _error("ARTIFACT_FORMAT_INVALID", "PNG is missing its complete image data or IEND chunk")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    if color_type == 3:
        if palette is None:
            raise _error("ARTIFACT_FORMAT_INVALID", "indexed PNG is missing its palette")
        palette_entries = len(palette) // 3
        if palette_entries > 256:
            raise _error("ARTIFACT_FORMAT_INVALID", "indexed PNG palette is too large")
        if transparency is not None and (not transparency or len(transparency) > palette_entries):
            raise _error("ARTIFACT_FORMAT_INVALID", "indexed PNG transparency table exceeds its palette")
    elif color_type == 0:
        if transparency is not None and (len(transparency) != 2 or int.from_bytes(transparency, "big") > 255):
            raise _error("ARTIFACT_FORMAT_INVALID", "grayscale PNG transparency key is invalid")
    elif color_type == 2:
        if transparency is not None and (len(transparency) != 6 or any(int.from_bytes(transparency[i:i + 2], "big") > 255 for i in (0, 2, 4))):
            raise _error("ARTIFACT_FORMAT_INVALID", "RGB PNG transparency key is invalid")
    elif transparency is not None:
        raise _error("ARTIFACT_FORMAT_INVALID", "PNG tRNS is unsupported for alpha color types")

    row_bytes = width * channels
    expected_raw_bytes = height * (row_bytes + 1)
    if expected_raw_bytes > MAX_IMAGE_EXPANDED_BYTES:
        raise _error("INCOMPLETE", "PNG decoded bytes exceed the 64 MiB expansion limit")
    target_width, target_height = _fit_dimensions(width, height, max_width, max_height)
    sample_x = [min(width - 1, ((2 * x + 1) * width) // (2 * target_width)) for x in range(target_width)]
    sample_y = [min(height - 1, ((2 * y + 1) * height) // (2 * target_height)) for y in range(target_height)]
    sampled_y = {source_y: target_y for target_y, source_y in enumerate(sample_y)}
    raw_rows = _inflate_png(idat, expected_raw_bytes)
    preview_pixels = bytearray(target_width * target_height * 4)
    prior = bytearray(row_bytes)
    scanline_size = row_bytes + 1
    for y in range(height):
        start = y * scanline_size
        filter_type = raw_rows[start]
        filtered = memoryview(raw_rows)[start + 1:start + scanline_size]
        if filter_type > 4:
            raise _error("ARTIFACT_FORMAT_INVALID", "PNG scanline uses an unknown filter")
        current = _unfilter_scanline(filtered, prior, channels, filter_type)
        target_y = sampled_y.get(y)
        if target_y is not None:
            destination = target_y * target_width * 4
            for target_x, source_x in enumerate(sample_x):
                rgba = _pixel_rgba(current, source_x, color_type, palette, transparency)
                pixel_offset = destination + target_x * 4
                preview_pixels[pixel_offset:pixel_offset + 4] = rgba
        prior = current
    return target_width, target_height, bytes(preview_pixels)


def _fit_dimensions(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    if width <= max_width and height <= max_height:
        return width, height
    if max_width * height <= max_height * width:
        target_width = max_width
        target_height = max(1, (height * max_width * 2 + width) // (2 * width))
    else:
        target_height = max_height
        target_width = max(1, (width * max_height * 2 + height) // (2 * height))
    return min(width, max_width, target_width), min(height, max_height, target_height)


def _inflate_png(chunks: Sequence[memoryview], expected: int) -> bytearray:
    inflater = zlib.decompressobj()
    output = bytearray()
    try:
        for payload in chunks:
            cursor = 0
            while cursor < len(payload):
                segment = payload[cursor:cursor + 64 * 1024]
                cursor += len(segment)
                remaining = expected + 1 - len(output)
                if remaining <= 0:
                    raise _error("INCOMPLETE", "PNG decompression exceeds its declared image dimensions")
                output.extend(inflater.decompress(segment, remaining))
                if len(output) > expected:
                    raise _error("INCOMPLETE", "PNG decompression exceeds its declared image dimensions")
                if inflater.unused_data:
                    raise _error("ARTIFACT_FORMAT_INVALID", "PNG zlib stream has trailing data")
                while inflater.unconsumed_tail:
                    tail = inflater.unconsumed_tail
                    remaining = expected + 1 - len(output)
                    if remaining <= 0:
                        raise _error("INCOMPLETE", "PNG decompression exceeds its declared image dimensions")
                    before_tail = len(tail)
                    before_output = len(output)
                    output.extend(inflater.decompress(tail, remaining))
                    if len(output) > expected:
                        raise _error("INCOMPLETE", "PNG decompression exceeds its declared image dimensions")
                    if len(inflater.unconsumed_tail) == before_tail and len(output) == before_output:
                        raise _error("ARTIFACT_FORMAT_INVALID", "PNG zlib stream could not make progress")
        remaining = expected + 1 - len(output)
        if remaining <= 0:
            raise _error("INCOMPLETE", "PNG decompression exceeds its declared image dimensions")
        output.extend(inflater.flush(remaining))
    except zlib.error as exc:
        raise _error("ARTIFACT_FORMAT_INVALID", "PNG zlib stream is invalid") from exc
    if len(output) > expected:
        raise _error("INCOMPLETE", "PNG decompression exceeds its declared image dimensions")
    if len(output) != expected or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise _error("ARTIFACT_FORMAT_INVALID", "PNG decompressed bytes do not match the declared dimensions")
    return output


def _unfilter_scanline(filtered: memoryview, prior: bytearray, bytes_per_pixel: int, filter_type: int) -> bytearray:
    current = bytearray(len(filtered))
    for index, value in enumerate(filtered):
        left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        up = prior[index]
        upper_left = prior[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        if filter_type == 0:
            predictor = 0
        elif filter_type == 1:
            predictor = left
        elif filter_type == 2:
            predictor = up
        elif filter_type == 3:
            predictor = (left + up) // 2
        else:
            predictor = _paeth(left, up, upper_left)
        current[index] = (value + predictor) & 0xFF
    return current


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= up_distance and left_distance <= upper_left_distance:
        return left
    if up_distance <= upper_left_distance:
        return up
    return upper_left


def _pixel_rgba(row: bytearray, x: int, color_type: int, palette: bytes | None, transparency: bytes | None) -> bytes:
    if color_type == 0:
        gray = row[x]
        alpha = 0 if transparency is not None and gray == int.from_bytes(transparency, "big") else 255
        return bytes((gray, gray, gray, alpha))
    if color_type == 2:
        offset = x * 3
        red, green, blue = row[offset:offset + 3]
        transparent = transparency is not None and (red, green, blue) == tuple(int.from_bytes(transparency[i:i + 2], "big") for i in (0, 2, 4))
        return bytes((red, green, blue, 0 if transparent else 255))
    if color_type == 3:
        assert palette is not None
        palette_index = row[x]
        if palette_index >= len(palette) // 3:
            raise _error("ARTIFACT_FORMAT_INVALID", "indexed PNG pixel refers outside its palette")
        offset = palette_index * 3
        alpha = transparency[palette_index] if transparency is not None and palette_index < len(transparency) else 255
        return bytes((palette[offset], palette[offset + 1], palette[offset + 2], alpha))
    if color_type == 4:
        gray, alpha = row[x * 2:x * 2 + 2]
        return bytes((gray, gray, gray, alpha))
    offset = x * 4
    return bytes(row[offset:offset + 4])


def _encode_rgba_png(width: int, height: int, pixels: bytes) -> bytes:
    row_bytes = width * 4
    scanlines = bytearray(height * (row_bytes + 1))
    for y in range(height):
        destination = y * (row_bytes + 1)
        source = y * row_bytes
        scanlines[destination] = 0
        scanlines[destination + 1:destination + row_bytes + 1] = pixels[source:source + row_bytes]
    compressed = zlib.compress(scanlines, level=9)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")


def _parse_csv_table(source: bytes) -> tuple[list[str], list[str | None], list[list[str]], str]:
    text = _decode_utf8(source)
    parsed_rows: list[list[str]] = []
    cell_count = 0
    with _CSV_FIELD_LIMIT_LOCK:
        previous_limit = csv.field_size_limit()
        csv.field_size_limit(MAX_CSV_CELL_CHARS)
        try:
            reader = csv.reader(io.StringIO(text, newline=""), dialect="excel", strict=True)
            for row in reader:
                if not parsed_rows:
                    if not row:
                        raise _error("ARTIFACT_FORMAT_INVALID", "CSV table is missing its header row")
                    if len(row) > MAX_DATA_COLUMNS:
                        raise _error("INCOMPLETE", "CSV table exceeds the 4096-column source limit")
                    parsed_rows.append(row)
                    cell_count += len(row)
                    continue
                if len(row) != len(parsed_rows[0]):
                    raise _error("ARTIFACT_FORMAT_INVALID", "CSV rows do not match the declared header width")
                if any(len(cell) > MAX_CSV_CELL_CHARS for cell in row):
                    raise _error("INCOMPLETE", "CSV cell exceeds the bounded field length")
                parsed_rows.append(row)
                cell_count += len(row)
                if len(parsed_rows) - 1 > MAX_DATA_ROWS or cell_count > MAX_DATA_CELLS:
                    raise _error("INCOMPLETE", "CSV table exceeds the bounded row or cell budget")
        except csv.Error as exc:
            if "field larger than field limit" in str(exc).casefold():
                raise _error("INCOMPLETE", "CSV cell exceeds the bounded field length") from exc
            raise _error("ARTIFACT_FORMAT_INVALID", "CSV quoting or row syntax is malformed") from exc
        finally:
            csv.field_size_limit(previous_limit)
    if not parsed_rows:
        raise _error("ARTIFACT_FORMAT_INVALID", "CSV table is empty")
    columns = parsed_rows[0]
    rows = parsed_rows[1:]
    return columns, [None] * len(columns), rows, "csv_header"


def _scan_json_budget(text: str) -> None:
    in_string = False
    escaped = False
    depth = 0
    nodes = 0
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            nodes += 1
        elif character in "[{":
            depth += 1
            nodes += 1
            if depth > MAX_JSON_DEPTH:
                raise _error("INCOMPLETE", "JSON nesting exceeds the 64-level preview limit")
        elif character in "]}":
            depth -= 1
        elif character in ",:":
            nodes += 1
        if nodes > MAX_JSON_NODES:
            raise _error("INCOMPLETE", "JSON structure exceeds the bounded node budget")


class _DuplicateJsonKey(ValueError):
    pass


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonfinite_json(_value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _parse_json_table(source: bytes) -> tuple[list[str], list[str | None], list[list[Any]], str]:
    text = _decode_utf8(source)
    _scan_json_budget(text)
    try:
        root = json.loads(text, object_pairs_hook=_json_object, parse_constant=_reject_nonfinite_json)
    except _DuplicateJsonKey as exc:
        raise _error("ARTIFACT_FORMAT_INVALID", "JSON object contains a duplicate key") from exc
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        raise _error("ARTIFACT_FORMAT_INVALID", "JSON syntax, encoding, or finite-number requirements failed") from exc
    _check_json_scalars(root)

    if isinstance(root, dict) and "columns" in root and "rows" in root:
        if set(root) - {"columns", "rows", "units"}:
            raise _error("UNSUPPORTED_FORMAT", "JSON table objects may contain only columns, rows, and optional units")
        columns = root.get("columns")
        raw_rows = root.get("rows")
        if (not isinstance(columns, list) or not columns or len(columns) > MAX_DATA_COLUMNS
                or any(not isinstance(column, str) for column in columns)
                or len(set(columns)) != len(columns)
                or not isinstance(raw_rows, list)):
            raise _error("ARTIFACT_FORMAT_INVALID", "JSON table columns or rows have an invalid shape")
        rows: list[list[Any]] = []
        cells = len(columns)
        for row in raw_rows:
            if not isinstance(row, list) or len(row) != len(columns):
                raise _error("ARTIFACT_FORMAT_INVALID", "JSON table row width does not match its columns")
            if not all(_is_scalar(value) for value in row):
                raise _error("UNSUPPORTED_FORMAT", "JSON table cells must be scalar values")
            rows.append(row)
            cells += len(row)
            if len(rows) > MAX_DATA_ROWS or cells > MAX_DATA_CELLS:
                raise _error("INCOMPLETE", "JSON table exceeds the bounded row or cell budget")
        units = _normalize_units(root.get("units"), columns)
        return columns, units, rows, "declared_columns"

    if not isinstance(root, list) or not root:
        raise _error("UNSUPPORTED_FORMAT", "JSON preview requires a supported nonempty row table")
    if len(root) > MAX_DATA_ROWS:
        raise _error("INCOMPLETE", "JSON table exceeds the bounded row limit")
    if all(isinstance(row, dict) for row in root):
        columns = list(root[0])
        if not columns or len(columns) > MAX_DATA_COLUMNS:
            raise _error("INCOMPLETE", "JSON row objects have no usable columns or exceed the column limit")
        expected = set(columns)
        rows = []
        for row in root:
            if set(row) != expected:
                raise _error("ARTIFACT_FORMAT_INVALID", "JSON row objects do not have the same keys")
            values = [row[column] for column in columns]
            if not all(_is_scalar(value) for value in values):
                raise _error("UNSUPPORTED_FORMAT", "JSON row object values must be scalar")
            rows.append(values)
        if len(rows) * len(columns) > MAX_DATA_CELLS:
            raise _error("INCOMPLETE", "JSON table exceeds the bounded cell budget")
        return columns, [None] * len(columns), rows, "object_keys"
    if all(isinstance(row, list) for row in root):
        width = len(root[0])
        if width < 1 or width > MAX_DATA_COLUMNS:
            raise _error("INCOMPLETE", "JSON row arrays have no usable columns or exceed the column limit")
        if len(root) * width > MAX_DATA_CELLS:
            raise _error("INCOMPLETE", "JSON table exceeds the bounded cell budget")
        if any(not isinstance(row, list) or len(row) != width for row in root):
            raise _error("ARTIFACT_FORMAT_INVALID", "JSON row arrays do not have a uniform width")
        rows = []
        for row in root:
            if not all(_is_scalar(value) for value in row):
                raise _error("UNSUPPORTED_FORMAT", "JSON row array cells must be scalar values")
            rows.append(row)
        return [str(index) for index in range(width)], [None] * width, rows, "zero_based_positions"
    raise _error("UNSUPPORTED_FORMAT", "JSON document is not one of the supported table shapes")


def _check_json_scalars(root: Any) -> None:
    stack = [root]
    nodes = 0
    while stack:
        value = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise _error("INCOMPLETE", "JSON structure exceeds the bounded node budget")
        if isinstance(value, float) and not math.isfinite(value):
            raise _error("ARTIFACT_FORMAT_INVALID", "JSON numbers must be finite")
        if isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def _is_scalar(value: Any) -> bool:
    return value is None or type(value) in {str, bool, int, float} and (not isinstance(value, float) or math.isfinite(value))


def _normalize_units(value: Any, columns: list[str]) -> list[str | None]:
    if value is None:
        return [None] * len(columns)
    if isinstance(value, list):
        if len(value) != len(columns) or any(unit is not None and not isinstance(unit, str) for unit in value):
            raise _error("ARTIFACT_FORMAT_INVALID", "JSON table units must align with columns")
        return value
    if isinstance(value, dict):
        if set(value) != set(columns) or any(unit is not None and not isinstance(unit, str) for unit in value.values()):
            raise _error("ARTIFACT_FORMAT_INVALID", "JSON table unit keys must match the declared columns")
        return [value[column] for column in columns]
    raise _error("ARTIFACT_FORMAT_INVALID", "JSON table units must be a list or column-keyed object")


def _select_indices(total: int, maximum: int, selection: str) -> list[int]:
    if total <= maximum:
        return list(range(total))
    if selection == "first":
        return list(range(maximum))
    if maximum == 1:
        return [0]
    denominator = 2 * (maximum - 1)
    return [(2 * index * (total - 1) + (maximum - 1)) // denominator for index in range(maximum)]
