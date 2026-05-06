"""Compare code graphs between two git commits.

Self-contained module: includes its own AST scanner and lightweight resolver
so it works independently of scanner.py and resolver.py (which may be in
active development by another agent).

Usage:
    from codegraph.diff import diff_commits, compare_graphs
    diff = diff_commits("/path/to/repo", "HEAD~1", "HEAD", scan_subdir="src")
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


# ── Lightweight scan types (private to diff) ─────────────────────────────────

_EXCLUDE_DIRS = {
    "__pycache__", ".git", ".tox", ".eggs", "node_modules",
    "venv", ".venv", "env", ".env", "build", "dist", ".mypy_cache",
}


@dataclass
class _Node:
    id: str
    file: str
    class_name: str | None
    function: str
    short_name: str
    lineno: int
    params: int
    lines: int
    docstring: str
    is_async: bool
    decorators: list[str]
    type: str  # "function" | "method"
    ast_hash: str
    in_degree: int = 0
    out_degree: int = 0


@dataclass
class _ScanResult:
    nodes: list[_Node]
    raw_edges: list[dict]


def _extract_decorator_name(dec: ast.expr) -> str:
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
    if isinstance(dec, ast.Call):
        return _extract_decorator_name(dec.func)
    return "unknown"


def _docstring_first_line(node: ast.AST) -> str:
    ds = ast.get_docstring(node)
    if not ds:
        return ""
    first = ds.strip().split("\n")[0]
    return first[:80] if len(first) > 80 else first


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _body_hash(func_node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(func_node).encode()).hexdigest()[:16]


def _scan(root_dir: str | Path) -> _ScanResult:
    """Scan a Python codebase, extract function nodes and raw call edges."""
    root = Path(root_dir).resolve()
    nodes: list[_Node] = []
    raw_edges: list[dict] = []

    def _collect_calls(func_node: ast.AST) -> list[dict]:
        raw = []
        for child in ast.walk(func_node):
            if isinstance(child, ast.Call):
                cn = _call_name(child)
                if cn:
                    raw.append({"name": cn, "line": getattr(child, "lineno", 0)})
        raw.sort(key=lambda x: x["line"])
        seen: dict[str, int] = {}
        for r in raw:
            if r["name"] not in seen:
                seen[r["name"]] = len(seen) + 1
        for r in raw:
            r["order"] = seen[r["name"]]
        return raw

    def _make_node(func_node, rel_path: str, class_name: str | None = None) -> _Node:
        if class_name:
            node_id = f"{rel_path}:{class_name}.{func_node.name}"
            short = f"{class_name}.{func_node.name}"
            ntype = "method"
        else:
            node_id = f"{rel_path}:{func_node.name}"
            short = func_node.name
            ntype = "function"
        end = getattr(func_node, "end_lineno", func_node.lineno)
        return _Node(
            id=node_id, file=rel_path, class_name=class_name,
            function=func_node.name, short_name=short,
            lineno=func_node.lineno,
            params=len(func_node.args.args),
            lines=end - func_node.lineno,
            docstring=_docstring_first_line(func_node),
            is_async=isinstance(func_node, ast.AsyncFunctionDef),
            decorators=[_extract_decorator_name(d) for d in func_node.decorator_list],
            type=ntype,
            ast_hash=_body_hash(func_node),
        )

    def _visit(body, rel_path: str, class_name: str | None = None):
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                _visit(stmt.body, rel_path, class_name=stmt.name)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node = _make_node(stmt, rel_path, class_name)
                nodes.append(node)
                for c in _collect_calls(stmt):
                    raw_edges.append({
                        "source_id": node.id,
                        "call_name": c["name"],
                        "order": c["order"],
                    })

    def _excluded(path: Path) -> bool:
        return any(part in _EXCLUDE_DIRS for part in path.parts)

    for py_file in sorted(root.rglob("*.py")):
        if _excluded(py_file.relative_to(root)):
            continue
        rel_path = str(py_file.relative_to(root))
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue
        _visit(tree.body, rel_path)

    return _ScanResult(nodes=nodes, raw_edges=raw_edges)


# ── Lightweight resolver ─────────────────────────────────────────────────────

_SKIP_NAMES = {
    "__init__", "__repr__", "__str__", "__eq__", "__ne__", "__hash__",
    "__len__", "__iter__", "__next__", "__enter__", "__exit__",
    "__getattr__", "__setattr__", "__getitem__", "__setitem__",
    "__contains__", "__call__", "__bool__", "__del__",
    "to_dict", "from_dict", "to_proto", "from_proto",
    "join", "get", "set", "update", "delete", "create", "read", "write",
    "close", "open", "run", "start", "stop", "setup", "teardown",
    "validate", "copy", "keys", "values", "items", "append", "extend",
    "pop", "remove", "clear", "add", "name", "apply",
}

_AMBIGUITY_LIMIT = 3


@dataclass
class _ResolvedGraph:
    nodes: list[_Node]
    edges: list[tuple[str, str, int]]  # (source, target, order)

    @property
    def node_by_id(self) -> dict[str, _Node]:
        return {n.id: n for n in self.nodes}

    @property
    def edge_set(self) -> set[tuple[str, str]]:
        return {(s, t) for s, t, _ in self.edges}


def _resolve(scan: _ScanResult) -> _ResolvedGraph:
    """Resolve raw call edges to node-to-node edges."""
    name_idx: dict[str, list[str]] = defaultdict(list)
    qual_idx: dict[str, list[str]] = defaultdict(list)
    by_id: dict[str, _Node] = {}

    for n in scan.nodes:
        by_id[n.id] = n
        if n.function not in _SKIP_NAMES:
            name_idx[n.function].append(n.id)
        if n.class_name:
            qual_idx[f"{n.class_name}.{n.function}"].append(n.id)

    edges: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()

    for e in scan.raw_edges:
        src = e["source_id"]
        call = e["call_name"]
        order = e.get("order", 0)

        targets = qual_idx.get(call) or name_idx.get(call)
        if targets is None:
            continue
        if len(targets) > _AMBIGUITY_LIMIT:
            src_file = by_id[src].file if src in by_id else ""
            same = [t for t in targets if t != src and by_id.get(t) and by_id[t].file == src_file]
            targets = same[:_AMBIGUITY_LIMIT] if same else []
        for tgt in targets:
            if tgt != src and (src, tgt) not in seen:
                seen.add((src, tgt))
                edges.append((src, tgt, order))

    in_deg: dict[str, int] = defaultdict(int)
    out_deg: dict[str, int] = defaultdict(int)
    for s, t, _ in edges:
        out_deg[s] += 1
        in_deg[t] += 1
    for n in scan.nodes:
        n.in_degree = in_deg.get(n.id, 0)
        n.out_degree = out_deg.get(n.id, 0)

    return _ResolvedGraph(nodes=scan.nodes, edges=edges)


# ── GraphDiff ────────────────────────────────────────────────────────────────


@dataclass
class GraphDiff:
    """Structural diff between two code graphs."""

    added_nodes: list[str] = field(default_factory=list)
    removed_nodes: list[str] = field(default_factory=list)
    modified_nodes: list[str] = field(default_factory=list)
    added_edges: list[tuple[str, str]] = field(default_factory=list)
    removed_edges: list[tuple[str, str]] = field(default_factory=list)

    old_graph: _ResolvedGraph | None = field(default=None, repr=False)
    new_graph: _ResolvedGraph | None = field(default=None, repr=False)
    commit_a: str = ""
    commit_b: str = ""

    @property
    def summary(self) -> dict:
        return {
            "added": len(self.added_nodes),
            "removed": len(self.removed_nodes),
            "modified": len(self.modified_nodes),
            "edges_added": len(self.added_edges),
            "edges_removed": len(self.removed_edges),
        }

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added_nodes or self.removed_nodes or self.modified_nodes
            or self.added_edges or self.removed_edges
        )

    def to_dict(self) -> dict:
        return {
            "commit_a": self.commit_a,
            "commit_b": self.commit_b,
            "summary": self.summary,
            "added_nodes": self.added_nodes,
            "removed_nodes": self.removed_nodes,
            "modified_nodes": self.modified_nodes,
            "added_edges": [list(e) for e in self.added_edges],
            "removed_edges": [list(e) for e in self.removed_edges],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ── Comparison ───────────────────────────────────────────────────────────────


def compare_graphs(old: _ResolvedGraph, new: _ResolvedGraph) -> GraphDiff:
    """Compare two resolved graphs by node IDs and AST body hashes."""
    old_ids = {n.id for n in old.nodes}
    new_ids = {n.id for n in new.nodes}
    old_by_id = old.node_by_id
    new_by_id = new.node_by_id

    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    modified = sorted(
        nid for nid in (old_ids & new_ids)
        if old_by_id[nid].ast_hash != new_by_id[nid].ast_hash
    )

    added_edges = sorted(new.edge_set - old.edge_set)
    removed_edges = sorted(old.edge_set - new.edge_set)

    return GraphDiff(
        added_nodes=added,
        removed_nodes=removed,
        modified_nodes=modified,
        added_edges=added_edges,
        removed_edges=removed_edges,
        old_graph=old,
        new_graph=new,
    )


# ── Git helpers ──────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check,
    )


def _has_tracked_changes(repo: Path) -> bool:
    """Check for tracked changes that can be stashed (not merge conflicts)."""
    result = _git(repo, "diff", "--quiet", "HEAD", check=False)
    if result.returncode != 0:
        return True
    result = _git(repo, "diff", "--cached", "--quiet", "HEAD", check=False)
    return result.returncode != 0


def _in_merge(repo: Path) -> bool:
    """Check if repo is in the middle of a merge/rebase."""
    merge_head = repo / ".git" / "MERGE_HEAD"
    rebase_dir = repo / ".git" / "rebase-merge"
    rebase_apply = repo / ".git" / "rebase-apply"
    return merge_head.exists() or rebase_dir.exists() or rebase_apply.exists()


def _current_ref(repo: Path) -> str:
    result = _git(repo, "symbolic-ref", "--short", "HEAD", check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _resolve_commit(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--short", ref).stdout.strip()


def _scan_at_commit(repo: Path, commit: str, scan_subdir: str | None) -> _ResolvedGraph:
    _git(repo, "checkout", "--quiet", "--force", commit)
    scan_root = repo / scan_subdir if scan_subdir else repo
    return _resolve(_scan(str(scan_root)))


# ── Public API ───────────────────────────────────────────────────────────────


def diff_commits(
    repo_path: str | Path,
    commit_a: str,
    commit_b: str,
    scan_subdir: str | None = None,
) -> GraphDiff:
    """Compare code graphs between two git commits.

    Safely stashes working changes, checks out each commit, scans the codebase,
    then restores the original branch. Always cleans up via try/finally.

    Handles repos with uncommitted changes, merge conflicts, and detached HEAD.

    Args:
        repo_path: Path to the git repository root.
        commit_a: The "old" commit (base).
        commit_b: The "new" commit (head).
        scan_subdir: Subdirectory within repo to scan (e.g. "sdk/python/feast").
    """
    repo = Path(repo_path).resolve()
    original_ref = _current_ref(repo)
    did_stash = False
    short_a = _resolve_commit(repo, commit_a)
    short_b = _resolve_commit(repo, commit_b)

    # Abort any in-progress merge/rebase before stashing
    if _in_merge(repo):
        _git(repo, "merge", "--abort", check=False)
        _git(repo, "rebase", "--abort", check=False)

    if _has_tracked_changes(repo):
        result = _git(repo, "stash", "push", "-m", "codegraph-diff-autostash", check=False)
        did_stash = result.returncode == 0

    try:
        old_graph = _scan_at_commit(repo, commit_a, scan_subdir)
        new_graph = _scan_at_commit(repo, commit_b, scan_subdir)
    finally:
        _git(repo, "checkout", "--quiet", "--force", original_ref, check=False)
        if did_stash:
            _git(repo, "stash", "pop", check=False)

    diff = compare_graphs(old_graph, new_graph)
    diff.commit_a = short_a
    diff.commit_b = short_b
    return diff
