#!/usr/bin/env python3
"""W18 MCP tools: plot_render."""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import WithJsonSchema

from ._g2_registry import BY_ID


_PLOT_RENDER_SCHEMA = BY_ID["plot.render"].input_schema["properties"]
PlotRenderOptions = Annotated[Any, WithJsonSchema(_PLOT_RENDER_SCHEMA["options"])]
PlotSolutionSpec = Annotated[Any, WithJsonSchema(_PLOT_RENDER_SCHEMA["solution"])]


def plot_render(path: str, options: PlotRenderOptions = None,
                solution: PlotSolutionSpec = None) -> dict[str, Any]:
    """Render a plot group to a verified image artifact and return ImageContent.

    Args:
        path: Tag or path of the plot group to render (e.g. 'pg3d' or 'result/pg3d').
        options: Optional render settings such as width, height, format ('png'), and scientific parameter values.
        solution: Optional explicit plot binding with a required dataset and an optional mapped solution tag.
    """
    result = {"path": path, "options": options or {}}
    if solution is not None:
        result["solution"] = solution
    return result


def register(registry: Any) -> None:
    registry.add_tool(plot_render, name="plot.render")
    registry.add_tool(plot_render, name="plot_render", operation_id="plot.render")
