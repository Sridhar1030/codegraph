"""Data structures for the CodeGraph analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FunctionNode:
    id: str
    file: str
    class_name: Optional[str]
    function: str
    short_name: str
    lineno: int
    end_lineno: int
    params: int
    lines: int
    docstring: Optional[str]
    is_async: bool
    decorators: list[str]
    node_type: str
    in_degree: int = 0
    out_degree: int = 0
    color_class: str = "internal"
    file_group: int = 0
    depth: int = 0
    is_generated: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "short_name": self.short_name,
            "file": self.file,
            "class_name": self.class_name,
            "function": self.function,
            "lineno": self.lineno,
            "params": self.params,
            "lines": self.lines,
            "docstring": self.docstring or "",
            "is_async": self.is_async,
            "decorators": self.decorators,
            "type": self.node_type,
            "in_degree": self.in_degree,
            "out_degree": self.out_degree,
            "color_class": self.color_class,
            "file_group": self.file_group,
            "depth": self.depth,
            "is_generated": self.is_generated,
        }


@dataclass
class RawCall:
    """A call reference extracted during AST scanning, before resolution."""
    name: str
    receiver: Optional[str]
    lineno: int
    order: int = 0


@dataclass
class CallEdge:
    source: str
    target: str
    order: int = 0
    edge_type: str = "resolved"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "order": self.order,
            "type": self.edge_type,
        }


@dataclass
class CodeGraph:
    nodes: list[FunctionNode] = field(default_factory=list)
    edges: list[CallEdge] = field(default_factory=list)
    external_packages: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def node_by_id(self, node_id: str) -> Optional[FunctionNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None
