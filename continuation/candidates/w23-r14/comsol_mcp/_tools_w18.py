#!/usr/bin/env python3
"""W18 MCP tools: plot_render."""
from __future__ import annotations

from typing import Any


def plot_render(path: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render a plot group to a verified image artifact and return ImageContent.

    Args:
        path: Tag or path of the plot group to render (e.g. 'pg3d' or 'result/pg3d').
        options: Optional render settings such as width, height, format ('png'), and scientific parameter values.
    """
    return {"path": path, "options": options or {}}


def register(registry: Any) -> None:
    registry.add_tool(plot_render, name="plot.render")
    registry.add_tool(plot_render, name="plot_render")
