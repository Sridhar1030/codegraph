# CodeGraph

A static analysis tool that builds a function-level call graph from Python codebases and serves it as an interactive web service.

CodeGraph scans Python source files using AST, extracts every function and method as a node, resolves call relationships as edges, and provides a REST API + browser-based visualization for exploring the graph.

## What it does

- **Scans** any Python codebase — extracts functions, methods, classes, and call relationships
- **Resolves** ambiguous calls using 4-level name resolution (self.method, type-tracked variables, import-based narrowing, polymorphic dispatch)
- **Stores** the graph in NetworkX with a swappable backend (GraphStore ABC)
- **Serves** an interactive web UI with D3.js force-directed layout, search, impact analysis, and flowchart generation
- **Exposes** a REST API for programmatic queries — search, impact analysis, flowchart, file-level views

## Quick Start

```bash
pip install -r requirements.txt
python codegraph_cli.py serve --port 8787
```

Open [http://localhost:8787](http://localhost:8787), enter a path to a Python project, and click **Scan**.

## Web UI Features

- **Left-to-right flow layout** — entry points on the left, utilities on the right, depth lanes labeled
- **Search** — type a function name, matching nodes highlight and the view zooms to them
- **Impact analysis** — click any node to see upstream (what depends on it) and downstream (what it affects)
- **Persistent multi-select** — legend clicks, node clicks, and edge clicks accumulate selections
- **Directory filter** — checkbox panel to show/hide top-level directories
- **Class/function toggle** — collapse methods into class-level nodes
- **Light/dark theme** — toggle with localStorage persistence
- **Call order numbers** — green numbered circles show the order of outgoing calls
- **Edge tooltips** — hover an edge to see source→target, call order, downstream/upstream impact counts

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/scan` | Scan a repo: `{"path": "/abs/path", "exclude": []}` |
| `POST` | `/resync` | Re-scan the currently loaded repo |
| `GET` | `/status` | Current state: loaded repo, node/edge counts, scan time |
| `GET` | `/graph/stats` | Full graph statistics |
| `GET` | `/graph/nodes?search=&type=&min_in=&min_out=` | Search/filter nodes |
| `GET` | `/graph/node/{id}` | Single node with calls and called_by lists |
| `GET` | `/graph/node/{id}/impact?depth=5` | Upstream + downstream impact analysis |
| `GET` | `/graph/node/{id}/flowchart?depth=5` | Call chain as a tree structure |
| `GET` | `/graph/node/{id}/neighbors?direction=both&depth=1` | Neighborhood subgraph |
| `GET` | `/graph/files` | File list with node counts |
| `GET` | `/graph/file/{path}` | All nodes in a specific file |
| `GET` | `/graph/full` | Complete graph data for visualization |

## MCP Server (AI Agent Integration)

CodeGraph can be used as an MCP tool server, allowing AI agents in Cursor to query the code graph directly.

### Setup

Add to `~/.cursor/mcp.json`:

```json
{
  "codegraph": {
    "command": "python",
    "args": ["-m", "codegraph.mcp_server"],
    "env": {
      "PYTHONPATH": "/path/to/parent/of/codegraph",
      "CODEGRAPH_URL": "http://localhost:8787"
    }
  }
}
```

The FastAPI server must be running first (`python codegraph_cli.py serve --port 8787`).

### Available Tools

| Tool | Description |
|------|-------------|
| `get_status` | Check if server is running and graph is loaded |
| `scan_repo` | Scan a Python repo and build the call graph |
| `search_functions` | Find functions by name, file, type, or degree |
| `get_function_detail` | Full detail — file, line, callers, callees, docstring |
| `impact_analysis` | Who's affected if this function changes? |
| `get_neighbors` | Direct callers/callees at configurable depth |
| `get_flowchart` | Call chain tree from a function to its leaves |
| `file_overview` | All functions in a file with line numbers and stats |
| `list_files` | All scanned files sorted by function count |
| `diff_analysis` | Compare two git commits — structural changes + impact |

### Example Agent Prompt

```
Use the codegraph MCP tools to answer:
How does get_historical_features work for the Oracle offline store in Feast?

1. search_functions to find the function
2. get_function_detail for callers/callees
3. get_flowchart to trace the call chain
4. impact_analysis for blast radius
```

## Architecture

```
codegraph/
  scanner.py       AST scanning — extract functions, methods, raw calls
  resolver.py      4-level name resolution with dispatch nodes
  models.py        FunctionNode, CallEdge, CodeGraph dataclasses
  store.py         GraphStore ABC + NetworkXStore (swappable to Neo4j)
  server.py        FastAPI web service + built-in D3.js UI
  mcp_server.py    MCP tool server for AI agent integration
  filters.py       File/directory exclusion patterns
  diff.py          Git diff — compare graphs between commits
  exporters/
    html_export.py   Standalone HTML export (offline use)
    json_export.py   Raw JSON export
    diff_html.py     Diff visualization

codegraph_cli.py   CLI entry point
```

## How Resolution Works

When the code calls `obj.apply()`, the resolver determines the target through 4 levels:

1. **Level A** — `self.method()` → resolves to the enclosing class (unambiguous)
2. **Level B** — Type-tracked variables → `registry = Registry()` then `registry.apply()` resolves to `Registry.apply`
3. **Level B+** — Import-based narrowing → uses `from feast.registry import Registry` to disambiguate
4. **Level C** — Polymorphic dispatch → 2+ candidates create a **dispatch node** with option edges to all possibilities

Dispatch nodes represent ambiguity honestly — no silent false positives. Every uncertain resolution is visibly marked.

## Generated Code Detection

Functions from generated files (`*_pb2.py`, `*_pb2_grpc.py`, etc.) are tagged with `is_generated=True`. The API and UI let you filter them in or out — they stay in the graph but don't clutter the view by default.

## Storage Layer

The `GraphStore` ABC defines the query interface. The current implementation (`NetworkXStore`) keeps everything in-process with pickle persistence. The abstraction allows swapping to Neo4j or another graph database without changing the API, UI, or any consumer.

## Example: Feast SDK

Scanning the [Feast](https://github.com/feast-dev/feast) feature store SDK:

- 549 Python files, 329 with functions
- 3,627 functions/methods, 569 classes
- ~4,500 resolved edges
- Scan time: ~1.5 seconds

## License

MIT
