"""CodeGraph FastAPI web service with built-in D3.js visualization UI."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from codegraph.store import NetworkXStore

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("codegraph.server")

app = FastAPI(title="CodeGraph", version="0.1.0")

_store = NetworkXStore()
_scan_state = {
    "repo_path": None,
    "exclude_patterns": [],
    "last_scan_time": None,
    "scan_duration": None,
    "scanning": False,
}


class ScanRequest(BaseModel):
    path: str
    exclude: list[str] = []


def _do_scan(path: str, exclude: list[str]) -> dict:
    from codegraph.scanner import scan_codebase
    from codegraph.resolver import build_graph

    resolved = str(Path(path).resolve())
    if not Path(resolved).is_dir():
        log.warning("Scan rejected — path not found: %s", path)
        raise HTTPException(status_code=400, detail=f"Path not found: {path}")

    _scan_state["scanning"] = True
    _scan_state["repo_path"] = resolved
    _scan_state["exclude_patterns"] = exclude

    log.info("Starting scan of %s (exclude=%s)", resolved, exclude)
    t0 = time.time()
    try:
        scan = scan_codebase(resolved, exclude_patterns=exclude)
        log.info("Scan complete: %d functions, %d classes, %d files with imports",
                 len(scan.nodes), len(scan.class_names), len(scan.file_imports))

        graph = build_graph(scan)
        log.info("Graph built: %d nodes, %d edges, %d dispatch nodes",
                 len(graph.nodes), len(graph.edges), graph.stats.get("dispatch_nodes", 0))

        _store.load(graph)
        elapsed = time.time() - t0
        _scan_state["last_scan_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _scan_state["scan_duration"] = round(elapsed, 2)
        log.info("Store loaded in %.2fs — %d nodes, %d edges",
                 elapsed, _store.stats()["total_nodes"], _store.stats()["total_edges"])
    except Exception:
        log.exception("Scan failed for %s", resolved)
        raise
    finally:
        _scan_state["scanning"] = False

    return _store.stats()


@app.post("/scan")
def scan_repo(req: ScanRequest):
    log.info("POST /scan — path=%s, exclude=%s", req.path, req.exclude)
    return _do_scan(req.path, req.exclude)


@app.post("/resync")
def resync_repo():
    log.info("POST /resync — repo_path=%s", _scan_state["repo_path"])
    if not _scan_state["repo_path"]:
        raise HTTPException(status_code=400, detail="No repo loaded. Use /scan first.")
    return _do_scan(_scan_state["repo_path"], _scan_state["exclude_patterns"])


@app.post("/clear")
def clear_graph():
    log.info("POST /clear — clearing graph and state")
    _store._graph = None
    _store._code_graph = None
    _store._meta = {}
    _scan_state["repo_path"] = None
    _scan_state["exclude_patterns"] = []
    _scan_state["last_scan_time"] = None
    _scan_state["scan_duration"] = None
    _scan_state["scanning"] = False
    return {"status": "cleared"}


@app.get("/status")
def status():
    stats = _store.stats() if _store.is_loaded() else {}
    return {
        "repo_path": _scan_state["repo_path"],
        "last_scan_time": _scan_state["last_scan_time"],
        "scan_duration": _scan_state["scan_duration"],
        "scanning": _scan_state["scanning"],
        "total_nodes": stats.get("total_nodes", 0),
        "total_edges": stats.get("total_edges", 0),
    }


@app.get("/graph/stats")
def graph_stats():
    log.debug("GET /graph/stats")
    if not _store.is_loaded():
        raise HTTPException(status_code=404, detail="No graph loaded")
    return _store.stats()


@app.get("/graph/nodes")
def graph_nodes(
    search: str = "",
    file: Optional[str] = None,
    type: Optional[str] = None,
    min_in: int = 0,
    min_out: int = 0,
    include_generated: bool = False,
    limit: int = 100,
):
    if not _store.is_loaded():
        return []
    results = _store.search(
        query=search, file=file, node_type=type,
        min_in=min_in, min_out=min_out,
        include_generated=include_generated, limit=limit,
    )
    log.debug("GET /graph/nodes — search=%r, file=%s, type=%s → %d results",
              search, file, type, len(results))
    return results


@app.get("/graph/node/{node_id:path}/neighbors")
def graph_neighbors(node_id: str, direction: str = "both", depth: int = 1):
    log.debug("GET /graph/node/%s/neighbors — direction=%s, depth=%d", node_id, direction, depth)
    if not _store.is_loaded():
        raise HTTPException(status_code=404, detail="No graph loaded")
    sub = _store.get_neighbors(node_id, direction=direction, depth=depth)
    return {"nodes": sub.nodes, "edges": sub.edges, "stats": sub.stats}


@app.get("/graph/node/{node_id:path}/impact")
def graph_impact(node_id: str, depth: int = 5):
    log.debug("GET /graph/node/%s/impact — depth=%d", node_id, depth)
    if not _store.is_loaded():
        raise HTTPException(status_code=404, detail="No graph loaded")
    result = _store.get_impact(node_id, depth=depth)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    return {
        "focus": result.focus,
        "downstream": result.downstream,
        "upstream": result.upstream,
        "downstream_count": result.downstream_count,
        "upstream_count": result.upstream_count,
    }


@app.get("/graph/node/{node_id:path}/flowchart")
def graph_flowchart(node_id: str, depth: int = 5):
    log.debug("GET /graph/node/%s/flowchart — depth=%d", node_id, depth)
    if not _store.is_loaded():
        raise HTTPException(status_code=404, detail="No graph loaded")
    tree = _store.get_call_chain(node_id, depth=depth)
    if tree is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    return tree.to_dict()


@app.get("/graph/node/{node_id:path}")
def graph_node(node_id: str):
    log.debug("GET /graph/node/%s", node_id)
    if not _store.is_loaded():
        raise HTTPException(status_code=404, detail="No graph loaded")
    node = _store.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    return node


@app.get("/graph/file/{file_path:path}")
def graph_file(file_path: str):
    if not _store.is_loaded():
        return []
    return _store.get_file_nodes(file_path)


@app.get("/graph/files")
def graph_files():
    if not _store.is_loaded():
        return []
    return _store.get_files()


@app.get("/graph/full")
def graph_full():
    if not _store.is_loaded():
        log.debug("GET /graph/full — no graph loaded")
        return {"nodes": [], "edges": [], "stats": {}}
    sub = _store.get_full_graph_data()
    log.info("GET /graph/full — returning %d nodes, %d edges", len(sub.nodes), len(sub.edges))
    return {"nodes": sub.nodes, "edges": sub.edges, "stats": sub.stats}


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def ui():
    return _build_ui_html()


def _build_ui_html() -> str:
    return """<!DOCTYPE html>
<html lang="en" data-theme="dark"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CodeGraph</title>
<style>
/* ── Theme variables ── */
:root,[data-theme="dark"]{
  --bg:#0d1117;--bg2:#161b22;--bg3:#1c2128;--text:#c9d1d9;--text2:#8b949e;
  --text-bright:#f0f6fc;--border:#30363d;--blue:#58a6ff;--green:#39d353;
  --red:#f85149;--purple:#a371f7;--orange:#f0883e;--yellow:#d29922;
  --dim:#484f58;--node-stroke:#0d1117;
  --edge-def:#30363d;--edge-dim:rgba(48,54,61,0.12);--edge-hl:#58a6ff;
  --shadow:rgba(0,0,0,0.4);--hover-bg:#21262d;
}
[data-theme="light"]{
  --bg:#ffffff;--bg2:#f6f8fa;--bg3:#eaeef2;--text:#1f2328;--text2:#636c76;
  --text-bright:#1f2328;--border:#d0d7de;--blue:#0969da;--green:#1a7f37;
  --red:#cf222e;--purple:#8250df;--orange:#bc4c00;--yellow:#9a6700;
  --dim:#8c959f;--node-stroke:#ffffff;
  --edge-def:#afb8c1;--edge-dim:rgba(175,184,193,0.2);--edge-hl:#0969da;
  --shadow:rgba(140,149,159,0.15);--hover-bg:#eaeef2;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;overflow:hidden;height:100vh;width:100vw}

/* ── Topbar ── */
#topbar{position:fixed;top:0;left:0;right:0;height:52px;background:var(--bg2);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 16px;gap:12px;z-index:200}
#topbar h1{font-size:15px;color:var(--text-bright);white-space:nowrap}
#topbar h1 span{color:var(--blue)} #topbar h1 .ver{color:var(--green);font-size:10px;margin-left:3px}
#path-input{flex:1;padding:7px 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;outline:none;min-width:200px}
#path-input:focus{border-color:var(--blue)}
.tb-btn{padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:12px;cursor:pointer;white-space:nowrap;transition:all .15s}
.tb-btn:hover{border-color:var(--blue);color:var(--text-bright)}
.tb-btn:disabled{opacity:0.4;cursor:default}
.tb-btn.primary{background:var(--blue);color:#fff;border-color:var(--blue)}
.tb-btn.primary:hover{opacity:0.9}
#status-text{font-size:12px;color:var(--text2);white-space:nowrap;max-width:300px;overflow:hidden;text-overflow:ellipsis}
#status-text.scanning{color:var(--orange)}
#status-text.loaded{color:var(--green)}
#theme-toggle{background:none;border:1px solid var(--border);border-radius:6px;padding:4px 8px;cursor:pointer;font-size:16px;color:var(--text);transition:all .15s}
#theme-toggle:hover{background:var(--hover-bg);border-color:var(--blue)}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--blue);border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:4px}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── Main layout ── */
#main{display:flex;margin-top:52px;height:calc(100vh - 52px)}

/* ── Sidebar ── */
#sidebar{width:320px;min-width:320px;background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.section{padding:10px 16px;border-bottom:1px solid var(--border);font-size:13px}
.stat{display:flex;justify-content:space-between;margin:3px 0} .stat-val{color:var(--blue);font-weight:600}

/* ── Controls ── */
.controls label{font-size:12px;color:var(--text2);display:block;margin-bottom:2px}
.controls input[type=range]{width:100%;accent-color:var(--blue)}
.controls input[type=text]{width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;outline:none;margin-bottom:8px}
.controls input[type=text]:focus{border-color:var(--blue)}
.tval{font-size:11px;color:var(--blue);float:right}
.slider-help{font-size:10px;color:var(--dim);margin-bottom:6px}

/* ── View toggle ── */
.view-toggle{display:flex;gap:4px;margin-bottom:8px}
.vt-btn{flex:1;padding:6px;text-align:center;font-size:12px;cursor:pointer;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text2);transition:all .15s}
.vt-btn:hover{border-color:var(--blue);color:var(--text)}
.vt-btn.active{background:var(--blue);color:#fff;border-color:var(--blue)}
.view-hint{font-size:10px;color:var(--dim);margin-bottom:8px;font-style:italic}

/* ── Directory filter ── */
#dir-section{max-height:200px;display:flex;flex-direction:column}
.dir-header{display:flex;align-items:center;gap:6px;margin-bottom:6px}
.dir-title{font-size:12px;color:var(--text2);font-weight:600;flex:1;text-transform:uppercase;letter-spacing:.5px}
.dir-btn{font-size:10px;padding:2px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text2);cursor:pointer;transition:all .15s}
.dir-btn:hover{border-color:var(--blue);color:var(--text)}
.dir-list{overflow-y:auto;flex:1}
.dir-item{display:flex;align-items:center;gap:6px;padding:2px 0;font-size:12px;cursor:pointer}
.dir-item input{accent-color:var(--blue);cursor:pointer}
.dir-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dir-count{color:var(--dim);font-size:10px;min-width:28px;text-align:right}

/* ── Impact box ── */
.impact-box{padding:10px 16px;border-bottom:1px solid var(--border);font-size:12px;background:var(--bg3);display:none}
.impact-box.active{display:block}
.impact-box .ib-title{color:var(--text-bright);font-weight:600;margin-bottom:4px;font-size:13px}
.impact-box .ib-stat{margin:2px 0}
.ib-downstream{color:var(--orange);font-weight:600} .ib-upstream{color:var(--purple);font-weight:600}
.ib-hint{color:var(--dim);font-size:11px;margin-top:6px;font-style:italic}

/* ── Tabs ── */
.tabs{display:flex;border-bottom:1px solid var(--border)}
.tab{flex:1;padding:8px;text-align:center;font-size:12px;cursor:pointer;color:var(--text2);border-bottom:2px solid transparent;transition:all .15s}
.tab:hover{color:var(--text)} .tab.active{color:var(--blue);border-bottom-color:var(--blue)}
.tab-panel{flex:1;overflow-y:auto;padding:8px 16px;display:none} .tab-panel.active{display:block}
.tab-panel h2{font-size:12px;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px}
.cp-item{padding:5px 8px;border-radius:4px;cursor:pointer;font-size:12px;margin-bottom:2px;display:flex;align-items:center;gap:6px;transition:background .15s}
.cp-item:hover{background:var(--hover-bg)}
.cp-rank{color:var(--red);font-weight:700;min-width:28px}
.ep-marker{color:var(--purple);font-size:10px;min-width:16px}
.dp-marker{color:var(--orange);font-size:10px;min-width:16px}
.cp-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cp-degree{color:var(--text2);font-size:11px}

/* ── Graph ── */
#graph-container{flex:1;position:relative;overflow:hidden}
svg{width:100%;height:100%}
.tooltip{position:absolute;pointer-events:none;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px;font-size:12px;line-height:1.5;max-width:350px;box-shadow:0 8px 24px var(--shadow);display:none;z-index:100}
.tooltip .tt-name{color:var(--text-bright);font-weight:600;font-size:14px}
.tooltip .tt-file{color:var(--blue)} .tooltip .tt-meta{color:var(--text2)}
.tooltip .tt-doc{color:var(--green);font-style:italic;margin-top:4px}
.tt-tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;margin-top:4px}
.tt-tag-entry{background:rgba(130,80,223,0.15);color:var(--purple)}
.tt-tag-orphan{background:rgba(140,149,159,0.15);color:var(--dim)}
.tt-tag-ctrl{background:rgba(207,34,46,0.15);color:var(--red)}
.tt-tag-dispatch{background:rgba(188,76,0,0.15);color:var(--orange)}
.edge-tooltip{position:absolute;pointer-events:none;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:10px 14px;font-size:12px;line-height:1.5;box-shadow:0 8px 24px var(--shadow);display:none;z-index:100;max-width:400px}

/* ── Legend ── */
.legend{position:absolute;bottom:16px;right:16px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 14px;font-size:11px}
.legend-item{display:flex;align-items:center;gap:8px;margin:3px 0;cursor:pointer;padding:2px 4px;border-radius:4px;transition:background .15s}
.legend-item:hover{background:var(--hover-bg)} .legend-item.active{background:var(--bg3);outline:1px solid var(--blue)}
.legend-dot{width:12px;height:12px;border-radius:50%;flex-shrink:0}
.legend-diamond{width:12px;height:12px;flex-shrink:0;transform:rotate(45deg);border-radius:2px}
.legend-line{width:24px;height:3px;border-radius:2px;flex-shrink:0}
.legend-sep{margin-top:6px;padding-top:6px;border-top:1px solid var(--border)}
.depth-label{fill:var(--text2);font-size:12px;font-weight:700;text-anchor:middle;pointer-events:none;opacity:0.7}
.depth-lane{stroke:var(--dim);stroke-width:1;stroke-dasharray:4,4;opacity:0.4}

/* ── No-modules & empty state ── */
#no-modules-msg{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:var(--bg3);border:1px solid var(--border);border-radius:12px;padding:24px 40px;font-size:15px;color:var(--text2);text-align:center;display:none;z-index:50}
#empty-state{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;color:var(--text2);z-index:10}
#empty-state h2{font-size:20px;color:var(--text-bright);margin-bottom:8px}
#empty-state p{font-size:14px;margin-bottom:4px}
</style></head><body>
<div id="app">
<div id="topbar">
  <h1>Code<span>Graph</span><span class="ver">v7</span></h1>
  <input type="text" id="path-input" placeholder="Enter path to Python repo...">
  <button class="tb-btn primary" id="scan-btn">Scan</button>
  <button class="tb-btn" id="resync-btn" disabled>Resync</button>
  <button class="tb-btn" id="clear-btn" disabled>Clear</button>
  <span id="status-text">No repo loaded</span>
  <button id="theme-toggle" title="Toggle light/dark theme">&#9788;</button>
</div>
<div id="main">
<div id="sidebar">
  <div class="section" id="stats"></div>
  <div class="section controls">
    <div class="view-toggle">
      <button class="vt-btn active" data-view="function" title="Each function/method is an individual node">Functions</button>
      <button class="vt-btn" data-view="class" title="Methods collapsed into parent class nodes">Classes</button>
    </div>
    <div class="view-hint" id="view-hint">Each function/method is an individual node</div>
    <input type="text" id="search" placeholder="Search functions... (highlights in graph)" autocomplete="off">
    <div id="search-results" style="display:none;max-height:180px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;margin-bottom:8px;background:var(--bg)"></div>
    <label>Min in-degree: <span class="tval" id="in-val">3</span></label>
    <input type="range" id="in-slider" min="0" max="20" value="3">
    <div class="slider-help">Callers count. Higher = widely used utility.</div>
    <label>Min out-degree: <span class="tval" id="out-val">7</span></label>
    <input type="range" id="out-slider" min="0" max="30" value="7">
    <div class="slider-help">Calls count. Higher = orchestrator.</div>
    <label>Blast radius depth: <span class="tval" id="depth-val">5</span></label>
    <input type="range" id="depth-slider" min="1" max="10" value="5">
    <div class="slider-help">Impact trace depth on hover/click.</div>
  </div>
  <div class="section" id="dir-section">
    <div class="dir-header">
      <span class="dir-title">Directories</span>
      <button class="dir-btn" id="dir-all">All</button>
      <button class="dir-btn" id="dir-none">None</button>
    </div>
    <div class="dir-list" id="dir-list"></div>
  </div>
  <div class="impact-box" id="impact-box">
    <div class="ib-title" id="ib-title">Impact Analysis</div>
    <div class="ib-stat">Downstream: <span class="ib-downstream" id="ib-down">0</span> affected</div>
    <div class="ib-stat">Upstream: <span class="ib-upstream" id="ib-up">0</span> depend on this</div>
    <div class="ib-hint">Click background to deselect all. Numbers = call order.</div>
  </div>
  <div class="tabs">
    <div class="tab active" data-tab="cp">Control Points</div>
    <div class="tab" data-tab="ep">Entry Points</div>
    <div class="tab" data-tab="dp">Dispatch</div>
  </div>
  <div class="tab-panel active" id="panel-cp"><h2>Most called</h2><div id="cp-list"></div></div>
  <div class="tab-panel" id="panel-ep"><h2>Entry points</h2><div id="ep-list"></div></div>
  <div class="tab-panel" id="panel-dp"><h2>Dispatch nodes</h2><div id="dp-list"></div></div>
</div>
<div id="graph-container">
  <svg id="graph"></svg>
  <div class="tooltip" id="tooltip"></div>
  <div class="edge-tooltip" id="edge-tooltip"></div>
  <div id="no-modules-msg">No modules selected.<br>Check directories in the sidebar to show nodes.</div>
  <div id="empty-state">
    <h2>CodeGraph</h2>
    <p>Enter a path above and click <strong>Scan</strong> to visualize.</p>
  </div>
  <div class="legend" id="legend">
    <div class="legend-item" data-filter="top10"><div class="legend-dot" style="background:var(--red)"></div>Control points</div>
    <div class="legend-item" data-filter="top30"><div class="legend-dot" style="background:var(--yellow)"></div>Top 11-30</div>
    <div class="legend-item" data-filter="entry"><div class="legend-diamond" style="background:var(--purple)"></div>Entry points</div>
    <div class="legend-item" data-filter="dispatch"><div class="legend-diamond" style="background:var(--orange)"></div>Dispatch</div>
    <div class="legend-item" data-filter="internal"><div class="legend-dot" style="background:var(--blue)"></div>Internal</div>
    <div class="legend-item" data-filter="orphan"><div class="legend-dot" style="background:var(--dim);border:1px dashed var(--text2)"></div>Orphaned</div>
    <div class="legend-item" data-filter="external"><div class="legend-dot" style="background:var(--dim)"></div>External pkg</div>
    <div class="legend-sep">
      <div class="legend-item" style="cursor:default"><div class="legend-line" style="background:var(--green)"></div>Outgoing</div>
      <div class="legend-item" style="cursor:default"><div class="legend-line" style="background:var(--purple)"></div>Incoming</div>
      <div class="legend-item" style="cursor:default"><div class="legend-line" style="background:var(--orange)"></div>Downstream</div>
      <div class="legend-item" style="cursor:default"><div class="legend-line" style="background:var(--orange);opacity:0.4"></div>Option edge</div>
    </div>
    <div class="legend-sep">
      <div class="legend-item" style="cursor:default;color:var(--green);font-weight:600">Numbers = call order</div>
    </div>
  </div>
</div>
</div>
</div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
/* ──────── data & constants ──────── */
var DATA=null;
var COL={top10:"#f85149",top30:"#d29922",internal:"#58a6ff",external:"#484f58",entry:"#a371f7",orphan:"#6e7681",dispatch:"#f0883e"};
var COL_LIGHT={top10:"#cf222e",top30:"#9a6700",internal:"#0969da",external:"#8c959f",entry:"#8250df",orphan:"#8c959f",dispatch:"#bc4c00"};
var E={def:"#30363d",defW:0.8,out:"#39d353",inc:"#bc8cff",down:"#f0883e",dim:"rgba(48,54,61,0.12)",hiW:2.5,dimW:0.2,opt:"rgba(240,136,62,0.4)"};
var E_LIGHT={def:"#afb8c1",defW:0.8,out:"#1a7f37",inc:"#8250df",down:"#bc4c00",dim:"rgba(175,184,193,0.2)",hiW:2.5,dimW:0.3,opt:"rgba(188,76,0,0.35)"};
function C(){return document.documentElement.getAttribute("data-theme")==="light"?COL_LIGHT:COL;}
function EC(){return document.documentElement.getAttribute("data-theme")==="light"?E_LIGHT:E;}

var ENTRY_DECORATORS=new Set(["get","post","put","delete","patch","head","options",
  "route","api_route","websocket","command","group","app_route","endpoint","on_event",
  "middleware","task","periodic_task","test","fixture","parametrize"]);

/* ──────── state ──────── */
var sim,svg,g,laneG,linkG,nodeG,labelG,orderG;
var cNodes=[],cLinks=[];
var fwd={},bwd={};
var viewMode="function";
var selectedLegends=new Set();
var selectedNodeIds=new Set();
var lockedImpact=null;

/* ──────── theme ──────── */
var theme=localStorage.getItem("codegraph-theme")||"dark";
function applyTheme(){
  document.documentElement.setAttribute("data-theme",theme);
  localStorage.setItem("codegraph-theme",theme);
  document.getElementById("theme-toggle").innerHTML=theme==="dark"?"&#9788;":"&#9790;";
  if(cNodes.length)refreshColors();
}
function refreshColors(){
  var c=C(),e=EC();
  nodeG.selectAll(".node").each(function(d){
    var el=d3.select(this);
    if(d.color_class==="entry"||d.color_class==="dispatch"){el.attr("fill",c[d.color_class]).attr("stroke","var(--node-stroke)");}
    else if(d.color_class==="orphan"){el.attr("stroke",c.orphan);}
    else{el.attr("fill",c[d.color_class]);if(d.color_class==="top10")el.attr("stroke","var(--node-stroke)");}
  });
  if(!hasActiveSelection()){
    linkG.selectAll("line.edge").each(function(d){
      d3.select(this).attr("stroke",d.type==="option"?e.opt:e.def);
    });
  }else{applySelectionVisuals();}
}

/* ──────── adjacency & BFS ──────── */
function buildAdj(){fwd={};bwd={};cLinks.forEach(function(l){var s=l.source&&l.source.id||l.source,t=l.target&&l.target.id||l.target;if(!fwd[s])fwd[s]=[];fwd[s].push(t);if(!bwd[t])bwd[t]=[];bwd[t].push(s);});}
function bfs(start,adj,depth){var v=new Set(),q=[[start,0]];v.add(start);while(q.length){var p=q.shift(),n=p[0],d=p[1];if(d>=depth)continue;(adj[n]||[]).forEach(function(nb){if(!v.has(nb)){v.add(nb);q.push([nb,d+1]);}});};v.delete(start);return v;}
function getDepth(){return+document.getElementById("depth-slider").value;}

/* ──────── class-view aggregation ──────── */
function buildClassData(nodes,links){
  var classMap={},standalone=[];
  nodes.forEach(function(n){
    if(n.class_name){
      var key=n.file+":"+n.class_name;
      if(!classMap[key])classMap[key]={id:key,short_name:n.class_name,file:n.file,class_name:n.class_name,"function":n.class_name,lineno:n.lineno,params:0,lines:0,docstring:"",is_async:false,decorators:[],type:"class",in_degree:0,out_degree:0,color_class:n.color_class,file_group:n.file_group,depth:0,methods:[],memberIds:new Set()};
      var cls=classMap[key];cls.methods.push(n);cls.memberIds.add(n.id);
      cls.in_degree+=n.in_degree;cls.out_degree+=n.out_degree;cls.lines+=n.lines;
      if(n.lineno<cls.lineno)cls.lineno=n.lineno;
      var pri={top10:5,dispatch:4,entry:3,top30:2,internal:1,orphan:0,external:0};
      if((pri[n.color_class]||0)>(pri[cls.color_class]||0))cls.color_class=n.color_class;
    }else standalone.push(n);
  });
  var classNodes=[];
  Object.values(classMap).forEach(function(cls){cls.short_name=cls.class_name+" ("+cls.methods.length+" methods)";classNodes.push(cls);});
  var m2c={};Object.values(classMap).forEach(function(cls){cls.memberIds.forEach(function(mid){m2c[mid]=cls.id;});});
  var edgeSet=new Set(),classLinks=[];
  links.forEach(function(l){var s=l.source&&l.source.id||l.source,t=l.target&&l.target.id||l.target,ms=m2c[s]||s,mt=m2c[t]||t;if(ms===mt)return;var k=ms+"->"+mt;if(!edgeSet.has(k)){edgeSet.add(k);classLinks.push({source:ms,target:mt,order:l.order,type:l.type});}});
  return{nodes:classNodes.concat(standalone),links:classLinks,m2c:m2c};
}

/* ──────── flow depth computation ──────── */
function computeFlowDepths(){
  var depths={},queue=[];
  cNodes.forEach(function(n){if(n.color_class==="entry"){depths[n.id]=0;queue.push(n.id);}});
  if(queue.length===0)cNodes.forEach(function(n){if(n.in_degree===0&&n.type!=="external"&&n.type!=="dispatch"&&n.out_degree>0){depths[n.id]=0;queue.push(n.id);}});
  var head=0;while(head<queue.length){var nid=queue[head++];(fwd[nid]||[]).forEach(function(t){if(!(t in depths)){depths[t]=depths[nid]+1;queue.push(t);}});}
  var maxD=Math.max.apply(null,Object.values(depths).concat([1]));
  cNodes.forEach(function(n){
    if(n.id in depths)n.depth=depths[n.id];
    else if(n.type==="external")n.depth=maxD+1;
    else if(n.type==="dispatch")n.depth=Math.round(maxD*0.6);
    else if(n.color_class==="orphan")n.depth=maxD+2;
    else if(n.out_degree===0)n.depth=maxD;
    else if(n.in_degree===0)n.depth=0;
    else n.depth=Math.round((n.in_degree/(n.in_degree+n.out_degree))*maxD);
  });
  return maxD;
}

/* ──────── directory filter helpers ──────── */
function getCheckedDirs(){
  var s=new Set();
  document.querySelectorAll("#dir-list input[type=checkbox]:checked").forEach(function(cb){s.add(cb.dataset.dir);});
  return s;
}
function allDirCount(){return document.querySelectorAll("#dir-list input[type=checkbox]").length;}

/* ──────── selection model ──────── */
function hasActiveSelection(){return selectedLegends.size>0||selectedNodeIds.size>0||lockedImpact!==null||searchHighlightIds.size>0;}

function computeHighlightSet(){
  var h=new Set();
  if(selectedLegends.size>0)cNodes.forEach(function(n){if(selectedLegends.has(n.color_class))h.add(n.id);});
  selectedNodeIds.forEach(function(id){h.add(id);});
  if(lockedImpact){
    h.add(lockedImpact.focusId);
    lockedImpact.downIds.forEach(function(id){h.add(id);});
    lockedImpact.upIds.forEach(function(id){h.add(id);});
    if(lockedImpact.directOut)lockedImpact.directOut.forEach(function(id){h.add(id);});
    if(lockedImpact.directIn)lockedImpact.directIn.forEach(function(id){h.add(id);});
  }
  return h;
}

function applySelectionVisuals(){
  if(!hasActiveSelection()){resetVisuals();updateStats();return;}
  var h=computeHighlightSet(),e=EC();
  nodeG.selectAll(".node").attr("opacity",function(d){return h.has(d.id)?1:0.06;});
  labelG.selectAll("text").attr("opacity",function(d){return h.has(d.id)?1:0.06;});
  if(lockedImpact){
    var focusId=lockedImpact.focusId,downIds=lockedImpact.downIds,upIds=lockedImpact.upIds,directOut=lockedImpact.directOut,directIn=lockedImpact.directIn;
    linkG.selectAll("line.edge").each(function(l){
      var s=l.source&&l.source.id||l.source,t=l.target&&l.target.id||l.target,el=d3.select(this);
      if(s===focusId&&(directOut&&directOut.has(t)))el.attr("stroke",e.out).attr("stroke-width",e.hiW).attr("marker-end","url(#arr-out)").attr("opacity",1);
      else if(t===focusId&&(directIn&&directIn.has(s)))el.attr("stroke",e.inc).attr("stroke-width",e.hiW).attr("marker-end","url(#arr-in)").attr("opacity",1);
      else if(s===focusId)el.attr("stroke",e.out).attr("stroke-width",e.hiW).attr("marker-end","url(#arr-out)").attr("opacity",1);
      else if(t===focusId)el.attr("stroke",e.inc).attr("stroke-width",e.hiW).attr("marker-end","url(#arr-in)").attr("opacity",1);
      else if(downIds.has(s)&&downIds.has(t))el.attr("stroke",e.down).attr("stroke-width",1.3).attr("marker-end","url(#arr-dn)").attr("opacity",0.6);
      else if(upIds.has(s)&&upIds.has(t))el.attr("stroke",e.inc).attr("stroke-width",1.3).attr("marker-end","url(#arr-in)").attr("opacity",0.5);
      else if(h.has(s)&&h.has(t))el.attr("stroke",e.def).attr("stroke-width",1).attr("marker-end","url(#arr-def)").attr("opacity",0.7);
      else el.attr("stroke",e.dim).attr("stroke-width",e.dimW).attr("marker-end","").attr("opacity",1);
    });
  }else{
    linkG.selectAll("line.edge").each(function(l){
      var s=l.source&&l.source.id||l.source,t=l.target&&l.target.id||l.target,el=d3.select(this),e2=EC();
      if(h.has(s)&&h.has(t))el.attr("stroke",e2.def).attr("stroke-width",1.5).attr("marker-end","url(#arr-def)").attr("opacity",0.8);
      else if(h.has(s)||h.has(t))el.attr("stroke",e2.def).attr("stroke-width",0.8).attr("marker-end","url(#arr-def)").attr("opacity",0.3);
      else el.attr("stroke",e2.dim).attr("stroke-width",e2.dimW).attr("marker-end","").attr("opacity",1);
    });
  }
  updateStats();
}

function clearAllSelections(){
  selectedLegends.clear();selectedNodeIds.clear();lockedImpact=null;
  searchHighlightIds.clear();
  document.querySelectorAll(".legend-item[data-filter]").forEach(function(x){x.classList.remove("active");});
  document.getElementById("impact-box").classList.remove("active");
  document.getElementById("edge-tooltip").style.display="none";
  document.getElementById("search-results").style.display="none";
  orderG.selectAll("*").remove();
  resetVisuals();updateStats();
}

/* ──────── search (non-destructive: highlight + zoom) ──────── */
var searchHighlightIds=new Set();

function doSearch(){
  var q=document.getElementById("search").value.toLowerCase().trim();
  searchHighlightIds.clear();

  if(!q||!cNodes.length){
    resetVisuals();updateStats();
    document.getElementById("search-results").style.display="none";
    return;
  }

  var matches=cNodes.filter(function(n){
    return n.short_name.toLowerCase().includes(q)||n.file.toLowerCase().includes(q);
  });

  if(matches.length===0){
    document.getElementById("search-results").innerHTML='<div style="padding:6px;color:var(--dim);font-size:12px">No matches</div>';
    document.getElementById("search-results").style.display="block";
    resetVisuals();updateStats();
    return;
  }

  matches.forEach(function(n){searchHighlightIds.add(n.id);});

  var dep=getDepth();
  matches.forEach(function(n){
    (fwd[n.id]||[]).forEach(function(t){searchHighlightIds.add(t);});
    (bwd[n.id]||[]).forEach(function(t){searchHighlightIds.add(t);});
  });

  var e=EC();
  nodeG.selectAll(".node").attr("opacity",function(d){return searchHighlightIds.has(d.id)?1:0.06;});
  labelG.selectAll("text").attr("opacity",function(d){return searchHighlightIds.has(d.id)?1:0.06;});
  linkG.selectAll("line.edge").each(function(l){
    var s=l.source&&l.source.id||l.source,t=l.target&&l.target.id||l.target,el=d3.select(this);
    if(searchHighlightIds.has(s)&&searchHighlightIds.has(t))el.attr("stroke",e.def).attr("stroke-width",1.5).attr("opacity",0.8);
    else el.attr("stroke",e.dim).attr("stroke-width",e.dimW).attr("opacity",0.15);
  });

  var html=matches.slice(0,15).map(function(n){
    return '<div class="cp-item search-result" data-id="'+n.id+'" style="border-left:3px solid var(--blue);margin-left:0"><span class="cp-name">'+n.short_name+'</span><span class="cp-degree" style="font-size:10px">'+n.file+'</span></div>';
  }).join("");
  if(matches.length>15)html+='<div style="padding:4px 8px;color:var(--dim);font-size:11px">+'+(matches.length-15)+' more</div>';
  document.getElementById("search-results").innerHTML=html;
  document.getElementById("search-results").style.display="block";

  document.querySelectorAll(".search-result").forEach(function(el){
    el.addEventListener("click",function(){
      var nd=cNodes.find(function(n){return n.id===el.dataset.id;});
      if(nd){
        onNodeClick(nd);
        zoomToNode(nd);
      }
    });
  });

  if(matches.length<=3)zoomToNode(matches[0]);
  updateStats();
}

function zoomToNode(nd){
  if(!nd||nd.x==null||nd.y==null)return;
  var container=document.getElementById("graph-container"),W=container.clientWidth,H=container.clientHeight;
  var scale=1.5;
  var transform=d3.zoomIdentity.translate(W/2-nd.x*scale,H/2-nd.y*scale).scale(scale);
  svg.transition().duration(500).call(d3.zoom().scaleExtent([0.05,10]).on("zoom",function(e){g.attr("transform",e.transform);}).transform,transform);
}

/* ──────── filter pipeline (degree + directory only, no search) ──────── */
function applyFilter(){
  if(!DATA)return;
  var mI=+document.getElementById("in-slider").value,mO=+document.getElementById("out-slider").value;
  var checkedDirs=getCheckedDirs(),nDirs=allDirCount(),dirActive=checkedDirs.size<nDirs;

  var keep=new Set();
  DATA.nodes.forEach(function(n){
    if(n.type==="external"){if(n.in_degree>=3)keep.add(n.id);}
    else if(n.type==="dispatch"){if(n.in_degree>=1||n.out_degree>=1)keep.add(n.id);}
    else if(n.in_degree>=mI||n.out_degree>=mO||n.color_class==="entry")keep.add(n.id);
  });
  var fN=DATA.nodes.filter(function(n){return keep.has(n.id);}),fL=DATA.links.filter(function(l){return keep.has(l.source&&l.source.id||l.source)&&keep.has(l.target&&l.target.id||l.target);});

  if(dirActive){
    if(checkedDirs.size===0){fN=[];fL=[];}
    else{
      fN=fN.filter(function(n){
        if(n.type==="external"||n.type==="dispatch")return true;
        var p=n.file.split("/");return checkedDirs.has(p.length>1?p[0]+"/":"(root)");
      });
      var ids=new Set(fN.map(function(n){return n.id;}));
      fL=fL.filter(function(l){return ids.has(l.source&&l.source.id||l.source)&&ids.has(l.target&&l.target.id||l.target);});
    }
  }

  if(viewMode==="class"){
    var cd=buildClassData(fN,fL);cNodes=cd.nodes;cLinks=cd.links;
  }else{cNodes=fN;cLinks=fL;}

  selectedLegends.clear();selectedNodeIds.clear();lockedImpact=null;
  document.querySelectorAll(".legend-item[data-filter]").forEach(function(x){x.classList.remove("active");});
  document.getElementById("impact-box").classList.remove("active");
  document.getElementById("edge-tooltip").style.display="none";

  var msg=document.getElementById("no-modules-msg");
  msg.style.display=cNodes.length===0?"block":"none";

  buildAdj();updateStats();render();
}

/* ──────── stats ──────── */
function updateStats(){
  if(!DATA)return;
  var s=DATA.stats,entries=cNodes.filter(function(n){return n.color_class==="entry";}).length;
  var dispatches=cNodes.filter(function(n){return n.color_class==="dispatch";}).length,maxD=d3.max(cNodes,function(n){return n.depth;})||0;
  var vis=cNodes.length;
  if(hasActiveSelection())vis=computeHighlightSet().size;
  document.getElementById("stats").innerHTML=
    '<div class="stat"><span>Full codebase</span><span class="stat-val">'+(s.total_functions||DATA.nodes.length)+'</span></div>'+
    '<div class="stat"><span>Visible nodes</span><span class="stat-val" id="vis-count">'+vis+'</span></div>'+
    '<div class="stat"><span>Visible edges</span><span class="stat-val">'+cLinks.length+'</span></div>'+
    '<div class="stat"><span>Entry points</span><span class="stat-val" style="color:var(--purple)">'+entries+'</span></div>'+
    '<div class="stat"><span>Dispatch nodes</span><span class="stat-val" style="color:var(--orange)">'+dispatches+'</span></div>'+
    '<div class="stat"><span>Flow depth</span><span class="stat-val">'+maxD+' layers</span></div>'+
    '<div class="stat"><span>View</span><span class="stat-val">'+(viewMode==="class"?"Class":"Function")+'</span></div>';
}

/* ──────── render ──────── */
function render(){
  var container=document.getElementById("graph-container"),W=container.clientWidth,H=container.clientHeight;
  var c=C(),e=EC();
  var maxC=d3.max(cNodes,function(d){return d.in_degree+d.out_degree;})||1;
  var rS=d3.scaleSqrt().domain([0,maxC]).range([3,18]);
  if(sim)sim.stop();orderG.selectAll("*").remove();laneG.selectAll("*").remove();

  var maxD=computeFlowDepths(),totalD=maxD+3,pad=80;
  var graphW=Math.max(W,totalD*180);
  var depthLabels=["Entry","","","Mid","","","","Deep","","Utility","External","Orphan"];
  for(var i=0;i<=Math.min(totalD,11);i++){
    var x=pad+(i/totalD)*(graphW-2*pad);
    laneG.append("line").attr("class","depth-lane").attr("x1",x).attr("y1",30).attr("x2",x).attr("y2",H-10);
    if(depthLabels[i])laneG.append("text").attr("class","depth-label").attr("x",x).attr("y",18).text(depthLabels[i]);
  }

  sim=d3.forceSimulation(cNodes)
    .force("link",d3.forceLink(cLinks).id(function(d){return d.id;}).distance(40).strength(0.05))
    .force("charge",d3.forceManyBody().strength(-35).distanceMax(200))
    .force("x",d3.forceX(function(d){return pad+(d.depth/totalD)*(graphW-2*pad);}).strength(0.85))
    .force("y",d3.forceY(function(d){
      var ln=cNodes.filter(function(n){return n.depth===d.depth;}),idx=ln.indexOf(d),cnt=ln.length;
      return cnt<=1?H/2:pad+((idx/(cnt-1))*(H-2*pad));
    }).strength(0.15))
    .force("collision",d3.forceCollide().radius(function(d){return rS(d.in_degree+d.out_degree)+4;}).strength(0.8))
    .alphaDecay(0.025).velocityDecay(0.5);

  var lk=linkG.selectAll("line.edge").data(cLinks,function(d){return(d.source&&d.source.id||d.source)+"-"+(d.target&&d.target.id||d.target);});
  lk.exit().remove();
  var lkE=lk.enter().append("line").attr("class","edge")
    .attr("stroke",function(d){return d.type==="option"?e.opt:e.def;})
    .attr("stroke-width",function(d){return d.type==="option"?0.6:e.defW;})
    .attr("stroke-dasharray",function(d){return d.type==="option"?"4,3":d.type==="dynamic"?"2,2":null;})
    .attr("marker-end","url(#arr-def)");
  var lkM=lkE.merge(lk);

  var ht=linkG.selectAll("line.ehit").data(cLinks,function(d){return(d.source&&d.source.id||d.source)+"-"+(d.target&&d.target.id||d.target);});
  ht.exit().remove();
  var htE=ht.enter().append("line").attr("class","ehit").attr("stroke","transparent").attr("stroke-width",12).style("cursor","pointer")
    .on("mouseover",edgeHover).on("mousemove",edgeMove).on("mouseout",edgeOut).on("click",onEdgeClick);
  var htM=htE.merge(ht);

  var nd=nodeG.selectAll(".node").data(cNodes,function(d){return d.id;});nd.exit().remove();
  var ndE=nd.enter().append(function(d){return document.createElementNS("http://www.w3.org/2000/svg",
    d.color_class==="entry"||d.color_class==="dispatch"?"rect":"circle");})
    .attr("class","node").style("cursor","pointer")
    .on("mouseover",showTip).on("mouseout",hideTip)
    .on("click",function(ev,d){ev.stopPropagation();onNodeClick(d);})
    .call(d3.drag().on("start",ds).on("drag",dg).on("end",de));
  var ndM=ndE.merge(nd);
  ndM.each(function(d){var el=d3.select(this),r=rS(d.in_degree+d.out_degree);
    if(d.color_class==="entry")el.attr("width",r*2).attr("height",r*2).attr("rx",3).attr("ry",3).attr("fill",c.entry).attr("opacity",0.9).attr("stroke","var(--node-stroke)").attr("stroke-width",1.5);
    else if(d.color_class==="dispatch")el.attr("width",r*2).attr("height",r*2).attr("rx",3).attr("ry",3).attr("fill",c.dispatch).attr("opacity",0.85).attr("stroke","var(--node-stroke)").attr("stroke-width",1.5);
    else if(d.color_class==="orphan")el.attr("r",Math.max(r,3)).attr("fill","none").attr("stroke",c.orphan).attr("stroke-width",1.5).attr("stroke-dasharray","3,2").attr("opacity",0.5);
    else el.attr("r",r).attr("fill",c[d.color_class]).attr("stroke",d.color_class==="top10"?"var(--node-stroke)":"none").attr("stroke-width",d.color_class==="top10"?2:0).attr("opacity",d.type==="external"?0.45:0.85);
  });

  var topN=cNodes.filter(function(d){return["top10","top30","entry","dispatch"].indexOf(d.color_class)>=0;});
  var lb=labelG.selectAll("text.nlabel").data(topN,function(d){return d.id;});lb.exit().remove();
  var lbE=lb.enter().append("text").attr("class","nlabel")
    .text(function(d){return d.short_name.length>30?d.short_name.slice(0,27)+"...":d.short_name;})
    .attr("font-size",function(d){return d.color_class==="top10"?9:7;})
    .attr("fill",function(d){return d.color_class==="entry"?"var(--purple)":d.color_class==="dispatch"?"var(--orange)":d.color_class==="top10"?"var(--text-bright)":"var(--text2)";})
    .attr("text-anchor","middle").attr("dy",function(d){return -rS(d.in_degree+d.out_degree)-4;}).attr("pointer-events","none");
  var lbM=lbE.merge(lb);

  sim.on("tick",function(){
    lkM.attr("x1",function(d){return d.source.x;}).attr("y1",function(d){return d.source.y;}).attr("x2",function(d){return d.target.x;}).attr("y2",function(d){return d.target.y;});
    htM.attr("x1",function(d){return d.source.x;}).attr("y1",function(d){return d.source.y;}).attr("x2",function(d){return d.target.x;}).attr("y2",function(d){return d.target.y;});
    ndM.each(function(d){var el=d3.select(this);if(d.color_class==="entry"||d.color_class==="dispatch"){var r=rS(d.in_degree+d.out_degree);el.attr("x",d.x-r).attr("y",d.y-r);}else el.attr("cx",d.x).attr("cy",d.y);});
    lbM.attr("x",function(d){return d.x;}).attr("y",function(d){return d.y;});
    orderG.selectAll(".olabel").each(function(){var el=d3.select(this),lnk=el.datum();
      if(lnk&&lnk.source&&lnk.target)el.attr("x",(lnk.source.x+lnk.target.x)/2).attr("y",(lnk.source.y+lnk.target.y)/2-6);
    });
  });
}

/* ──────── call-order numbers ──────── */
function showOrderNumbers(nodeId){
  orderG.selectAll("*").remove();
  cLinks.filter(function(l){return(l.source&&l.source.id||l.source)===nodeId;}).forEach(function(l){
    if(l.order){
      orderG.append("circle").attr("class","olabel").attr("r",7).attr("fill","var(--bg)").attr("stroke","var(--green)").attr("stroke-width",1.5).datum(l);
      orderG.append("text").attr("class","olabel").text(l.order).attr("fill","var(--green)").attr("font-size",8).attr("font-weight",700).attr("text-anchor","middle").attr("dominant-baseline","central").datum(l);
    }
  });
}

/* ──────── tooltip ──────── */
function showTip(event,d){
  var tt=document.getElementById("tooltip"),async_=d.is_async?" async":"";
  var decos=d.decorators&&d.decorators.length?'<div class="tt-meta">@'+d.decorators.join(", @")+'</div>':"";
  var tag="";
  if(d.color_class==="entry")tag='<span class="tt-tag tt-tag-entry">ENTRY POINT</span>';
  else if(d.color_class==="orphan")tag='<span class="tt-tag tt-tag-orphan">ORPHANED</span>';
  else if(d.color_class==="top10")tag='<span class="tt-tag tt-tag-ctrl">CONTROL POINT</span>';
  else if(d.color_class==="dispatch")tag='<span class="tt-tag tt-tag-dispatch">DISPATCH</span>';
  var methods=d.methods?'<div class="tt-meta">Methods: '+d.methods.length+'</div>':"";
  tt.innerHTML='<div class="tt-name">'+d.short_name+async_+'</div><div class="tt-file">'+d.file+":"+d.lineno+'</div>'+
    '<div class="tt-meta">in: '+d.in_degree+' | out: '+d.out_degree+' | '+d.lines+' lines | depth: '+d.depth+'</div>'+
    decos+methods+tag+(d.docstring?'<div class="tt-doc">'+d.docstring+'</div>':"");
  tt.style.display="block";tt.style.left=(event.pageX-320+15)+"px";tt.style.top=(event.pageY+15)+"px";
}
function hideTip(){document.getElementById("tooltip").style.display="none";}

/* ──────── edge tooltip & hover ──────── */
function edgeHover(event,d){
  if(hasActiveSelection()){showEdgeTooltipOnly(event,d);return;}
  var s=d.source&&d.source.id||d.source,t=d.target&&d.target.id||d.target,dep=getDepth(),e=EC();
  var down=bfs(t,fwd,dep),up=bfs(s,bwd,dep),all=new Set([s,t]);down.forEach(function(x){all.add(x);});up.forEach(function(x){all.add(x);});
  nodeG.selectAll(".node").attr("opacity",function(nd){return all.has(nd.id)?1:0.05;});
  labelG.selectAll("text").attr("opacity",function(nd){return all.has(nd.id)?1:0.05;});
  linkG.selectAll("line.edge").each(function(l){var ls=l.source&&l.source.id||l.source,lt=l.target&&l.target.id||l.target;var el=d3.select(this);
    if(ls===s&&lt===t)el.attr("stroke","var(--text-bright)").attr("stroke-width",3).attr("opacity",1);
    else if(down.has(ls)&&down.has(lt)||ls===t&&down.has(lt))el.attr("stroke",e.down).attr("stroke-width",1.5).attr("opacity",0.7);
    else if(up.has(ls)&&up.has(lt)||lt===s&&up.has(ls))el.attr("stroke",e.inc).attr("stroke-width",1.5).attr("opacity",0.6);
    else el.attr("stroke",e.dim).attr("stroke-width",e.dimW).attr("opacity",1);
  });
  showEdgeTooltipOnly(event,d);
}
function showEdgeTooltipOnly(event,d){
  var s=d.source&&d.source.id||d.source,t=d.target&&d.target.id||d.target,dep=getDepth();
  var down=bfs(t,fwd,dep),up=bfs(s,bwd,dep);
  var sn=cNodes.find(function(n){return n.id===s;}),tn=cNodes.find(function(n){return n.id===t;});
  var ett=document.getElementById("edge-tooltip");
  ett.innerHTML='<div style="color:var(--text-bright);font-weight:600">'+(sn?sn.short_name:s)+' &#8594; '+(tn?tn.short_name:t)+'</div>'+
    (d.order?'<div style="color:var(--green)">Call order: #'+d.order+'</div>':'')+
    (d.type&&d.type!=="resolved"?'<div style="color:var(--orange)">Type: '+d.type+'</div>':'')+
    '<div style="color:var(--orange);margin-top:3px">Downstream: '+down.size+'</div><div style="color:var(--purple)">Upstream: '+up.size+'</div>'+
    '<div style="color:var(--dim);font-size:11px;margin-top:4px">Click to add to selection</div>';
  ett.style.display="block";
}
function edgeMove(event){var e=document.getElementById("edge-tooltip");e.style.left=(event.pageX-320+15)+"px";e.style.top=(event.pageY+15)+"px";}
function edgeOut(){document.getElementById("edge-tooltip").style.display="none";if(!hasActiveSelection())resetVisuals();}

/* ──────── click handlers ──────── */
function onNodeClick(d){
  if(selectedNodeIds.has(d.id)){
    selectedNodeIds.delete(d.id);
    if(lockedImpact&&lockedImpact.focusId===d.id)lockedImpact=null;
  }else{
    selectedNodeIds.add(d.id);
    var dep=getDepth(),down=bfs(d.id,fwd,dep),up=bfs(d.id,bwd,dep);
    lockedImpact={focusId:d.id,downIds:down,upIds:up,directOut:new Set(fwd[d.id]||[]),directIn:new Set(bwd[d.id]||[])};
    showOrderNumbers(d.id);showImpactBox(d.short_name,down.size,up.size);
  }
  applySelectionVisuals();
}
function onEdgeClick(event,d){
  event.stopPropagation();
  var s=d.source&&d.source.id||d.source,t=d.target&&d.target.id||d.target,dep=getDepth();
  var down=bfs(t,fwd,dep),up=bfs(s,bwd,dep);
  selectedNodeIds.add(s);selectedNodeIds.add(t);
  var allDown=new Set([t]);down.forEach(function(x){allDown.add(x);});
  lockedImpact={focusId:s,downIds:allDown,upIds:up,directOut:new Set(fwd[s]||[]),directIn:new Set(bwd[s]||[])};
  showOrderNumbers(s);
  var sn=cNodes.find(function(n){return n.id===s;}),tn=cNodes.find(function(n){return n.id===t;});
  showImpactBox((sn?sn.short_name:s)+" &#8594; "+(tn?tn.short_name:t),down.size+1,up.size);
  document.getElementById("edge-tooltip").style.display="none";
  applySelectionVisuals();
}
function onLegendClick(cls){
  if(selectedLegends.has(cls))selectedLegends.delete(cls);else selectedLegends.add(cls);
  document.querySelectorAll(".legend-item[data-filter]").forEach(function(el){el.classList.toggle("active",selectedLegends.has(el.dataset.filter));});
  applySelectionVisuals();
}

/* ──────── impact box ──────── */
function showImpactBox(title,down,up){var box=document.getElementById("impact-box");box.classList.add("active");
  document.getElementById("ib-title").innerHTML=title;document.getElementById("ib-down").textContent=down;document.getElementById("ib-up").textContent=up;}

/* ──────── reset helpers ──────── */
function resetVisuals(){
  orderG.selectAll("*").remove();var c=C(),e=EC();
  nodeG.selectAll(".node").attr("opacity",function(d){return d.color_class==="orphan"?0.5:d.type==="external"?0.45:0.85;});
  labelG.selectAll("text").attr("opacity",1);
  linkG.selectAll("line.edge").each(function(d){
    d3.select(this).attr("stroke",d.type==="option"?e.opt:e.def)
      .attr("stroke-width",d.type==="option"?0.6:e.defW)
      .attr("stroke-dasharray",d.type==="option"?"4,3":d.type==="dynamic"?"2,2":null)
      .attr("marker-end","url(#arr-def)").attr("opacity",1);
  });
}
function debounce(fn,ms){var t;return function(){clearTimeout(t);t=setTimeout(fn,ms);};}
function ds(e,d){if(!e.active)sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y;}
function dg(e,d){d.fx=e.x;d.fy=e.y;}
function de(e,d){if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}

/* ──────── API: classify nodes client-side ──────── */
function classifyNodes(){
  var internals=DATA.nodes.filter(function(n){return n.type!=="external"&&n.type!=="dispatch";});
  var sorted=internals.slice().sort(function(a,b){return b.in_degree-a.in_degree;});
  var top10Ids=new Set(sorted.slice(0,10).map(function(n){return n.id;}));
  var top30Ids=new Set(sorted.slice(10,30).map(function(n){return n.id;}));
  DATA.nodes.forEach(function(n){
    var isEntry=(n.decorators||[]).some(function(d){return ENTRY_DECORATORS.has(d.toLowerCase());});
    if(!isEntry&&n.in_degree===0&&n.out_degree>=3&&n.type!=="external"&&n.type!=="dispatch")isEntry=true;
    var isOrphan=n.in_degree===0&&n.out_degree===0&&n.type!=="external"&&n.type!=="dispatch";
    if(n.type==="dispatch")n.color_class="dispatch";
    else if(isEntry)n.color_class="entry";
    else if(isOrphan)n.color_class="orphan";
    else if(top10Ids.has(n.id))n.color_class="top10";
    else if(top30Ids.has(n.id))n.color_class="top30";
    else if(n.type==="external")n.color_class="external";
    else n.color_class="internal";
  });
}

/* ──────── API: build directory filter ──────── */
function buildDirFilter(){
  var counts={};
  DATA.nodes.forEach(function(n){
    if(n.type==="external"||n.type==="dispatch")return;
    var parts=n.file.split("/");
    var key=parts.length>1?parts[0]+"/":"(root)";
    counts[key]=(counts[key]||0)+1;
  });
  var sorted=Object.entries(counts).sort(function(a,b){return b[1]-a[1];});
  var el=document.getElementById("dir-list");
  el.innerHTML=sorted.map(function(pair){
    var dir=pair[0],count=pair[1];
    return '<label class="dir-item"><input type="checkbox" checked data-dir="'+dir+'"><span class="dir-name">'+dir+'</span><span class="dir-count">'+count+'</span></label>';
  }).join("");
  el.querySelectorAll("input[type=checkbox]").forEach(function(cb){cb.addEventListener("change",applyFilter);});
}

/* ──────── API: build sidebar lists ──────── */
function buildSidebarLists(){
  var internals=DATA.nodes.filter(function(n){return n.type!=="external";});
  var topCtrl=internals.slice().sort(function(a,b){return b.in_degree-a.in_degree;}).slice(0,20);
  document.getElementById("cp-list").innerHTML=topCtrl.map(function(n,i){
    return '<div class="cp-item" data-id="'+n.id+'"><span class="cp-rank">#'+(i+1)+'</span> <span class="cp-name">'+n.short_name+'</span> <span class="cp-degree">in:'+n.in_degree+'</span></div>';
  }).join("");
  var entries=DATA.nodes.filter(function(n){return n.color_class==="entry";}).sort(function(a,b){return b.out_degree-a.out_degree;}).slice(0,20);
  document.getElementById("ep-list").innerHTML=entries.map(function(n){
    return '<div class="cp-item" data-id="'+n.id+'"><span class="ep-marker">&#9654;</span> <span class="cp-name">'+n.short_name+'</span> <span class="cp-degree">out:'+n.out_degree+'</span></div>';
  }).join("");
  var dispatches=DATA.nodes.filter(function(n){return n.color_class==="dispatch";}).sort(function(a,b){return b.out_degree-a.out_degree;}).slice(0,20);
  document.getElementById("dp-list").innerHTML=dispatches.map(function(n){
    return '<div class="cp-item" data-id="'+n.id+'"><span class="dp-marker">&#9670;</span> <span class="cp-name">'+n.short_name+'</span> <span class="cp-degree">out:'+n.out_degree+'</span></div>';
  }).join("");
  document.querySelectorAll(".cp-item").forEach(function(el){el.addEventListener("click",function(){
    var nd=cNodes.find(function(n){return n.id===el.dataset.id;});if(nd)onNodeClick(nd);
  });});
}

/* ──────── API: status, load, scan, resync ──────── */
function setStatus(cls,text){
  var el=document.getElementById("status-text");el.className=cls;el.textContent=text;
}

function loadGraph(){
  setStatus("scanning","Loading graph...");
  return fetch("/graph/full").then(function(r){return r.json();}).then(function(d){
    DATA={nodes:d.nodes,links:d.edges,stats:d.stats};
    classifyNodes();
    buildDirFilter();
    buildSidebarLists();
    document.getElementById("empty-state").style.display="none";
    setStatus("loaded","Loaded: "+DATA.nodes.length+" nodes, "+DATA.links.length+" edges");
    applyFilter();
  }).catch(function(e){
    setStatus("","Error loading graph: "+e.message);
  });
}

function doScan(){
  var path=document.getElementById("path-input").value.trim();
  if(!path){alert("Enter a path first");return;}
  setStatus("scanning","Scanning...");
  document.getElementById("scan-btn").disabled=true;
  fetch("/scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:path})})
    .then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail||"Scan failed");});return r.json();})
    .then(function(){
      document.getElementById("resync-btn").disabled=false;
      document.getElementById("clear-btn").disabled=false;
      return loadGraph();
    })
    .catch(function(e){setStatus("","Error: "+e.message);})
    .finally(function(){document.getElementById("scan-btn").disabled=false;});
}

function doResync(){
  setStatus("scanning","Rescanning...");
  document.getElementById("resync-btn").disabled=true;
  fetch("/resync",{method:"POST"})
    .then(function(r){if(!r.ok)return r.json().then(function(e){throw new Error(e.detail||"Resync failed");});return r.json();})
    .then(function(){return loadGraph();})
    .catch(function(e){setStatus("","Error: "+e.message);})
    .finally(function(){document.getElementById("resync-btn").disabled=false;});
}

function doClear(){
  fetch("/clear",{method:"POST"}).then(function(){
    DATA=null;cNodes=[];cLinks=[];
    if(sim)sim.stop();
    nodeG.selectAll("*").remove();linkG.selectAll("*").remove();
    labelG.selectAll("*").remove();orderG.selectAll("*").remove();laneG.selectAll("*").remove();
    clearAllSelections();
    document.getElementById("stats").innerHTML="";
    document.getElementById("cp-list").innerHTML="";
    document.getElementById("ep-list").innerHTML="";
    document.getElementById("dp-list").innerHTML="";
    document.getElementById("dir-list").innerHTML="";
    document.getElementById("search").value="";
    document.getElementById("search-results").style.display="none";
    document.getElementById("path-input").value="";
    document.getElementById("resync-btn").disabled=true;
    document.getElementById("clear-btn").disabled=true;
    document.getElementById("empty-state").style.display="block";
    setStatus("","No repo loaded");
  });
}

/* ──────── init ──────── */
function init(){
  applyTheme();
  svg=d3.select("#graph");g=svg.append("g");
  laneG=g.append("g");linkG=g.append("g");nodeG=g.append("g");labelG=g.append("g");orderG=g.append("g");
  var defs=svg.append("defs");
  [["arr-def",EC().def],["arr-out",EC().out],["arr-in",EC().inc],["arr-dn",EC().down]].forEach(function(pair){
    defs.append("marker").attr("id",pair[0]).attr("viewBox","0 -3 6 6").attr("refX",14).attr("refY",0)
      .attr("markerWidth",5).attr("markerHeight",5).attr("orient","auto")
      .append("path").attr("d","M0,-3L6,0L0,3").attr("fill",pair[1]);
  });
  svg.call(d3.zoom().scaleExtent([0.05,10]).on("zoom",function(e){g.attr("transform",e.transform);}));
  svg.on("click",function(e){if(e.target===this||e.target.tagName==="svg")clearAllSelections();});

  document.getElementById("theme-toggle").addEventListener("click",function(){theme=theme==="dark"?"light":"dark";applyTheme();});
  document.getElementById("scan-btn").addEventListener("click",doScan);
  document.getElementById("resync-btn").addEventListener("click",doResync);
  document.getElementById("clear-btn").addEventListener("click",doClear);
  document.getElementById("path-input").addEventListener("keydown",function(e){if(e.key==="Enter")doScan();});

  document.getElementById("search").addEventListener("input",debounce(doSearch,300));
  ["in-slider","out-slider"].forEach(function(id){document.getElementById(id).addEventListener("input",function(){
    document.getElementById("in-val").textContent=document.getElementById("in-slider").value;
    document.getElementById("out-val").textContent=document.getElementById("out-slider").value;
    applyFilter();
  });});
  document.getElementById("depth-slider").addEventListener("input",function(){
    document.getElementById("depth-val").textContent=document.getElementById("depth-slider").value;
    if(lockedImpact){
      var d=cNodes.find(function(n){return n.id===lockedImpact.focusId;});
      if(d){var dep=getDepth(),down=bfs(d.id,fwd,dep),up=bfs(d.id,bwd,dep);
        lockedImpact={focusId:d.id,downIds:down,upIds:up,directOut:new Set(fwd[d.id]||[]),directIn:new Set(bwd[d.id]||[])};
        showImpactBox(d.short_name,down.size,up.size);applySelectionVisuals();}
    }
  });

  document.querySelectorAll(".tab").forEach(function(t){t.addEventListener("click",function(){
    document.querySelectorAll(".tab").forEach(function(x){x.classList.remove("active");});
    document.querySelectorAll(".tab-panel").forEach(function(x){x.classList.remove("active");});
    t.classList.add("active");document.getElementById("panel-"+t.dataset.tab).classList.add("active");
  });});

  document.querySelectorAll(".legend-item[data-filter]").forEach(function(el){el.addEventListener("click",function(){onLegendClick(el.dataset.filter);});});

  document.querySelectorAll(".vt-btn").forEach(function(btn){btn.addEventListener("click",function(){
    viewMode=btn.dataset.view;
    document.querySelectorAll(".vt-btn").forEach(function(b){b.classList.remove("active");});btn.classList.add("active");
    document.getElementById("view-hint").textContent=viewMode==="class"?"Methods collapsed into parent class nodes":"Each function/method is an individual node";
    applyFilter();
  });});

  document.getElementById("dir-all").addEventListener("click",function(){
    document.querySelectorAll("#dir-list input[type=checkbox]").forEach(function(cb){cb.checked=true;});applyFilter();
  });
  document.getElementById("dir-none").addEventListener("click",function(){
    document.querySelectorAll("#dir-list input[type=checkbox]").forEach(function(cb){cb.checked=false;});applyFilter();
  });

  fetch("/status").then(function(r){return r.json();}).then(function(d){
    if(d.repo_path){
      document.getElementById("path-input").value=d.repo_path;
      if(d.total_nodes>0){
        document.getElementById("resync-btn").disabled=false;
        document.getElementById("clear-btn").disabled=false;
        loadGraph();
      }
    }
  }).catch(function(){});
}
init();
</script></body></html>"""
