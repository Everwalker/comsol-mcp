"""Tests for W18: Real Plotting, Rendering, Views, Exports, and MCP ImageContent."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Mapping

import pytest
from mcp.types import ImageContent, TextContent

from comsol_mcp._artifact_store import ArtifactStore
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_ops import DISPATCH, dispatch
from comsol_mcp import _g3_w18 as w18
from comsol_mcp._mcp_gateway import mcp_result


# ---------------------------------------------------------------------------
# Mock COMSOL Node Hierarchy for W18
# ---------------------------------------------------------------------------

class MockPropFeature:
    def __init__(self, tag: str, type_id: str = "Feature") -> None:
        self.tag = tag
        self.type_id = type_id
        self.props: dict[str, Any] = {}
        self.ran = False

    def getType(self) -> str:
        return self.type_id

    def set(self, key: str, value: Any) -> None:
        self.props[key] = value

    def setEntry(self, key: str, subkey: str, subval: str) -> None:
        self.props.setdefault(key, {})[subkey] = subval

    def getString(self, key: str) -> str:
        return str(self.props.get(key, ""))

    def getInt(self, key: str) -> int:
        return int(self.props.get(key, 0))

    def getDouble(self, key: str) -> float:
        return float(self.props.get(key, 0.0))

    def getBoolean(self, key: str) -> bool:
        return bool(self.props.get(key, False))

    def properties(self) -> list[str]:
        return list(self.props.keys())

    def run(self) -> None:
        self.ran = True
        filename = self.props.get("pngfilename") or self.props.get("filename")
        if filename:
            p = Path(filename)
            p.parent.mkdir(parents=True, exist_ok=True)
            w = int(self.props.get("width") or 640)
            h = int(self.props.get("height") or 480)
            import struct
            import zlib
            ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
            ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data))
            idat_data = zlib.compress(b"\x00" * (w * 4 + 1) * h)
            idat_chunk = struct.pack(">I", len(idat_data)) + b"IDAT" + idat_data + struct.pack(">I", zlib.crc32(b"IDAT" + idat_data))
            png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + ihdr_data + ihdr_crc + idat_chunk + b"\x00\x00\x00\x00IEND\xaeB`\x82"
            p.write_bytes(png_bytes)


class MockFeatureList:
    def __init__(self) -> None:
        self._features: dict[str, MockPropFeature] = {}

    def tags(self) -> list[str]:
        return list(self._features.keys())

    def get(self, tag: str) -> MockPropFeature:
        if tag not in self._features:
            raise KeyError(tag)
        return self._features[tag]

    def create(self, tag: str, type_id: str) -> MockPropFeature:
        feat = MockPropFeature(tag, type_id)
        self._features[tag] = feat
        return feat

    def remove(self, tag: str) -> None:
        self._features.pop(tag, None)


class MockPlotGroup(MockPropFeature):
    def __init__(self, tag: str, type_id: str = "PlotGroup2D") -> None:
        super().__init__(tag, type_id)
        self._feat_list = MockFeatureList()

    def isPlotGroup(self) -> bool:
        return True

    def feature(self, tag: str | None = None) -> Any:
        if tag is not None:
            return self._feat_list.get(tag)
        return self._feat_list


class MockResult:
    def __init__(self) -> None:
        self._plot_groups: dict[str, MockPlotGroup] = {}
        self._export_list = MockFeatureList()

    def tags(self) -> list[str]:
        return list(self._plot_groups.keys())

    def get(self, tag: str) -> MockPlotGroup:
        if tag not in self._plot_groups:
            raise KeyError(tag)
        return self._plot_groups[tag]

    def create(self, tag: str, type_id: str) -> MockPlotGroup:
        pg = MockPlotGroup(tag, type_id)
        self._plot_groups[tag] = pg
        return pg

    def remove(self, tag: str) -> None:
        self._plot_groups.pop(tag, None)

    def export(self, tag: str | None = None) -> Any:
        if tag is not None:
            return self._export_list.get(tag)
        return self._export_list


class MockViewList:
    def __init__(self) -> None:
        self._views: dict[str, MockPropFeature] = {
            "view1": MockPropFeature("view1", "View3D"),
        }

    def tags(self) -> list[str]:
        return list(self._views.keys())

    def get(self, tag: str) -> MockPropFeature:
        if tag not in self._views:
            raise KeyError(tag)
        return self._views[tag]

    def create(self, tag: str, dim: int = 3) -> MockPropFeature:
        v = MockPropFeature(tag, f"View{dim}D")
        self._views[tag] = v
        return v

    def remove(self, tag: str) -> None:
        self._views.pop(tag, None)


class MockModel:
    def __init__(self) -> None:
        self._result = MockResult()
        self._view = MockViewList()

    def result(self, tag: str | None = None) -> Any:
        if tag is not None:
            return self._result.get(tag)
        return self._result

    def view(self, tag: str | None = None) -> Any:
        if tag is not None:
            return self._view.get(tag)
        return self._view


class MockWorker:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.paths = self
        self.resolved_project_root = project_root
        self._model = MockModel()

    def client(self) -> Any:
        return self

    def model(self, tag: str) -> MockModel:
        return self._model

    def model_handle(self, tag: str) -> MockModel:
        return self._model


# ---------------------------------------------------------------------------
# Tests for W18 Plot and Export Operations
# ---------------------------------------------------------------------------

def test_plot_lifecycle(tmp_path: Path) -> None:
    worker = MockWorker(tmp_path)

    # 1. Create Plot Group
    create_res = DISPATCH["plot.group_create"](
        worker,
        "m1",
        {"tag": "pg1", "dimension": 2, "dataset": "dset1", "properties": {"title": "Temperature"}},
    )
    assert create_res["tag"] == "pg1"
    assert create_res["type_id"] == "PlotGroup2D"

    # 2. Create Plot Feature
    feat_res = DISPATCH["plot.feature_create"](
        worker,
        "m1",
        {"group": "pg1", "tag": "surf1", "type_id": "Surface", "properties": {"expr": "T", "unit": "degC"}},
    )
    assert feat_res["tag"] == "surf1"
    assert feat_res["type_id"] == "Surface"

    # 3. List Plots
    list_res = DISPATCH["plot.list"](worker, "m1", {})
    assert list_res["total_count"] == 1
    pg_item = list_res["plot_groups"][0]
    assert pg_item["tag"] == "pg1"
    assert len(pg_item["features"]) == 1
    assert pg_item["features"][0]["tag"] == "surf1"

    # 4. Update Feature
    update_res = DISPATCH["plot.update"](
        worker,
        "m1",
        {"path": "pg1/surf1", "properties": {"expr": "p"}},
    )
    assert update_res["updated"] is True

    # 5. Remove Feature and Plot Group
    feat_remove = DISPATCH["plot.remove"](worker, "m1", {"path": "pg1/surf1"})
    assert feat_remove["removed"] is True

    pg_remove = DISPATCH["plot.remove"](worker, "m1", {"path": "pg1"})
    assert pg_remove["removed"] is True

    assert DISPATCH["plot.list"](worker, "m1", {})["total_count"] == 0


def test_plot_render_and_mcp_image_return(tmp_path: Path) -> None:
    worker = MockWorker(tmp_path)
    DISPATCH["plot.group_create"](worker, "m1", {"tag": "pg_render", "dimension": 2})

    # Render Plot
    render_res = DISPATCH["plot.render"](
        worker,
        "m1",
        {
            "path": "pg_render",
            "options": {"width": 640, "height": 480, "format": "png", "destination": "rendered.png"},
        },
    )
    assert render_res["plot_group"] == "pg_render"
    assert render_res["width"] == 640
    assert render_res["height"] == 480
    assert render_res["format"] == "png"
    assert "image_base64" in render_res
    assert ArtifactStore.is_registered_artifact(render_res["file_path"])

    # Test MCP Gateway mcp_result returns ImageContent and TextContent
    envelope = {"success": True, "data": render_res}
    call_result = mcp_result(envelope)
    assert len(call_result.content) == 2
    assert isinstance(call_result.content[0], ImageContent)
    assert call_result.content[0].type == "image"
    assert call_result.content[0].mimeType == "image/png"
    assert call_result.content[0].data == render_res["image_base64"]

    assert isinstance(call_result.content[1], TextContent)
    assert "<embedded base64 image" in call_result.content[1].text


def test_plot_view_manage(tmp_path: Path) -> None:
    worker = MockWorker(tmp_path)

    # List views
    v_list = DISPATCH["plot.view_manage"](worker, "m1", {"action": "list"})
    assert v_list["total_count"] >= 1

    # Inspect view
    v_inspect = DISPATCH["plot.view_manage"](worker, "m1", {"action": "inspect", "path": "view1"})
    assert v_inspect["view"] == "view1"

    # Create view
    v_create = DISPATCH["plot.view_manage"](
        worker, "m1", {"action": "create", "path": "view2", "definition": {"dimension": 3}}
    )
    assert v_create["created"] is True

    # Update view
    v_update = DISPATCH["plot.view_manage"](
        worker, "m1", {"action": "update", "path": "view2", "definition": {"zoom": "fit"}}
    )
    assert v_update["updated"] is True

    # Remove view
    v_remove = DISPATCH["plot.view_manage"](worker, "m1", {"action": "remove", "path": "view2"})
    assert v_remove["removed"] is True


def test_export_lifecycle(tmp_path: Path) -> None:
    worker = MockWorker(tmp_path)

    # 1. Create Export Node
    create_res = DISPATCH["export.create"](
        worker,
        "m1",
        {
            "tag": "exp1",
            "type_id": "Image",
            "definition": {"filename": str(tmp_path / "exp1.png"), "width": 800, "height": 600},
        },
    )
    assert create_res["tag"] == "exp1"
    assert create_res["type_id"] == "Image"

    # 2. List Exports
    list_res = DISPATCH["export.list"](worker, "m1", {})
    assert list_res["total_count"] == 1

    # 3. Update Export
    update_res = DISPATCH["export.update"](
        worker,
        "m1",
        {"path": "exp1", "definition": {"width": 1024}},
    )
    assert update_res["updated"] is True

    # 4. Run Export
    run_res = DISPATCH["export.run"](worker, "m1", {"path": "exp1"})
    assert run_res["tag"] == "exp1"
    assert "image_base64" in run_res
    assert Path(run_res["file_path"]).is_file()

    # 5. Remove Export
    remove_res = DISPATCH["export.remove"](worker, "m1", {"path": "exp1"})
    assert remove_res["removed"] is True
    assert DISPATCH["export.list"](worker, "m1", {})["total_count"] == 0


def test_export_staging_fallback_refused(tmp_path: Path) -> None:
    worker = MockWorker(tmp_path)
    target_file = tmp_path / "stale_target.png"
    target_file.write_bytes(b"STALE_PREEXISTING_CONTENT")

    DISPATCH["export.create"](
        worker,
        "m1",
        {
            "tag": "exp_fail",
            "type_id": "Image",
            "definition": {"filename": str(target_file)},
        },
    )

    exp_feat = worker.model("m1").result().export("exp_fail")
    exp_feat.run = lambda: None  # no-op: does not produce staging file

    with pytest.raises(ExecutionContractError) as exc_info:
        DISPATCH["export.run"](worker, "m1", {"path": "exp_fail"})
    assert exc_info.value.code == "EXPORT_FAILED"
    # Old target file must remain untouched
    assert target_file.read_bytes() == b"STALE_PREEXISTING_CONTENT"


def test_export_staging_binding_failed(tmp_path: Path) -> None:
    worker = MockWorker(tmp_path)
    DISPATCH["export.create"](
        worker,
        "m1",
        {
            "tag": "exp_nobind",
            "type_id": "Image",
            "definition": {"filename": str(tmp_path / "out.png")},
        },
    )

    exp_feat = worker.model("m1").result().export("exp_nobind")
    def fail_set(key: str, val: Any) -> None:
        raise RuntimeError("Property assignment rejected")
    exp_feat.set = fail_set

    with pytest.raises(ExecutionContractError) as exc_info:
        DISPATCH["export.run"](worker, "m1", {"path": "exp_nobind"})
    assert exc_info.value.code == "EXPORT_BINDING_FAILED"
