"""JSON export for CodeGraph."""

from __future__ import annotations

import json
from pathlib import Path

from codegraph.models import CodeGraph


def export_json(graph: CodeGraph, output_path: str) -> None:
    """Write *graph* to a JSON file at *output_path*."""
    data = {
        "nodes": [n.to_dict() for n in graph.nodes],
        "links": [e.to_dict() for e in graph.edges],
        "stats": graph.stats,
    }
    Path(output_path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
