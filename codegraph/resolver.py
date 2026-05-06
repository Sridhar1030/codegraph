"""Qualified name resolution — the core improvement over the prototype.

Levels of resolution:
  A. self.method()      -> EnclosingClass.method (unambiguous)
  B. variable.method()  -> TrackedType.method (constructor assignments)
  C. Polymorphic        -> dispatch node with option edges to all candidates
  D. Dynamic            -> getattr() patterns -> dynamic dispatch node
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from codegraph.models import CallEdge, CodeGraph, FunctionNode, RawCall
from codegraph.scanner import ScanResult

_BUILTIN_NAMES = {
    "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
    "list", "dict", "set", "tuple", "str", "int", "float", "bool", "bytes",
    "type", "isinstance", "issubclass", "hasattr", "getattr", "setattr",
    "delattr", "super", "property", "classmethod", "staticmethod",
    "open", "iter", "next", "reversed", "abs", "min", "max", "sum",
    "any", "all", "id", "hash", "repr", "format", "vars", "dir",
    "callable", "chr", "ord", "hex", "oct", "bin",
    "ValueError", "TypeError", "KeyError", "AttributeError", "RuntimeError",
    "Exception", "NotImplementedError", "ImportError", "OSError", "IOError",
    "StopIteration", "IndexError", "FileNotFoundError",
}

_DUNDER_NAMES = {
    "__init__", "__repr__", "__str__", "__eq__", "__ne__", "__hash__",
    "__len__", "__iter__", "__next__", "__enter__", "__exit__",
    "__getattr__", "__setattr__", "__getitem__", "__setitem__",
    "__contains__", "__call__", "__bool__", "__del__",
    "__lt__", "__le__", "__gt__", "__ge__", "__add__", "__sub__",
    "__mul__", "__truediv__", "__floordiv__", "__mod__", "__pow__",
}

_KNOWN_PACKAGE_MAP = {
    "DataFrame": "pandas", "Series": "pandas", "concat": "pandas",
    "array": "numpy", "ndarray": "numpy", "zeros": "numpy",
    "FastAPI": "fastapi", "APIRouter": "fastapi", "Depends": "fastapi",
    "Field": "pydantic", "BaseModel": "pydantic", "validator": "pydantic",
    "Session": "sqlalchemy", "Column": "sqlalchemy", "create_engine": "sqlalchemy",
    "patch": "unittest", "MagicMock": "unittest", "Mock": "unittest",
    "Path": "pathlib", "dumps": "json", "loads": "json",
    "Logger": "logging", "getLogger": "logging",
    "datetime": "datetime", "timedelta": "datetime",
    "sleep": "time", "grpc": "grpc", "click": "click",
    "yaml": "pyyaml", "tabulate": "tabulate",
}

def build_graph(scan: ScanResult) -> CodeGraph:
    """Resolve raw calls into edges, building the full CodeGraph."""
    graph = CodeGraph()

    node_by_id: dict[str, FunctionNode] = {}
    qualified_index: dict[str, list[str]] = defaultdict(list)
    bare_index: dict[str, list[str]] = defaultdict(list)
    class_method_index: dict[str, dict[str, str]] = defaultdict(dict)

    for n in scan.nodes:
        node_by_id[n.id] = n
        bare_index[n.function].append(n.id)
        if n.class_name:
            qname = f"{n.class_name}.{n.function}"
            qualified_index[qname].append(n.id)
            class_method_index[n.class_name][n.function] = n.id

    internal_names = set()
    for n in scan.nodes:
        internal_names.add(n.function)
        if n.class_name:
            internal_names.add(n.class_name)

    external_packages = set(scan.imports["external"])

    seen_edges: set[tuple[str, str]] = set()
    ext_nodes: dict[str, FunctionNode] = {}
    dispatch_nodes: dict[str, FunctionNode] = {}

    for src_id, calls in scan.raw_calls.items():
        src_node = node_by_id.get(src_id)
        if src_node is None:
            continue
        enclosing_class = src_node.class_name
        type_scope = scan.type_scopes.get(src_id, {})
        file_imports = scan.file_imports.get(src_node.file, {})

        for call in calls:
            targets = _resolve_call(
                call=call,
                enclosing_class=enclosing_class,
                type_scope=type_scope,
                class_method_index=class_method_index,
                qualified_index=qualified_index,
                bare_index=bare_index,
                node_by_id=node_by_id,
                src_file=src_node.file,
                file_imports=file_imports,
            )

            if targets is None:
                _try_add_external(
                    call, src_id, src_node.file, external_packages,
                    internal_names, ext_nodes, seen_edges, graph,
                )
                continue

            if isinstance(targets, str) and targets.startswith("dispatch:"):
                dispatch_id = targets
                if dispatch_id not in dispatch_nodes:
                    method_name = dispatch_id.split(":", 1)[1]
                    dn = FunctionNode(
                        id=dispatch_id, file="<dispatch>",
                        class_name=None, function=method_name,
                        short_name=f"?.{method_name}",
                        lineno=0, end_lineno=0, params=0, lines=0,
                        docstring=f"Polymorphic dispatch: {method_name}",
                        is_async=False, decorators=[],
                        node_type="dispatch",
                    )
                    dispatch_nodes[dispatch_id] = dn

                key = (src_id, dispatch_id)
                if key not in seen_edges:
                    seen_edges.add(key)
                    graph.edges.append(CallEdge(
                        source=src_id, target=dispatch_id,
                        order=call.order, edge_type="resolved",
                    ))

                candidates = bare_index.get(call.name, [])
                for cand_id in candidates:
                    okey = (dispatch_id, cand_id)
                    if okey not in seen_edges:
                        seen_edges.add(okey)
                        graph.edges.append(CallEdge(
                            source=dispatch_id, target=cand_id,
                            order=0, edge_type="option",
                        ))
                continue

            for tgt_id in targets:
                if tgt_id == src_id:
                    continue
                key = (src_id, tgt_id)
                if key not in seen_edges:
                    seen_edges.add(key)
                    graph.edges.append(CallEdge(
                        source=src_id, target=tgt_id,
                        order=call.order, edge_type="resolved",
                    ))

    # Dynamic dispatch nodes (Level D)
    for src_id, lines in scan.dynamic_lines.items():
        dyn_id = f"dynamic:{src_id}"
        if dyn_id not in dispatch_nodes:
            dn = FunctionNode(
                id=dyn_id, file="<dynamic>",
                class_name=None, function="getattr_dispatch",
                short_name="dynamic_dispatch",
                lineno=lines[0] if lines else 0, end_lineno=0,
                params=0, lines=0,
                docstring=f"Dynamic dispatch via getattr (lines: {lines})",
                is_async=False, decorators=[],
                node_type="dispatch",
            )
            dispatch_nodes[dyn_id] = dn
        key = (src_id, dyn_id)
        if key not in seen_edges:
            seen_edges.add(key)
            graph.edges.append(CallEdge(
                source=src_id, target=dyn_id,
                order=0, edge_type="dynamic",
            ))

    all_nodes = list(scan.nodes) + list(ext_nodes.values()) + list(dispatch_nodes.values())

    in_deg: dict[str, int] = defaultdict(int)
    out_deg: dict[str, int] = defaultdict(int)
    for e in graph.edges:
        out_deg[e.source] += 1
        in_deg[e.target] += 1

    for n in all_nodes:
        n.in_degree = in_deg.get(n.id, 0)
        n.out_degree = out_deg.get(n.id, 0)

    graph.nodes = all_nodes
    graph.external_packages = scan.imports["external"]

    files_scanned = len({n.file for n in scan.nodes})
    graph.stats = {
        "total_functions": len(scan.nodes),
        "total_edges_resolved": len(graph.edges),
        "external_packages": len(scan.imports["external"]),
        "files_scanned": files_scanned,
        "dispatch_nodes": len(dispatch_nodes),
        "option_edges": sum(1 for e in graph.edges if e.edge_type == "option"),
        "dynamic_edges": sum(1 for e in graph.edges if e.edge_type == "dynamic"),
    }

    return graph


def _resolve_call(
    call: RawCall,
    enclosing_class: Optional[str],
    type_scope: dict[str, str],
    class_method_index: dict[str, dict[str, str]],
    qualified_index: dict[str, list[str]],
    bare_index: dict[str, list[str]],
    node_by_id: dict[str, FunctionNode],
    src_file: str,
    file_imports: dict[str, str] | None = None,
) -> list[str] | str | None:
    """Resolve a single call. Returns:
    - list[str] of target node IDs
    - "dispatch:<method>" sentinel for polymorphic dispatch
    - None if unresolved
    """
    name = call.name
    receiver = call.receiver

    if name in _DUNDER_NAMES:
        return None
    if name in _BUILTIN_NAMES:
        return None

    # Level A: self.method() -> EnclosingClass.method
    if receiver == "self" and enclosing_class:
        target_id = class_method_index.get(enclosing_class, {}).get(name)
        if target_id:
            return [target_id]

    # Level B: tracked variable type -> KnownType.method
    if receiver and receiver != "self" and receiver in type_scope:
        known_class = type_scope[receiver]
        target_id = class_method_index.get(known_class, {}).get(name)
        if target_id:
            return [target_id]

    # Level B+: import-based receiver narrowing
    # If receiver is a directly imported class name, resolve via class_method_index.
    # e.g., `from feast.registry import Registry` then `Registry.apply()`
    if receiver and receiver != "self" and file_imports:
        try:
            imported_module = file_imports.get(receiver)
            if imported_module:
                target_id = class_method_index.get(receiver, {}).get(name)
                if target_id:
                    return [target_id]
        except Exception:
            pass

    # Try fully-qualified lookup
    if name in qualified_index:
        return qualified_index[name]

    # Bare name lookup with disambiguation
    candidates = bare_index.get(name, [])
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates

    # Import-based narrowing: reduce candidates using source file's imports
    if len(candidates) >= 2 and file_imports:
        try:
            imported_modules = set(file_imports.values())
            narrowed: list[str] = []
            for cand_id in candidates:
                cand_node = node_by_id.get(cand_id)
                if not cand_node:
                    continue
                cand_module = cand_node.file.replace("/", ".").replace(".py", "")
                for imp_mod in imported_modules:
                    imp_as_dotted = imp_mod.replace("/", ".")
                    if cand_module.endswith(imp_as_dotted) or imp_as_dotted in cand_module:
                        narrowed.append(cand_id)
                        break
            if len(narrowed) == 1:
                return narrowed
            if 2 <= len(narrowed) < len(candidates):
                candidates = narrowed
        except Exception:
            pass

    # Ambiguous: 2+ candidates becomes a dispatch node
    if len(candidates) >= 2:
        return f"dispatch:{name}"

    return None


def _guess_package(call_name: str, external_packages: set[str]) -> Optional[str]:
    if call_name in _BUILTIN_NAMES:
        return None
    pkg = _KNOWN_PACKAGE_MAP.get(call_name)
    if pkg and pkg in external_packages:
        return pkg
    return None


def _try_add_external(
    call: RawCall,
    src_id: str,
    src_file: str,
    external_packages: set[str],
    internal_names: set[str],
    ext_nodes: dict[str, FunctionNode],
    seen_edges: set[tuple[str, str]],
    graph: CodeGraph,
) -> None:
    if call.name in internal_names:
        return
    pkg = _guess_package(call.name, external_packages)
    if not pkg:
        return
    ext_id = f"ext:{pkg}"
    if ext_id not in ext_nodes:
        ext_nodes[ext_id] = FunctionNode(
            id=ext_id, file="external",
            class_name=None, function=pkg,
            short_name=pkg,
            lineno=0, end_lineno=0, params=0, lines=0,
            docstring=f"External package: {pkg}",
            is_async=False, decorators=[],
            node_type="external",
        )
    key = (src_id, ext_id)
    if key not in seen_edges:
        seen_edges.add(key)
        graph.edges.append(CallEdge(
            source=src_id, target=ext_id,
            order=call.order, edge_type="resolved",
        ))


def filter_graph(graph: CodeGraph, min_in: int = 1, min_out: int = 1) -> CodeGraph:
    """Pre-filter to nodes with minimum connectivity."""
    keep_ids: set[str] = set()
    for n in graph.nodes:
        if n.node_type == "external":
            if n.in_degree >= 2:
                keep_ids.add(n.id)
        elif n.in_degree >= min_in or n.out_degree >= min_out:
            keep_ids.add(n.id)

    return CodeGraph(
        nodes=[n for n in graph.nodes if n.id in keep_ids],
        edges=[e for e in graph.edges if e.source in keep_ids and e.target in keep_ids],
        external_packages=graph.external_packages,
        stats=graph.stats,
    )
