"""Graph storage abstraction — swap backends without changing consumers.

Current backend: NetworkX (in-process).
Future: Neo4j, or any graph database implementing GraphStore.
"""

from __future__ import annotations

import pickle
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import networkx as nx

from codegraph.models import CodeGraph, FunctionNode, CallEdge


@dataclass
class SubGraph:
    """A subset of the full graph, returned by query methods."""
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@dataclass
class ImpactResult:
    """Impact analysis result for a single node."""
    focus: dict
    downstream: list[dict] = field(default_factory=list)
    upstream: list[dict] = field(default_factory=list)
    downstream_count: int = 0
    upstream_count: int = 0


@dataclass
class FlowChartNode:
    """A node in a flowchart tree."""
    id: str
    name: str
    file: str
    node_type: str
    children: list[FlowChartNode] = field(default_factory=list)
    edge_type: str = "resolved"
    order: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "file": self.file,
            "node_type": self.node_type,
            "edge_type": self.edge_type,
            "order": self.order,
            "children": [c.to_dict() for c in self.children],
        }


class GraphStore(ABC):
    """Abstract interface for graph storage backends."""

    @abstractmethod
    def load(self, graph: CodeGraph) -> None:
        """Load a CodeGraph into the store, replacing any existing data."""
        ...

    @abstractmethod
    def is_loaded(self) -> bool:
        """Check if a graph is currently loaded."""
        ...

    @abstractmethod
    def get_node(self, node_id: str) -> Optional[dict]:
        """Get a single node by ID, with all attributes."""
        ...

    @abstractmethod
    def get_neighbors(self, node_id: str, direction: str = "both", depth: int = 1) -> SubGraph:
        """Get the neighborhood subgraph around a node.
        direction: 'out' (calls), 'in' (called_by), 'both'
        """
        ...

    @abstractmethod
    def get_call_chain(self, node_id: str, depth: int = 5) -> Optional[FlowChartNode]:
        """Get the call chain rooted at node_id as a tree for flowchart rendering."""
        ...

    @abstractmethod
    def search(self, query: str, file: Optional[str] = None,
               node_type: Optional[str] = None,
               min_in: int = 0, min_out: int = 0,
               include_generated: bool = False,
               limit: int = 100) -> list[dict]:
        """Search nodes by name, file, type, and degree filters."""
        ...

    @abstractmethod
    def get_subgraph(self, node_ids: list[str]) -> SubGraph:
        """Get an arbitrary subgraph containing the specified nodes and edges between them."""
        ...

    @abstractmethod
    def get_impact(self, node_id: str, depth: int = 5) -> Optional[ImpactResult]:
        """Impact analysis: upstream and downstream from a node."""
        ...

    @abstractmethod
    def get_file_nodes(self, file_path: str) -> list[dict]:
        """Get all nodes in a specific file."""
        ...

    @abstractmethod
    def get_files(self) -> list[dict]:
        """Get list of files with node counts."""
        ...

    @abstractmethod
    def stats(self) -> dict:
        """Get graph statistics."""
        ...

    @abstractmethod
    def save(self, path: str) -> None:
        """Persist the graph to disk."""
        ...

    @abstractmethod
    def load_from_disk(self, path: str) -> bool:
        """Load a previously saved graph from disk. Returns True if successful."""
        ...


class NetworkXStore(GraphStore):
    """NetworkX-based graph storage. All data in-process, pickle for persistence."""

    def __init__(self) -> None:
        self._graph: Optional[nx.DiGraph] = None
        self._code_graph: Optional[CodeGraph] = None
        self._meta: dict = {}

    def load(self, graph: CodeGraph) -> None:
        G = nx.DiGraph()
        for node in graph.nodes:
            G.add_node(node.id, **node.to_dict())
        for edge in graph.edges:
            G.add_edge(edge.source, edge.target,
                       order=edge.order, type=edge.edge_type)
        self._graph = G
        self._code_graph = graph
        self._meta = {
            "total_nodes": G.number_of_nodes(),
            "total_edges": G.number_of_edges(),
            **graph.stats,
        }

    def is_loaded(self) -> bool:
        return self._graph is not None and self._graph.number_of_nodes() > 0

    def get_node(self, node_id: str) -> Optional[dict]:
        if not self._graph or node_id not in self._graph:
            return None
        attrs = dict(self._graph.nodes[node_id])
        attrs["calls"] = [
            {"id": succ, "name": self._graph.nodes[succ].get("short_name", succ),
             "order": self._graph.edges[node_id, succ].get("order", 0),
             "edge_type": self._graph.edges[node_id, succ].get("type", "resolved")}
            for succ in self._graph.successors(node_id)
            if succ in self._graph.nodes
        ]
        attrs["called_by"] = [
            {"id": pred, "name": self._graph.nodes[pred].get("short_name", pred),
             "order": self._graph.edges[pred, node_id].get("order", 0),
             "edge_type": self._graph.edges[pred, node_id].get("type", "resolved")}
            for pred in self._graph.predecessors(node_id)
            if pred in self._graph.nodes
        ]
        return attrs

    def get_neighbors(self, node_id: str, direction: str = "both", depth: int = 1) -> SubGraph:
        if not self._graph or node_id not in self._graph:
            return SubGraph()

        visited = {node_id}
        frontier = {node_id}
        for _ in range(depth):
            next_frontier = set()
            for nid in frontier:
                if direction in ("out", "both"):
                    next_frontier.update(self._graph.successors(nid))
                if direction in ("in", "both"):
                    next_frontier.update(self._graph.predecessors(nid))
            next_frontier -= visited
            visited.update(next_frontier)
            frontier = next_frontier

        sub = self._graph.subgraph(visited)
        nodes = [dict(sub.nodes[n]) for n in sub.nodes]
        edges = [{"source": u, "target": v, **d} for u, v, d in sub.edges(data=True)]
        return SubGraph(nodes=nodes, edges=edges,
                        stats={"node_count": len(nodes), "edge_count": len(edges)})

    def get_call_chain(self, node_id: str, depth: int = 5) -> Optional[FlowChartNode]:
        if not self._graph or node_id not in self._graph:
            return None

        def _build_tree(nid: str, current_depth: int, visited: set) -> FlowChartNode:
            node_attrs = self._graph.nodes[nid]
            tree_node = FlowChartNode(
                id=nid,
                name=node_attrs.get("short_name", nid),
                file=node_attrs.get("file", ""),
                node_type=node_attrs.get("type", "function"),
            )
            if current_depth >= depth:
                return tree_node

            visited.add(nid)
            successors = sorted(
                self._graph.successors(nid),
                key=lambda s: self._graph.edges[nid, s].get("order", 0),
            )
            for succ in successors:
                if succ not in visited and succ in self._graph.nodes:
                    edge_data = self._graph.edges[nid, succ]
                    child = _build_tree(succ, current_depth + 1, visited)
                    child.edge_type = edge_data.get("type", "resolved")
                    child.order = edge_data.get("order", 0)
                    tree_node.children.append(child)
            visited.discard(nid)
            return tree_node

        return _build_tree(node_id, 0, set())

    def search(self, query: str = "", file: Optional[str] = None,
               node_type: Optional[str] = None,
               min_in: int = 0, min_out: int = 0,
               include_generated: bool = False,
               limit: int = 100) -> list[dict]:
        if not self._graph:
            return []

        results = []
        q = query.lower()
        for nid, attrs in self._graph.nodes(data=True):
            if not include_generated and attrs.get("is_generated", False):
                continue
            if node_type and attrs.get("type") != node_type:
                continue
            if file and attrs.get("file") != file:
                continue
            if attrs.get("in_degree", 0) < min_in:
                continue
            if attrs.get("out_degree", 0) < min_out:
                continue
            if q:
                name_match = q in attrs.get("short_name", "").lower()
                file_match = q in attrs.get("file", "").lower()
                id_match = q in nid.lower()
                if not (name_match or file_match or id_match):
                    continue
            results.append(dict(attrs))
            if len(results) >= limit:
                break
        return results

    def get_subgraph(self, node_ids: list[str]) -> SubGraph:
        if not self._graph:
            return SubGraph()
        valid = [nid for nid in node_ids if nid in self._graph]
        sub = self._graph.subgraph(valid)
        nodes = [dict(sub.nodes[n]) for n in sub.nodes]
        edges = [{"source": u, "target": v, **d} for u, v, d in sub.edges(data=True)]
        return SubGraph(nodes=nodes, edges=edges,
                        stats={"node_count": len(nodes), "edge_count": len(edges)})

    def get_impact(self, node_id: str, depth: int = 5) -> Optional[ImpactResult]:
        if not self._graph or node_id not in self._graph:
            return None

        focus = self.get_node(node_id)
        if not focus:
            return None

        downstream_ids = self._bfs(node_id, direction="out", depth=depth)
        upstream_ids = self._bfs(node_id, direction="in", depth=depth)

        downstream = [dict(self._graph.nodes[nid]) for nid in downstream_ids if nid in self._graph]
        upstream = [dict(self._graph.nodes[nid]) for nid in upstream_ids if nid in self._graph]

        return ImpactResult(
            focus=focus,
            downstream=downstream,
            upstream=upstream,
            downstream_count=len(downstream),
            upstream_count=len(upstream),
        )

    def get_file_nodes(self, file_path: str) -> list[dict]:
        if not self._graph:
            return []
        return [dict(attrs) for _, attrs in self._graph.nodes(data=True)
                if attrs.get("file") == file_path]

    def get_files(self) -> list[dict]:
        if not self._graph:
            return []
        file_counts: dict[str, int] = defaultdict(int)
        for _, attrs in self._graph.nodes(data=True):
            f = attrs.get("file", "")
            if f and f not in ("<dispatch>", "<dynamic>", "external"):
                file_counts[f] += 1
        return [{"file": f, "node_count": c}
                for f, c in sorted(file_counts.items(), key=lambda x: -x[1])]

    def stats(self) -> dict:
        if not self._graph:
            return {"loaded": False}

        G = self._graph
        node_types: dict[str, int] = defaultdict(int)
        for _, attrs in G.nodes(data=True):
            node_types[attrs.get("type", "unknown")] += 1

        edge_types: dict[str, int] = defaultdict(int)
        for _, _, attrs in G.edges(data=True):
            edge_types[attrs.get("type", "unknown")] += 1

        generated_count = sum(1 for _, a in G.nodes(data=True) if a.get("is_generated", False))

        return {
            "loaded": True,
            "total_nodes": G.number_of_nodes(),
            "total_edges": G.number_of_edges(),
            "node_types": dict(node_types),
            "edge_types": dict(edge_types),
            "generated_nodes": generated_count,
            "files": len(set(a.get("file", "") for _, a in G.nodes(data=True))),
            **self._meta,
        }

    def save(self, path: str) -> None:
        if not self._graph:
            return
        data = {
            "graph": self._graph,
            "meta": self._meta,
        }
        Path(path).write_bytes(pickle.dumps(data))

    def load_from_disk(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        try:
            data = pickle.loads(p.read_bytes())
            self._graph = data["graph"]
            self._meta = data.get("meta", {})
            return True
        except (pickle.UnpicklingError, KeyError, EOFError):
            return False

    def _bfs(self, start: str, direction: str, depth: int) -> set[str]:
        """BFS traversal from start node."""
        visited: set[str] = set()
        frontier = {start}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for nid in frontier:
                if direction == "out":
                    neighbors = set(self._graph.successors(nid))
                else:
                    neighbors = set(self._graph.predecessors(nid))
                next_frontier.update(neighbors - visited - {start})
            visited.update(next_frontier)
            frontier = next_frontier
        return visited

    def get_full_graph_data(self) -> SubGraph:
        """Get the complete graph data for visualization."""
        if not self._graph:
            return SubGraph()
        nodes = [dict(attrs) for _, attrs in self._graph.nodes(data=True)]
        edges = [{"source": u, "target": v, **d}
                 for u, v, d in self._graph.edges(data=True)]
        return SubGraph(nodes=nodes, edges=edges,
                        stats=self.stats())
