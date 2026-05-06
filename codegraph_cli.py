#!/usr/bin/env python3
"""CodeGraph CLI — scan a Python codebase and produce call-graph output."""

from __future__ import annotations

import argparse
import os
import sys

from codegraph.scanner import scan_codebase
from codegraph.resolver import build_graph, filter_graph
from codegraph.exporters.json_export import export_json
from codegraph.exporters.html_export import export_html
from codegraph.store import NetworkXStore


def _cmd_diff(args: argparse.Namespace) -> None:
    """Compare code graphs between two git commits."""
    import time
    from pathlib import Path
    from codegraph.diff import diff_commits
    from codegraph.exporters.diff_html import generate_diff_html

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"Error: {repo} is not a directory", file=sys.stderr)
        sys.exit(1)

    scan_sub = args.subdir or None
    print(f"Diffing {args.commit_a} -> {args.commit_b} in {repo}")
    if scan_sub:
        print(f"  Scanning subdirectory: {scan_sub}")

    t0 = time.time()
    diff = diff_commits(repo, args.commit_a, args.commit_b, scan_subdir=scan_sub)
    elapsed = time.time() - t0

    s = diff.summary
    print(f"\nDiff complete in {elapsed:.1f}s")
    print(f"  Added:    {s['added']} functions")
    print(f"  Removed:  {s['removed']} functions")
    print(f"  Modified: {s['modified']} functions")
    print(f"  Edges +{s['edges_added']} / -{s['edges_removed']}")

    if not diff.has_changes:
        print("\nNo structural changes detected between these commits.")

    if args.output:
        out = Path(args.output)
        out.write_text(diff.to_json(), encoding="utf-8")
        print(f"\nJSON written to {out}")

    if args.html:
        html_path = Path(args.html)
        count = generate_diff_html(diff, html_path)
        print(f"HTML written to {html_path} ({count} nodes)")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="codegraph",
        description="Scan a Python codebase and build a call graph.",
    )
    sub = parser.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="Scan a Python codebase")
    scan_p.add_argument("path", help="Root directory to scan")
    scan_p.add_argument("--exclude", nargs="*", default=[], help="Glob patterns to exclude")
    scan_p.add_argument("--output", "-o", help="Write JSON graph to this path")
    scan_p.add_argument("--html", help="Write interactive HTML to this path")

    serve_p = sub.add_parser("serve", help="Start the CodeGraph web service")
    serve_p.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    serve_p.add_argument("--port", type=int, default=8787, help="Port to bind to")
    serve_p.add_argument("--reload", action="store_true", help="Auto-reload on code changes")

    diff_p = sub.add_parser("diff", help="Compare graphs between two git commits")
    diff_p.add_argument("commit_a", help="Base commit (old)")
    diff_p.add_argument("commit_b", help="Head commit (new)")
    diff_p.add_argument("repo", help="Path to the git repository")
    diff_p.add_argument("--subdir", help="Subdirectory within repo to scan")
    diff_p.add_argument("--output", "-o", help="Write JSON diff to this path")
    diff_p.add_argument("--html", help="Write HTML diff visualization to this path")

    args = parser.parse_args()
    if args.command == "serve":
        _cmd_serve(args)
        return
    if args.command == "diff":
        _cmd_diff(args)
        return
    if args.command != "scan":
        parser.print_help()
        sys.exit(1)

    root_dir = args.path
    if not os.path.isdir(root_dir):
        print(f"Error: {root_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {root_dir} ...")
    scan = scan_codebase(root_dir, exclude_patterns=args.exclude or [])
    print(f"  Found {len(scan.nodes)} functions/methods")
    print(f"  Found {sum(len(v) for v in scan.raw_calls.values())} raw call references")
    print(f"  Classes discovered: {len(scan.class_names)}")
    print(f"  Type-tracked scopes: {len(scan.type_scopes)}")
    print(f"  Dynamic dispatch sites: {sum(len(v) for v in scan.dynamic_lines.values())}")
    print(f"  Internal import paths: {len(scan.imports['internal'])}")
    print(f"  External packages: {len(scan.imports['external'])}")

    print("Building graph with qualified resolution...")
    graph = build_graph(scan)
    print(f"  Resolved {len(graph.edges)} edges (total)")
    print(f"  Dispatch nodes: {graph.stats.get('dispatch_nodes', 0)}")
    print(f"  Option edges: {graph.stats.get('option_edges', 0)}")
    print(f"  Dynamic edges: {graph.stats.get('dynamic_edges', 0)}")
    print(f"  Total nodes (with external + dispatch): {len(graph.nodes)}")

    filtered = filter_graph(graph, min_in=1, min_out=1)
    print(f"  Pre-filtered to {len(filtered.nodes)} nodes, {len(filtered.edges)} edges")

    if args.output:
        export_json(graph, args.output)
        print(f"  JSON written to {args.output}")

    if args.html:
        n_nodes, n_edges = export_html(filtered, args.html)
        print(f"  HTML written to {args.html}")
        print(f"  Embedded data: {n_nodes} nodes, {n_edges} edges (client slider further filters)")

    top10 = sorted(
        [n for n in graph.nodes if n.node_type not in ("external", "dispatch")],
        key=lambda n: n.in_degree,
        reverse=True,
    )[:10]
    print("\nTop 10 Control Points (highest in-degree):")
    for i, n in enumerate(top10):
        print(f"  {i+1}. {n.short_name} (in:{n.in_degree}, out:{n.out_degree}) — {n.file}:{n.lineno}")

    dispatch = [n for n in graph.nodes if n.node_type == "dispatch"]
    if dispatch:
        print(f"\nDispatch Nodes ({len(dispatch)}):")
        for n in sorted(dispatch, key=lambda n: n.out_degree, reverse=True)[:10]:
            print(f"  {n.short_name} -> {n.out_degree} candidates")


def _cmd_serve(args: argparse.Namespace) -> None:
    """Start the CodeGraph web service."""
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn is required. Install with: pip install uvicorn", file=sys.stderr)
        sys.exit(1)

    print(f"Starting CodeGraph server on {args.host}:{args.port}")
    uvicorn.run(
        "codegraph.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
