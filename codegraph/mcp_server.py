"""CodeGraph MCP Server — exposes code graph analysis tools for AI agents.

Wraps the CodeGraph REST API (FastAPI server on localhost:8787) as MCP tools.
Each tool maps to one or more REST endpoints and returns agent-friendly text.

Usage:
    python -m codegraph.mcp_server              # default localhost:8787
    CODEGRAPH_URL=http://host:port python -m codegraph.mcp_server
"""

from __future__ import annotations

import json
import os
import textwrap
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

CODEGRAPH_URL = os.environ.get("CODEGRAPH_URL", "http://localhost:8787")

mcp = FastMCP(
    "codegraph",
    instructions=textwrap.dedent("""\
        CodeGraph — static analysis tools for Python codebases.

        Typical workflow:
        1. Call `get_status` to check if a graph is loaded.
        2. If not loaded, call `scan_repo` with the repo path.
        3. Use `search_functions`, `get_function_detail`, `impact_analysis`,
           `get_neighbors`, `get_flowchart`, `file_overview`, or `list_files`
           to explore the codebase.
        4. Use `diff_analysis` to compare commits.
        5. For KFP (Kubeflow Pipelines) repos: use `list_kfp_pipelines` to
           discover pipelines, then `get_kfp_pipeline` to inspect a pipeline's
           component DAG with typed inputs/outputs and data-flow edges.

        Node IDs are qualified names like "file/path.py:ClassName.method_name"
        or "file/path.py:function_name". Use `search_functions` to discover them.
    """),
)

_client = httpx.Client(base_url=CODEGRAPH_URL, timeout=60.0)


def _get(path: str, params: dict | None = None) -> Any:
    r = _client.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict | None = None) -> Any:
    r = _client.post(path, json=body or {})
    r.raise_for_status()
    return r.json()


def _fmt_node_short(n: dict) -> str:
    """One-line summary of a node for list contexts."""
    tags = []
    if n.get("type") == "kfp_component":
        tags.append("KFP_COMPONENT")
    if n.get("type") == "kfp_pipeline":
        tags.append("KFP_PIPELINE")
    if n.get("color_class") == "entry":
        tags.append("ENTRY")
    if n.get("type") == "dispatch":
        tags.append("DISPATCH")
    if n.get("is_generated"):
        tags.append("GENERATED")
    tag_str = f" [{','.join(tags)}]" if tags else ""
    return (
        f"  {n['id']}  "
        f"(in:{n.get('in_degree', 0)} out:{n.get('out_degree', 0)} "
        f"lines:{n.get('lines', '?')}){tag_str}"
    )


def _fmt_node_detail(n: dict) -> str:
    """Multi-line detail of a node."""
    lines = [
        f"Function: {n.get('short_name', n.get('id', '?'))}",
        f"  ID: {n['id']}",
        f"  File: {n.get('file', '?')}:{n.get('lineno', '?')}",
        f"  Type: {n.get('type', '?')}",
        f"  In-degree: {n.get('in_degree', 0)}  Out-degree: {n.get('out_degree', 0)}",
        f"  Lines of code: {n.get('lines', '?')}",
        f"  Async: {n.get('is_async', False)}",
    ]
    if n.get("class_name"):
        lines.append(f"  Class: {n['class_name']}")
    if n.get("decorators"):
        lines.append(f"  Decorators: @{', @'.join(n['decorators'])}")
    if n.get("docstring"):
        lines.append(f"  Docstring: {n['docstring'][:200]}")
    if n.get("calls"):
        callees = [f"    → {c['name']} (#{c.get('order', '?')} {c.get('edge_type', '')})"
                   for c in sorted(n["calls"], key=lambda c: c.get("order", 0))]
        lines.append(f"  Calls ({len(callees)}):")
        lines.extend(callees[:30])
        if len(callees) > 30:
            lines.append(f"    ... and {len(callees) - 30} more")
    if n.get("called_by"):
        callers = [f"    ← {c['name']} ({c.get('edge_type', '')})" for c in n["called_by"]]
        lines.append(f"  Called by ({len(callers)}):")
        lines.extend(callers[:30])
        if len(callers) > 30:
            lines.append(f"    ... and {len(callers) - 30} more")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_status() -> str:
    """Check if CodeGraph server is running and whether a code graph is loaded.

    Returns the current repo path, scan time, and node/edge counts.
    Call this first to understand the current state before using other tools.
    """
    try:
        data = _get("/status")
    except httpx.ConnectError:
        return (
            f"ERROR: Cannot connect to CodeGraph at {CODEGRAPH_URL}. "
            "Is the server running? Start it with: python -m codegraph.server"
        )
    if not data.get("repo_path"):
        return "CodeGraph server is running but no repo is loaded. Use scan_repo to scan a codebase."
    return (
        f"CodeGraph is running.\n"
        f"  Repo: {data['repo_path']}\n"
        f"  Last scan: {data.get('last_scan_time', '?')} ({data.get('scan_duration', '?')}s)\n"
        f"  Nodes: {data.get('total_nodes', 0)}\n"
        f"  Edges: {data.get('total_edges', 0)}"
    )


@mcp.tool()
def scan_repo(path: str, exclude: list[str] | None = None) -> str:
    """Scan a Python repository and build a code graph.

    This parses all .py files, resolves function calls, and builds a
    dependency graph. Takes ~1-3 seconds for typical repos.

    Args:
        path: Absolute path to the Python repository to scan.
        exclude: Glob patterns to exclude (e.g. ["tests/", "*_pb2*"]).
    """
    try:
        data = _post("/scan", {"path": path, "exclude": exclude or []})
    except httpx.ConnectError:
        return f"ERROR: Cannot connect to CodeGraph at {CODEGRAPH_URL}."
    except httpx.HTTPStatusError as e:
        return f"ERROR: Scan failed — {e.response.json().get('detail', str(e))}"
    return (
        f"Scan complete.\n"
        f"  Nodes: {data.get('total_nodes', '?')}\n"
        f"  Edges: {data.get('total_edges', '?')}\n"
        f"  Files: {data.get('files', '?')}\n"
        f"  Node types: {json.dumps(data.get('node_types', {}))}\n"
        f"  Edge types: {json.dumps(data.get('edge_types', {}))}"
    )


@mcp.tool()
def search_functions(
    query: str = "",
    file: str | None = None,
    type: str | None = None,
    min_in: int = 0,
    min_out: int = 0,
    include_generated: bool = False,
    limit: int = 25,
) -> str:
    """Search for functions in the code graph by name, file, or type.

    Use this to discover function IDs needed by other tools. Searches across
    function names, file paths, and qualified IDs.

    Args:
        query: Search string — matches against function name, file path, and ID.
               Leave empty and use filters to browse.
        file: Filter to a specific file path (relative to repo root).
        type: Filter by node type: "function", "method", "classmethod",
              "staticmethod", "property", "external", "dispatch".
        min_in: Minimum in-degree (callers). Use to find widely-used utilities.
        min_out: Minimum out-degree (callees). Use to find orchestrators.
        include_generated: Include auto-generated code (e.g. protobuf stubs).
        limit: Max results to return (default 25).
    """
    params = {
        "search": query, "file": file, "type": type,
        "min_in": min_in, "min_out": min_out,
        "include_generated": str(include_generated).lower(),
        "limit": limit,
    }
    data = _get("/graph/nodes", params)
    if not data:
        return "No functions found matching your query."
    lines = [f"Found {len(data)} function(s):"]
    for n in data:
        lines.append(_fmt_node_short(n))
    return "\n".join(lines)


@mcp.tool()
def get_function_detail(node_id: str) -> str:
    """Get full details of a function including its callers and callees.

    Returns file location, type, degree, decorators, docstring, and complete
    lists of what it calls and what calls it (with call order).

    Args:
        node_id: Qualified function ID (e.g. "feast/feature_store.py:FeatureStore.apply").
                 Use search_functions to find IDs.
    """
    try:
        data = _get(f"/graph/node/{node_id}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Node not found: {node_id}. Use search_functions to find valid IDs."
        raise
    return _fmt_node_detail(data)


@mcp.tool()
def impact_analysis(node_id: str, depth: int = 5) -> str:
    """Analyze the impact of changing a function.

    Shows all downstream functions (what breaks if this changes) and upstream
    functions (what depends on this). Use for change impact assessment and
    code review.

    Args:
        node_id: Qualified function ID.
        depth: How many levels deep to trace (1-10, default 5).
    """
    try:
        data = _get(f"/graph/node/{node_id}/impact", {"depth": depth})
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Node not found: {node_id}"
        raise

    focus = data["focus"]
    lines = [
        f"Impact analysis for: {focus.get('short_name', node_id)}",
        f"  File: {focus.get('file', '?')}:{focus.get('lineno', '?')}",
        f"  Downstream (affected if this changes): {data['downstream_count']}",
        f"  Upstream (depends on this): {data['upstream_count']}",
    ]
    if data["downstream"]:
        lines.append("\nDownstream functions:")
        for n in data["downstream"][:30]:
            lines.append(f"  → {n.get('short_name', n.get('id', '?'))}  ({n.get('file', '?')})")
        if data["downstream_count"] > 30:
            lines.append(f"  ... and {data['downstream_count'] - 30} more")
    if data["upstream"]:
        lines.append("\nUpstream functions:")
        for n in data["upstream"][:30]:
            lines.append(f"  ← {n.get('short_name', n.get('id', '?'))}  ({n.get('file', '?')})")
        if data["upstream_count"] > 30:
            lines.append(f"  ... and {data['upstream_count'] - 30} more")
    return "\n".join(lines)


@mcp.tool()
def get_neighbors(node_id: str, direction: str = "both", depth: int = 1) -> str:
    """Get the immediate neighborhood of a function in the call graph.

    Shows direct callers and/or callees at a configurable depth.

    Args:
        node_id: Qualified function ID.
        direction: "out" (what it calls), "in" (what calls it), or "both".
        depth: Neighborhood radius (1-3 recommended, default 1).
    """
    try:
        data = _get(f"/graph/node/{node_id}/neighbors", {"direction": direction, "depth": depth})
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Node not found or no graph loaded: {node_id}"
        raise

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    lines = [
        f"Neighborhood of {node_id} (direction={direction}, depth={depth}):",
        f"  {len(nodes)} nodes, {len(edges)} edges",
        "\nNodes:",
    ]
    for n in sorted(nodes, key=lambda x: x.get("depth", 0)):
        lines.append(_fmt_node_short(n))
    if edges:
        lines.append(f"\nEdges ({len(edges)}):")
        for e in edges[:50]:
            src_name = e.get("source", "?").split(":")[-1] if ":" in e.get("source", "") else e.get("source", "?")
            tgt_name = e.get("target", "?").split(":")[-1] if ":" in e.get("target", "") else e.get("target", "?")
            order_str = f" #{e['order']}" if e.get("order") else ""
            lines.append(f"  {src_name} → {tgt_name}{order_str} ({e.get('type', 'resolved')})")
        if len(edges) > 50:
            lines.append(f"  ... and {len(edges) - 50} more edges")
    return "\n".join(lines)


@mcp.tool()
def get_flowchart(node_id: str, depth: int = 5) -> str:
    """Get the call chain tree starting from a function.

    Returns a hierarchical view: the function → what it calls → what those call,
    rendered as an indented tree. Useful for understanding execution flow.

    Args:
        node_id: Qualified function ID (starting point of the flowchart).
        depth: How deep to trace calls (1-10, default 5).
    """
    try:
        data = _get(f"/graph/node/{node_id}/flowchart", {"depth": depth})
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Node not found: {node_id}"
        raise

    def _render_tree(node: dict, indent: int = 0) -> list[str]:
        prefix = "  " * indent
        connector = "→ " if indent > 0 else ""
        order_str = f"#{node.get('order', '')} " if node.get("order") else ""
        edge_str = f" [{node['edge_type']}]" if node.get("edge_type", "resolved") != "resolved" else ""
        result = [f"{prefix}{connector}{order_str}{node['name']}  ({node.get('file', '?')}){edge_str}"]
        for child in node.get("children", []):
            result.extend(_render_tree(child, indent + 1))
        return result

    lines = [f"Call flowchart from {data.get('name', node_id)}:"]
    lines.extend(_render_tree(data))
    return "\n".join(lines)


@mcp.tool()
def file_overview(file_path: str) -> str:
    """Get all functions defined in a specific file with their relationships.

    Useful for understanding a file's structure — what it defines and how those
    functions relate to the rest of the codebase.

    Args:
        file_path: Relative file path within the scanned repo (e.g. "feast/feature_store.py").
    """
    data = _get(f"/graph/file/{file_path}")
    if not data:
        return f"No functions found in {file_path}. Check the path is relative to the repo root."
    lines = [f"File: {file_path} — {len(data)} function(s):\n"]
    for n in sorted(data, key=lambda x: x.get("lineno", 0)):
        kind = n.get("type", "function")
        async_str = "async " if n.get("is_async") else ""
        class_str = f"{n['class_name']}." if n.get("class_name") else ""
        lines.append(
            f"  L{n.get('lineno', '?'):>4}  {async_str}{class_str}{n.get('function', '?')}  "
            f"({kind}, in:{n.get('in_degree', 0)} out:{n.get('out_degree', 0)}, "
            f"{n.get('lines', '?')} lines)"
        )
    return "\n".join(lines)


@mcp.tool()
def list_files() -> str:
    """List all scanned files with their function counts.

    Returns files sorted by function count (most functions first).
    Use to identify the most complex files in the codebase.
    """
    data = _get("/graph/files")
    if not data:
        return "No files found. Is a graph loaded? Check with get_status."
    lines = [f"Scanned {len(data)} files:\n"]
    for f in data:
        lines.append(f"  {f['node_count']:>3} functions  {f['file']}")
    return "\n".join(lines)


@mcp.tool()
def diff_analysis(
    commit_a: str,
    commit_b: str,
    repo: str | None = None,
    subdir: str | None = None,
) -> str:
    """Compare two git commits and show what functions changed.

    Shows added, removed, and modified functions between commits. If a graph
    is loaded, also shows impact analysis for each changed function.

    Args:
        commit_a: The older commit (SHA, branch name, or "HEAD~N").
        commit_b: The newer commit (SHA, branch name, or "HEAD").
        repo: Repository path (uses the currently scanned repo if omitted).
        subdir: Subdirectory to scope the diff to (e.g. "sdk/python/feast").
    """
    body: dict[str, Any] = {"commit_a": commit_a, "commit_b": commit_b}
    if repo:
        body["repo"] = repo
    if subdir:
        body["subdir"] = subdir

    try:
        data = _post("/graph/diff", body)
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e)) if e.response.headers.get("content-type", "").startswith("application/json") else str(e)
        return f"Diff failed: {detail}"

    s = data.get("summary", {})
    lines = [
        f"Diff: {data.get('commit_a', commit_a)} → {data.get('commit_b', commit_b)}",
        f"  Added: {s.get('added', 0)} functions",
        f"  Removed: {s.get('removed', 0)} functions",
        f"  Modified: {s.get('modified', 0)} functions",
        f"  Edges: +{s.get('edges_added', 0)} / -{s.get('edges_removed', 0)}",
    ]

    impact = data.get("impact", {})

    if data.get("modified_nodes"):
        lines.append("\nModified functions:")
        for nid in data["modified_nodes"]:
            name = nid.split(":")[-1] if ":" in nid else nid
            imp = impact.get(nid, {})
            imp_str = ""
            if imp:
                imp_str = f"  [impact: ↓{imp.get('downstream_count', 0)} ↑{imp.get('upstream_count', 0)}]"
            lines.append(f"  ~ {name}{imp_str}")

    if data.get("added_nodes"):
        lines.append("\nAdded functions:")
        for nid in data["added_nodes"]:
            name = nid.split(":")[-1] if ":" in nid else nid
            imp = impact.get(nid, {})
            imp_str = ""
            if imp:
                imp_str = f"  [impact: ↓{imp.get('downstream_count', 0)} ↑{imp.get('upstream_count', 0)}]"
            lines.append(f"  + {name}{imp_str}")

    if data.get("removed_nodes"):
        lines.append("\nRemoved functions:")
        for nid in data["removed_nodes"]:
            name = nid.split(":")[-1] if ":" in nid else nid
            lines.append(f"  - {name}")

    if not s.get("added") and not s.get("removed") and not s.get("modified"):
        lines.append("\nNo structural changes between these commits.")

    return "\n".join(lines)


@mcp.tool()
def list_kfp_pipelines() -> str:
    """List all KFP (Kubeflow Pipelines) discovered in the scanned codebase.

    Returns pipeline names, file locations, and task counts for every function
    decorated with @dsl.pipeline. Use this to discover pipeline IDs needed by
    get_kfp_pipeline.

    Requires a scanned repo that contains KFP code (@dsl.component / @dsl.pipeline).
    """
    try:
        data = _get("/graph/kfp/pipelines")
    except httpx.ConnectError:
        return f"ERROR: Cannot connect to CodeGraph at {CODEGRAPH_URL}."

    if not data:
        return "No KFP pipelines found. Scan a repo that uses @dsl.component / @dsl.pipeline."

    lines = [f"Found {len(data)} KFP pipeline(s):\n"]
    for p in data:
        lines.append(
            f"  {p['name']}\n"
            f"    ID: {p['pipeline_id']}\n"
            f"    File: {p.get('file', '?')}:{p.get('lineno', '?')}\n"
            f"    Tasks: {p.get('task_count', 0)}  Components: {len(p.get('component_node_ids', []))}\n"
            f"    Data edges: {p.get('edge_count', 0)}"
        )
    return "\n".join(lines)


@mcp.tool()
def get_kfp_pipeline(pipeline_id: str) -> str:
    """Get the full DAG for a specific KFP pipeline.

    Returns the pipeline's component nodes with their typed parameters
    (Input/Output annotations) and all data-dependency edges showing how
    data flows between components.

    Args:
        pipeline_id: Pipeline ID from list_kfp_pipelines (e.g. "pipeline.py:ml_pipeline").
    """
    try:
        data = _get(f"/graph/kfp/pipeline/{pipeline_id}")
    except httpx.ConnectError:
        return f"ERROR: Cannot connect to CodeGraph at {CODEGRAPH_URL}."
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Pipeline not found: {pipeline_id}. Use list_kfp_pipelines to find valid IDs."
        raise

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    stats = data.get("stats", {})

    lines = [
        f"KFP Pipeline: {stats.get('name', pipeline_id)}",
        f"  File: {stats.get('file', '?')}:{stats.get('lineno', '?')}",
        f"  Components: {len([n for n in nodes if n.get('type') == 'kfp_component'])}",
        f"  Data edges: {len([e for e in edges if (e.get('type') or '').startswith('data_dependency')])}",
        "\nComponents:",
    ]

    for n in nodes:
        if n.get("type") != "kfp_component":
            continue
        params = n.get("kfp_params", [])
        inputs = [p for p in params if p["kind"] == "input"]
        outputs = [p for p in params if p["kind"] == "output"]
        lines.append(f"\n  @component {n.get('short_name', n.get('id', '?'))}")
        lines.append(f"    ID: {n['id']}")
        if inputs:
            lines.append("    Inputs:")
            for p in inputs:
                ann = f": {p['annotation']}" if p.get("annotation") else ""
                lines.append(f"      • {p['name']}{ann}")
        if outputs:
            lines.append("    Outputs:")
            for p in outputs:
                ann = f": {p['annotation']}" if p.get("annotation") else ""
                lines.append(f"      → {p['name']}{ann}")

    data_edges = [e for e in edges if (e.get("type") or "").startswith("data_dependency")]
    if data_edges:
        lines.append("\nData flow:")
        node_map = {n["id"]: n.get("short_name", n["id"]) for n in nodes}
        for e in data_edges:
            src_name = node_map.get(e["source"], e["source"])
            tgt_name = node_map.get(e["target"], e["target"])
            etype = e.get("type", "")
            label = etype.split(":", 1)[1] if ":" in etype else ""
            lines.append(f"  {src_name} ──({label})──▸ {tgt_name}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
