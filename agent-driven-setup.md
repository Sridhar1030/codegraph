---
name: setup-codegraph
description: >-
  Clone, install, and configure CodeGraph as an MCP tool for Python code analysis.
  Use when the user wants to set up CodeGraph, needs code structure analysis,
  wants to understand call graphs, or mentions codegraph setup/install.
---

# Setup CodeGraph

This skill bootstraps CodeGraph — a Python static analysis tool that builds
function-level call graphs and exposes them as MCP tools for AI agents.

After setup, you get 10 MCP tools: search_functions, get_function_detail,
impact_analysis, get_flowchart, get_neighbors, file_overview, list_files,
diff_analysis, scan_repo, get_status.

**Only prerequisite: Python 3.11+ installed.**

## Step 1: Clone and Install

```bash
# Pick an install location (default: ~/codegraph)
CODEGRAPH_HOME="${CODEGRAPH_HOME:-$HOME/codegraph}"

# Clone
git clone https://github.com/aniketpalu/codegraph.git "$CODEGRAPH_HOME"

# Install dependencies
pip install -r "$CODEGRAPH_HOME/requirements.txt"
```

Verify the install:

```bash
cd "$CODEGRAPH_HOME" && python -c "from codegraph.server import app; print('OK')"
```

If this prints `OK`, proceed. If it fails, check that Python 3.11+ is active
and `pip install` completed without errors.

## Step 2: Start the Server

```bash
cd "$CODEGRAPH_HOME" && python -m uvicorn codegraph.server:app --host 0.0.0.0 --port 8787 &
```

Verify the server is running:

```bash
curl -s http://localhost:8787/status
```

Expected: JSON with `"repo_path": null` (no repo scanned yet).

**Important:** The server must stay running for MCP tools to work. If it dies,
restart it before using any codegraph tools.

## Step 3: Register as Cursor MCP Server

Find the absolute python path and codegraph location:

```bash
PYTHON_PATH=$(python -c "import sys; print(sys.executable)")
CODEGRAPH_HOME=$(cd "$CODEGRAPH_HOME" && pwd)
echo "Python: $PYTHON_PATH"
echo "CodeGraph: $CODEGRAPH_HOME"
```

Add this entry to `~/.cursor/mcp.json` (create the file if it doesn't exist).
Use the actual paths from above:

```json
{
  "mcpServers": {
    "codegraph": {
      "command": "<PYTHON_PATH>",
      "args": ["-m", "codegraph.mcp_server"],
      "env": {
        "PYTHONPATH": "<CODEGRAPH_HOME>",
        "CODEGRAPH_URL": "http://localhost:8787"
      }
    }
  }
}
```

If `~/.cursor/mcp.json` already exists, merge the `codegraph` entry into the
existing `mcpServers` object. Do not overwrite other servers.

After editing, tell the user to reload MCP servers in Cursor
(Command Palette → `MCP: List Servers`, or restart Cursor).

## Step 4: Verify MCP Tools Work

Once MCP servers are reloaded, test by calling:

```
get_status
```

Expected: "CodeGraph server is running but no repo is loaded."

If you get a connection error, the FastAPI server isn't running — go back to Step 2.

## Step 5: Live Demo

Ask the user which Python repo they'd like to analyze. Present these options:

**Option A — Use the Feast feature store (recommended for demo)**

Feast is an open-source feature store with a large Python SDK (~3,700 functions).
It's a great demo because it has rich call relationships and interesting patterns.

```bash
# Clone Feast if not already present
FEAST_PATH="${HOME}/feast"
if [ ! -d "$FEAST_PATH" ]; then
  git clone https://github.com/feast-dev/feast.git "$FEAST_PATH"
fi
```

Then scan it:

```
scan_repo(path="<FEAST_PATH>/sdk/python/feast", exclude=["tests/", "*_pb2*", "__pycache__/"])
```

**Option B — Use the user's own repo**

Ask the user for the absolute path to their Python project and scan it:

```
scan_repo(path="/path/to/their/project", exclude=["tests/", "__pycache__/"])
```

## Step 6: Prove It Works — Run the Demo Query

After scanning completes, run this full analysis to demonstrate all tools working:

### Demo: "How does get_historical_features work for Oracle offline store?"

Execute these tool calls in sequence and present the results to the user:

1. **Find the function:**
```
search_functions(query="OracleOfflineStore.get_historical_features")
```

2. **Get full detail (callers + callees):**
```
get_function_detail(node_id="feast/infra/offline_stores/contrib/oracle_offline_store/oracle.py:OracleOfflineStore.get_historical_features")
```

3. **Trace the call chain:**
```
get_flowchart(node_id="feast/infra/offline_stores/contrib/oracle_offline_store/oracle.py:OracleOfflineStore.get_historical_features", depth=3)
```

4. **Assess blast radius:**
```
impact_analysis(node_id="feast/infra/offline_stores/contrib/oracle_offline_store/oracle.py:OracleOfflineStore.get_historical_features", depth=5)
```

5. **See the file structure:**
```
file_overview(file_path="feast/infra/offline_stores/contrib/oracle_offline_store/oracle.py")
```

Present a summary to the user explaining:
- The function is a `@staticmethod` (41 lines) that orchestrates 7 calls
- It establishes an Oracle connection via ibis, computes date ranges, builds an entity DataFrame from Oracle tables, then delegates to the shared `get_historical_features_ibis` for point-in-time joins
- It has 524 downstream functions (massive blast radius) and 62 upstream callers
- The Oracle-specific logic is thin — connection + table reading — while the heavy lifting is in the shared ibis module

## Step 7: Open the Web UI

Finally, open the CodeGraph visualization in the browser:

```bash
open http://localhost:8787
```

Tell the user:
- The web UI shows the same graph data as the MCP tools, but visually
- They can search, click nodes to see impact, toggle between function/class view
- The path field should already show the scanned repo
- Entry points are purple squares, control points are red circles, dispatch nodes are orange

---

## Using CodeGraph Tools (Reference)

### Typical workflow

1. `get_status` — check what's loaded
2. `scan_repo` — scan a repo if needed
3. `search_functions` — find functions by name/file/type
4. `get_function_detail` — get full info on a specific function
5. `impact_analysis` — who's affected if this function changes?
6. `get_flowchart` — trace the call chain as an indented tree
7. `get_neighbors` — see direct callers/callees
8. `file_overview` — all functions in a file
9. `list_files` — all files sorted by function count
10. `diff_analysis` — compare two git commits structurally

### Node IDs

Functions are identified by qualified IDs like:
- `path/to/file.py:function_name`
- `path/to/file.py:ClassName.method_name`

Use `search_functions` to discover node IDs before passing them to other tools.

### When to use which tool

| Question | Tool |
|----------|------|
| "What functions exist in this file?" | `file_overview` |
| "What does this function call?" | `get_function_detail` or `get_flowchart` |
| "What calls this function?" | `get_function_detail` or `impact_analysis` |
| "What breaks if I change this?" | `impact_analysis` |
| "How does execution flow from here?" | `get_flowchart` |
| "What changed between these commits?" | `diff_analysis` |
| "Find functions related to X" | `search_functions` |
| "What are the most connected functions?" | `search_functions` with `min_in` or `min_out` |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Cannot connect to CodeGraph" | Server not running. Start it: `cd $CODEGRAPH_HOME && python -m uvicorn codegraph.server:app --port 8787 &` |
| "No graph loaded" | Run `scan_repo` with the repo path first |
| MCP server not showing in Cursor | Check `~/.cursor/mcp.json` is valid JSON. Reload MCP servers. |
| Import errors on startup | Run `pip install -r $CODEGRAPH_HOME/requirements.txt` |
| "Node not found" | Use `search_functions` to find the correct qualified node ID |
