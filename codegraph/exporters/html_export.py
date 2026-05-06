"""HTML export for CodeGraph — D3.js v7 left-to-right flow layout.

v7: persistent multi-select, directory filter, class/function toggle,
    light/dark theme, reactive visible-node counter.
"""

from __future__ import annotations

import html as html_mod
import json
from collections import defaultdict
from pathlib import Path

from codegraph.models import CodeGraph

_ENTRY_DECORATORS = {
    "get", "post", "put", "delete", "patch", "head", "options",
    "route", "api_route", "websocket", "command", "group",
    "app_route", "endpoint", "on_event", "middleware",
    "task", "periodic_task", "test", "fixture", "parametrize",
}


def export_html(graph: CodeGraph, output_path: str) -> tuple[int, int]:
    """Generate a self-contained HTML visualization and return (node_count, edge_count)."""

    top_control = sorted(
        [n for n in graph.nodes if n.node_type != "external"],
        key=lambda n: n.in_degree,
        reverse=True,
    )[:30]
    top10_ids = {n.id for n in top_control[:10]}
    top30_ids = {n.id for n in top_control[10:30]}

    files = sorted({n.file for n in graph.nodes if n.node_type not in ("external", "dispatch")})
    file_index = {f: i for i, f in enumerate(files)}

    dir_counts: dict[str, int] = defaultdict(int)
    for n in graph.nodes:
        if n.node_type in ("external", "dispatch"):
            continue
        parts = n.file.split("/")
        dir_key = parts[0] + "/" if len(parts) > 1 else "(root)"
        dir_counts[dir_key] += 1
    directories = sorted(dir_counts.items(), key=lambda x: -x[1])

    graph_nodes = []
    for n in graph.nodes:
        is_entry = False
        if n.decorators:
            for dec in n.decorators:
                if dec.lower() in _ENTRY_DECORATORS:
                    is_entry = True
                    break
        if not is_entry and n.in_degree == 0 and n.out_degree >= 3 and n.node_type not in ("external", "dispatch"):
            is_entry = True
        is_orphan = n.in_degree == 0 and n.out_degree == 0 and n.node_type not in ("external", "dispatch")

        if n.node_type == "dispatch":
            color_class = "dispatch"
        elif is_entry:
            color_class = "entry"
        elif is_orphan:
            color_class = "orphan"
        elif n.id in top10_ids:
            color_class = "top10"
        elif n.id in top30_ids:
            color_class = "top30"
        elif n.node_type == "external":
            color_class = "external"
        else:
            color_class = "internal"

        n.color_class = color_class
        n.file_group = file_index.get(n.file, len(files))
        graph_nodes.append(n.to_dict())

    top_20_list = "\n".join(
        f'<div class="cp-item" data-id="{html_mod.escape(n.id)}">'
        f'<span class="cp-rank">#{i+1}</span> '
        f'<span class="cp-name">{html_mod.escape(n.short_name)}</span> '
        f'<span class="cp-degree">in:{n.in_degree}</span></div>'
        for i, n in enumerate(top_control[:20])
    )
    entry_nodes = [d for d in graph_nodes if d["color_class"] == "entry"]
    entry_list = "\n".join(
        f'<div class="cp-item" data-id="{html_mod.escape(n["id"])}">'
        f'<span class="ep-marker">&#9654;</span> '
        f'<span class="cp-name">{html_mod.escape(n["short_name"])}</span> '
        f'<span class="cp-degree">out:{n["out_degree"]}</span></div>'
        for n in sorted(entry_nodes, key=lambda x: x["out_degree"], reverse=True)[:20]
    )
    dispatch_nodes = [d for d in graph_nodes if d["color_class"] == "dispatch"]
    dispatch_list = "\n".join(
        f'<div class="cp-item" data-id="{html_mod.escape(n["id"])}">'
        f'<span class="dp-marker">&#9670;</span> '
        f'<span class="cp-name">{html_mod.escape(n["short_name"])}</span> '
        f'<span class="cp-degree">out:{n["out_degree"]}</span></div>'
        for n in sorted(dispatch_nodes, key=lambda x: x["out_degree"], reverse=True)[:20]
    )

    dir_filter_html = ""
    for dir_name, count in directories:
        escaped = html_mod.escape(dir_name)
        dir_filter_html += (
            f'<label class="dir-item"><input type="checkbox" checked '
            f'data-dir="{escaped}"><span class="dir-name">{escaped}</span>'
            f'<span class="dir-count">{count}</span></label>\n'
        )

    graph_data = {
        "nodes": graph_nodes,
        "links": [e.to_dict() for e in graph.edges],
        "stats": graph.stats,
    }
    json_data = json.dumps(graph_data, separators=(",", ":"))

    html_content = _build_html(json_data, top_20_list, entry_list, dispatch_list, dir_filter_html)
    Path(output_path).write_text(html_content, encoding="utf-8")
    return len(graph_nodes), len(graph.edges)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

def _build_html(
    json_data: str,
    top_20_list: str,
    entry_list: str,
    dispatch_list: str,
    dir_filter_html: str,
) -> str:
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CodeGraph</title>
<style>
/* ── Theme variables ── */
:root,[data-theme="dark"]{{
  --bg:#0d1117;--bg2:#161b22;--bg3:#1c2128;--text:#c9d1d9;--text2:#8b949e;
  --text-bright:#f0f6fc;--border:#30363d;--blue:#58a6ff;--green:#39d353;
  --red:#f85149;--purple:#a371f7;--orange:#f0883e;--yellow:#d29922;
  --dim:#484f58;--node-stroke:#0d1117;
  --edge-def:#30363d;--edge-dim:rgba(48,54,61,0.12);--edge-hl:#58a6ff;
  --shadow:rgba(0,0,0,0.4);--hover-bg:#21262d;
}}
[data-theme="light"]{{
  --bg:#ffffff;--bg2:#f6f8fa;--bg3:#eaeef2;--text:#1f2328;--text2:#636c76;
  --text-bright:#1f2328;--border:#d0d7de;--blue:#0969da;--green:#1a7f37;
  --red:#cf222e;--purple:#8250df;--orange:#bc4c00;--yellow:#9a6700;
  --dim:#8c959f;--node-stroke:#ffffff;
  --edge-def:#afb8c1;--edge-dim:rgba(175,184,193,0.2);--edge-hl:#0969da;
  --shadow:rgba(140,149,159,0.15);--hover-bg:#eaeef2;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;overflow:hidden;height:100vh;width:100vw}}
#app{{display:flex;height:100vh}}

/* ── Sidebar ── */
#sidebar{{width:320px;min-width:320px;background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}}
.sidebar-header{{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--border)}}
.sidebar-header h1{{font-size:16px;color:var(--text-bright)}}
.sidebar-header h1 span{{color:var(--blue)}} .sidebar-header h1 .ver{{color:var(--green);font-size:11px;margin-left:4px}}
#theme-toggle{{background:none;border:1px solid var(--border);border-radius:6px;padding:4px 8px;cursor:pointer;font-size:16px;color:var(--text);transition:all .15s}}
#theme-toggle:hover{{background:var(--hover-bg);border-color:var(--blue)}}
.section{{padding:10px 16px;border-bottom:1px solid var(--border);font-size:13px}}
.stat{{display:flex;justify-content:space-between;margin:3px 0}} .stat-val{{color:var(--blue);font-weight:600}}

/* ── Controls ── */
.controls label{{font-size:12px;color:var(--text2);display:block;margin-bottom:2px}}
.controls input[type=range]{{width:100%;accent-color:var(--blue)}}
.controls input[type=text]{{width:100%;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;outline:none;margin-bottom:8px}}
.controls input[type=text]:focus{{border-color:var(--blue)}}
.tval{{font-size:11px;color:var(--blue);float:right}}
.slider-help{{font-size:10px;color:var(--dim);margin-bottom:6px}}

/* ── View toggle ── */
.view-toggle{{display:flex;gap:4px;margin-bottom:8px}}
.vt-btn{{flex:1;padding:6px;text-align:center;font-size:12px;cursor:pointer;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text2);transition:all .15s}}
.vt-btn:hover{{border-color:var(--blue);color:var(--text)}}
.vt-btn.active{{background:var(--blue);color:#fff;border-color:var(--blue)}}
.view-hint{{font-size:10px;color:var(--dim);margin-bottom:8px;font-style:italic}}

/* ── Directory filter ── */
#dir-section{{max-height:200px;display:flex;flex-direction:column}}
.dir-header{{display:flex;align-items:center;gap:6px;margin-bottom:6px}}
.dir-title{{font-size:12px;color:var(--text2);font-weight:600;flex:1;text-transform:uppercase;letter-spacing:.5px}}
.dir-btn{{font-size:10px;padding:2px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text2);cursor:pointer;transition:all .15s}}
.dir-btn:hover{{border-color:var(--blue);color:var(--text)}}
.dir-list{{overflow-y:auto;flex:1}}
.dir-item{{display:flex;align-items:center;gap:6px;padding:2px 0;font-size:12px;cursor:pointer}}
.dir-item input{{accent-color:var(--blue);cursor:pointer}}
.dir-name{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.dir-count{{color:var(--dim);font-size:10px;min-width:28px;text-align:right}}

/* ── Impact box ── */
.impact-box{{padding:10px 16px;border-bottom:1px solid var(--border);font-size:12px;background:var(--bg3);display:none}}
.impact-box.active{{display:block}}
.impact-box .ib-title{{color:var(--text-bright);font-weight:600;margin-bottom:4px;font-size:13px}}
.impact-box .ib-stat{{margin:2px 0}}
.ib-downstream{{color:var(--orange);font-weight:600}} .ib-upstream{{color:var(--purple);font-weight:600}}
.ib-hint{{color:var(--dim);font-size:11px;margin-top:6px;font-style:italic}}

/* ── Tabs ── */
.tabs{{display:flex;border-bottom:1px solid var(--border)}}
.tab{{flex:1;padding:8px;text-align:center;font-size:12px;cursor:pointer;color:var(--text2);border-bottom:2px solid transparent;transition:all .15s}}
.tab:hover{{color:var(--text)}} .tab.active{{color:var(--blue);border-bottom-color:var(--blue)}}
.tab-panel{{flex:1;overflow-y:auto;padding:8px 16px;display:none}} .tab-panel.active{{display:block}}
.tab-panel h2{{font-size:12px;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px}}
.cp-item{{padding:5px 8px;border-radius:4px;cursor:pointer;font-size:12px;margin-bottom:2px;display:flex;align-items:center;gap:6px;transition:background .15s}}
.cp-item:hover{{background:var(--hover-bg)}}
.cp-rank{{color:var(--red);font-weight:700;min-width:28px}}
.ep-marker{{color:var(--purple);font-size:10px;min-width:16px}}
.dp-marker{{color:var(--orange);font-size:10px;min-width:16px}}
.cp-name{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cp-degree{{color:var(--text2);font-size:11px}}

/* ── Graph ── */
#graph-container{{flex:1;position:relative;overflow:hidden}}
svg{{width:100%;height:100%}}
.tooltip{{position:absolute;pointer-events:none;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px;font-size:12px;line-height:1.5;max-width:350px;box-shadow:0 8px 24px var(--shadow);display:none;z-index:100}}
.tooltip .tt-name{{color:var(--text-bright);font-weight:600;font-size:14px}}
.tooltip .tt-file{{color:var(--blue)}} .tooltip .tt-meta{{color:var(--text2)}}
.tooltip .tt-doc{{color:var(--green);font-style:italic;margin-top:4px}}
.tt-tag{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;margin-top:4px}}
.tt-tag-entry{{background:rgba(130,80,223,0.15);color:var(--purple)}}
.tt-tag-orphan{{background:rgba(140,149,159,0.15);color:var(--dim)}}
.tt-tag-ctrl{{background:rgba(207,34,46,0.15);color:var(--red)}}
.tt-tag-dispatch{{background:rgba(188,76,0,0.15);color:var(--orange)}}
.edge-tooltip{{position:absolute;pointer-events:none;background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:10px 14px;font-size:12px;line-height:1.5;box-shadow:0 8px 24px var(--shadow);display:none;z-index:100;max-width:400px}}

/* ── Legend ── */
.legend{{position:absolute;bottom:16px;right:16px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 14px;font-size:11px}}
.legend-item{{display:flex;align-items:center;gap:8px;margin:3px 0;cursor:pointer;padding:2px 4px;border-radius:4px;transition:background .15s}}
.legend-item:hover{{background:var(--hover-bg)}} .legend-item.active{{background:var(--bg3);outline:1px solid var(--blue)}}
.legend-dot{{width:12px;height:12px;border-radius:50%;flex-shrink:0}}
.legend-diamond{{width:12px;height:12px;flex-shrink:0;transform:rotate(45deg);border-radius:2px}}
.legend-line{{width:24px;height:3px;border-radius:2px;flex-shrink:0}}
.legend-sep{{margin-top:6px;padding-top:6px;border-top:1px solid var(--border)}}
.depth-label{{fill:var(--bg3);font-size:11px;font-weight:600;text-anchor:middle;pointer-events:none}}
.depth-lane{{stroke:var(--bg2);stroke-width:1;stroke-dasharray:4,4}}

/* ── No-modules overlay ── */
#no-modules-msg{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:var(--bg3);border:1px solid var(--border);border-radius:12px;padding:24px 40px;font-size:15px;color:var(--text2);text-align:center;display:none;z-index:50}}
</style></head><body>
<div id="app">
<div id="sidebar">
  <div class="sidebar-header">
    <h1>Code<span>Graph</span><span class="ver">v7</span></h1>
    <button id="theme-toggle" title="Toggle light/dark theme">&#9788;</button>
  </div>
  <div class="section" id="stats"></div>
  <div class="section controls">
    <div class="view-toggle">
      <button class="vt-btn active" data-view="function" title="Each function/method is an individual node">Functions</button>
      <button class="vt-btn" data-view="class" title="Methods collapsed into parent class nodes">Classes</button>
    </div>
    <div class="view-hint" id="view-hint">Each function/method is an individual node</div>
    <input type="text" id="search" placeholder="Search functions..." autocomplete="off">
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
    <div class="dir-list" id="dir-list">
      {dir_filter_html}
    </div>
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
  <div class="tab-panel active" id="panel-cp"><h2>Most called</h2><div id="cp-list">{top_20_list}</div></div>
  <div class="tab-panel" id="panel-ep"><h2>Entry points</h2><div id="ep-list">{entry_list}</div></div>
  <div class="tab-panel" id="panel-dp"><h2>Dispatch nodes</h2><div id="dp-list">{dispatch_list}</div></div>
</div>
<div id="graph-container">
  <svg id="graph"></svg>
  <div class="tooltip" id="tooltip"></div>
  <div class="edge-tooltip" id="edge-tooltip"></div>
  <div id="no-modules-msg">No modules selected.<br>Check directories in the sidebar to show nodes.</div>
  <div class="legend" id="legend">
    <div class="legend-item" data-filter="top10"><div class="legend-dot" style="background:var(--red)"></div>Control points</div>
    <div class="legend-item" data-filter="top30"><div class="legend-dot" style="background:var(--yellow)"></div>Top 11–30</div>
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
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
/* ──────── data & constants ──────── */
const DATA={json_data};
const COL={{top10:"#f85149",top30:"#d29922",internal:"#58a6ff",external:"#484f58",entry:"#a371f7",orphan:"#6e7681",dispatch:"#f0883e"}};
const COL_LIGHT={{top10:"#cf222e",top30:"#9a6700",internal:"#0969da",external:"#8c959f",entry:"#8250df",orphan:"#8c959f",dispatch:"#bc4c00"}};
const E={{def:"#30363d",defW:0.8,out:"#39d353",inc:"#bc8cff",down:"#f0883e",dim:"rgba(48,54,61,0.12)",hiW:2.5,dimW:0.2,opt:"rgba(240,136,62,0.4)"}};
const E_LIGHT={{def:"#afb8c1",defW:0.8,out:"#1a7f37",inc:"#8250df",down:"#bc4c00",dim:"rgba(175,184,193,0.2)",hiW:2.5,dimW:0.3,opt:"rgba(188,76,0,0.35)"}};
function C(){{return document.documentElement.getAttribute("data-theme")==="light"?COL_LIGHT:COL;}}
function EC(){{return document.documentElement.getAttribute("data-theme")==="light"?E_LIGHT:E;}}

/* ──────── state ──────── */
let sim,svg,g,laneG,linkG,nodeG,labelG,orderG;
let cNodes=[],cLinks=[];
let fwd={{}},bwd={{}};
let viewMode="function";
let selectedLegends=new Set();
let selectedNodeIds=new Set();
let lockedImpact=null;

/* ──────── theme ──────── */
let theme=localStorage.getItem("codegraph-theme")||"dark";
function applyTheme(){{
  document.documentElement.setAttribute("data-theme",theme);
  localStorage.setItem("codegraph-theme",theme);
  document.getElementById("theme-toggle").innerHTML=theme==="dark"?"&#9788;":"&#9790;";
  if(cNodes.length)refreshColors();
}}
function refreshColors(){{
  const c=C(),e=EC();
  nodeG.selectAll(".node").each(function(d){{
    const el=d3.select(this);
    if(d.color_class==="entry"||d.color_class==="dispatch"){{el.attr("fill",c[d.color_class]).attr("stroke","var(--node-stroke)");}}
    else if(d.color_class==="orphan"){{el.attr("stroke",c.orphan);}}
    else{{el.attr("fill",c[d.color_class]);if(d.color_class==="top10")el.attr("stroke","var(--node-stroke)");}}
  }});
  if(!hasActiveSelection()){{
    linkG.selectAll("line.edge").each(function(d){{
      d3.select(this).attr("stroke",d.type==="option"?e.opt:e.def);
    }});
  }}else{{applySelectionVisuals();}}
}}

/* ──────── adjacency & BFS ──────── */
function buildAdj(){{fwd={{}};bwd={{}};cLinks.forEach(l=>{{const s=l.source?.id||l.source,t=l.target?.id||l.target;if(!fwd[s])fwd[s]=[];fwd[s].push(t);if(!bwd[t])bwd[t]=[];bwd[t].push(s);}});}}
function bfs(start,adj,depth){{const v=new Set(),q=[[start,0]];v.add(start);while(q.length){{const[n,d]=q.shift();if(d>=depth)continue;(adj[n]||[]).forEach(nb=>{{if(!v.has(nb)){{v.add(nb);q.push([nb,d+1]);}}}});}};v.delete(start);return v;}}
function getDepth(){{return+document.getElementById("depth-slider").value;}}

/* ──────── class-view aggregation ──────── */
function buildClassData(nodes,links){{
  const classMap={{}},standalone=[];
  nodes.forEach(n=>{{
    if(n.class_name){{
      const key=n.file+":"+n.class_name;
      if(!classMap[key])classMap[key]={{id:key,short_name:n.class_name,file:n.file,class_name:n.class_name,function:n.class_name,lineno:n.lineno,params:0,lines:0,docstring:"",is_async:false,decorators:[],type:"class",in_degree:0,out_degree:0,color_class:n.color_class,file_group:n.file_group,depth:0,methods:[],memberIds:new Set()}};
      const cls=classMap[key];cls.methods.push(n);cls.memberIds.add(n.id);
      cls.in_degree+=n.in_degree;cls.out_degree+=n.out_degree;cls.lines+=n.lines;
      if(n.lineno<cls.lineno)cls.lineno=n.lineno;
      const pri={{top10:5,dispatch:4,entry:3,top30:2,internal:1,orphan:0,external:0}};
      if((pri[n.color_class]||0)>(pri[cls.color_class]||0))cls.color_class=n.color_class;
    }}else standalone.push(n);
  }});
  const classNodes=[];
  Object.values(classMap).forEach(cls=>{{cls.short_name=cls.class_name+" ("+cls.methods.length+" methods)";classNodes.push(cls);}});
  const m2c={{}};Object.values(classMap).forEach(cls=>{{cls.memberIds.forEach(mid=>{{m2c[mid]=cls.id;}});}});
  const edgeSet=new Set(),classLinks=[];
  links.forEach(l=>{{const s=l.source?.id||l.source,t=l.target?.id||l.target,ms=m2c[s]||s,mt=m2c[t]||t;if(ms===mt)return;const k=ms+"->"+mt;if(!edgeSet.has(k)){{edgeSet.add(k);classLinks.push({{source:ms,target:mt,order:l.order,type:l.type}});}}}});
  return{{nodes:[...classNodes,...standalone],links:classLinks,m2c:m2c}};
}}

/* ──────── flow depth computation ──────── */
function computeFlowDepths(){{
  const depths={{}},queue=[];
  cNodes.forEach(n=>{{if(n.color_class==="entry"){{depths[n.id]=0;queue.push(n.id);}}}});
  if(queue.length===0)cNodes.forEach(n=>{{if(n.in_degree===0&&n.type!=="external"&&n.type!=="dispatch"&&n.out_degree>0){{depths[n.id]=0;queue.push(n.id);}}}});
  let head=0;while(head<queue.length){{const nid=queue[head++];(fwd[nid]||[]).forEach(t=>{{if(!(t in depths)){{depths[t]=depths[nid]+1;queue.push(t);}}}});}}
  const maxD=Math.max(...Object.values(depths),1);
  cNodes.forEach(n=>{{
    if(n.id in depths)n.depth=depths[n.id];
    else if(n.type==="external")n.depth=maxD+1;
    else if(n.type==="dispatch")n.depth=Math.round(maxD*0.6);
    else if(n.color_class==="orphan")n.depth=maxD+2;
    else if(n.out_degree===0)n.depth=maxD;
    else if(n.in_degree===0)n.depth=0;
    else n.depth=Math.round((n.in_degree/(n.in_degree+n.out_degree))*maxD);
  }});
  return maxD;
}}

/* ──────── directory filter helpers ──────── */
function getCheckedDirs(){{
  const s=new Set();
  document.querySelectorAll("#dir-list input[type=checkbox]:checked").forEach(cb=>s.add(cb.dataset.dir));
  return s;
}}
function allDirCount(){{return document.querySelectorAll("#dir-list input[type=checkbox]").length;}}

/* ──────── selection model ──────── */
function hasActiveSelection(){{return selectedLegends.size>0||selectedNodeIds.size>0||lockedImpact!==null;}}

function computeHighlightSet(){{
  const h=new Set();
  if(selectedLegends.size>0)cNodes.forEach(n=>{{if(selectedLegends.has(n.color_class))h.add(n.id);}});
  selectedNodeIds.forEach(id=>h.add(id));
  if(lockedImpact){{
    h.add(lockedImpact.focusId);
    lockedImpact.downIds.forEach(id=>h.add(id));
    lockedImpact.upIds.forEach(id=>h.add(id));
    if(lockedImpact.directOut)lockedImpact.directOut.forEach(id=>h.add(id));
    if(lockedImpact.directIn)lockedImpact.directIn.forEach(id=>h.add(id));
  }}
  return h;
}}

function applySelectionVisuals(){{
  if(!hasActiveSelection()){{resetVisuals();updateStats();return;}}
  const h=computeHighlightSet(),e=EC();
  nodeG.selectAll(".node").attr("opacity",d=>h.has(d.id)?1:0.06);
  labelG.selectAll("text").attr("opacity",d=>h.has(d.id)?1:0.06);
  if(lockedImpact){{
    const{{focusId,downIds,upIds,directOut,directIn}}=lockedImpact;
    linkG.selectAll("line.edge").each(function(l){{
      const s=l.source?.id||l.source,t=l.target?.id||l.target,el=d3.select(this);
      if(s===focusId&&(directOut&&directOut.has(t)))el.attr("stroke",e.out).attr("stroke-width",e.hiW).attr("marker-end","url(#arr-out)").attr("opacity",1);
      else if(t===focusId&&(directIn&&directIn.has(s)))el.attr("stroke",e.inc).attr("stroke-width",e.hiW).attr("marker-end","url(#arr-in)").attr("opacity",1);
      else if(s===focusId)el.attr("stroke",e.out).attr("stroke-width",e.hiW).attr("marker-end","url(#arr-out)").attr("opacity",1);
      else if(t===focusId)el.attr("stroke",e.inc).attr("stroke-width",e.hiW).attr("marker-end","url(#arr-in)").attr("opacity",1);
      else if(downIds.has(s)&&downIds.has(t))el.attr("stroke",e.down).attr("stroke-width",1.3).attr("marker-end","url(#arr-dn)").attr("opacity",0.6);
      else if(upIds.has(s)&&upIds.has(t))el.attr("stroke",e.inc).attr("stroke-width",1.3).attr("marker-end","url(#arr-in)").attr("opacity",0.5);
      else if(h.has(s)&&h.has(t))el.attr("stroke",e.def).attr("stroke-width",1).attr("marker-end","url(#arr-def)").attr("opacity",0.7);
      else el.attr("stroke",e.dim).attr("stroke-width",e.dimW).attr("marker-end","").attr("opacity",1);
    }});
  }}else{{
    linkG.selectAll("line.edge").each(function(l){{
      const s=l.source?.id||l.source,t=l.target?.id||l.target,el=d3.select(this),e2=EC();
      if(h.has(s)&&h.has(t))el.attr("stroke",e2.def).attr("stroke-width",1.5).attr("marker-end","url(#arr-def)").attr("opacity",0.8);
      else if(h.has(s)||h.has(t))el.attr("stroke",e2.def).attr("stroke-width",0.8).attr("marker-end","url(#arr-def)").attr("opacity",0.3);
      else el.attr("stroke",e2.dim).attr("stroke-width",e2.dimW).attr("marker-end","").attr("opacity",1);
    }});
  }}
  updateStats();
}}

function clearAllSelections(){{
  selectedLegends.clear();selectedNodeIds.clear();lockedImpact=null;
  document.querySelectorAll(".legend-item[data-filter]").forEach(x=>x.classList.remove("active"));
  document.getElementById("impact-box").classList.remove("active");
  document.getElementById("edge-tooltip").style.display="none";
  orderG.selectAll("*").remove();
  resetVisuals();updateStats();
}}

/* ──────── filter pipeline ──────── */
function applyFilter(){{
  const mI=+document.getElementById("in-slider").value,mO=+document.getElementById("out-slider").value;
  const q=document.getElementById("search").value.toLowerCase();
  const checkedDirs=getCheckedDirs(),nDirs=allDirCount(),dirActive=checkedDirs.size<nDirs;

  const keep=new Set();
  DATA.nodes.forEach(n=>{{
    if(n.type==="external"){{if(n.in_degree>=3)keep.add(n.id);}}
    else if(n.type==="dispatch"){{if(n.in_degree>=1||n.out_degree>=1)keep.add(n.id);}}
    else if(n.in_degree>=mI||n.out_degree>=mO||n.color_class==="entry")keep.add(n.id);
  }});
  let fN=DATA.nodes.filter(n=>keep.has(n.id)),fL=DATA.links.filter(l=>keep.has(l.source?.id||l.source)&&keep.has(l.target?.id||l.target));

  if(q){{const m=new Set();
    fN.forEach(n=>{{if(n.short_name.toLowerCase().includes(q)||n.file.toLowerCase().includes(q))m.add(n.id);}});
    fL.forEach(l=>{{const s=l.source?.id||l.source,t=l.target?.id||l.target;if(m.has(s))m.add(t);if(m.has(t))m.add(s);}});
    fN=fN.filter(n=>m.has(n.id));fL=fL.filter(l=>m.has(l.source?.id||l.source)&&m.has(l.target?.id||l.target));
  }}

  if(dirActive){{
    if(checkedDirs.size===0){{fN=[];fL=[];}}
    else{{
      fN=fN.filter(n=>{{
        if(n.type==="external"||n.type==="dispatch")return true;
        const p=n.file.split("/");return checkedDirs.has(p.length>1?p[0]+"/":"(root)");
      }});
      const ids=new Set(fN.map(n=>n.id));
      fL=fL.filter(l=>ids.has(l.source?.id||l.source)&&ids.has(l.target?.id||l.target));
    }}
  }}

  if(viewMode==="class"){{
    const cd=buildClassData(fN,fL);cNodes=cd.nodes;cLinks=cd.links;
  }}else{{cNodes=fN;cLinks=fL;}}

  selectedLegends.clear();selectedNodeIds.clear();lockedImpact=null;
  document.querySelectorAll(".legend-item[data-filter]").forEach(x=>x.classList.remove("active"));
  document.getElementById("impact-box").classList.remove("active");
  document.getElementById("edge-tooltip").style.display="none";

  const msg=document.getElementById("no-modules-msg");
  msg.style.display=cNodes.length===0?"block":"none";

  buildAdj();updateStats();render();
}}

/* ──────── stats ──────── */
function updateStats(){{
  const s=DATA.stats,entries=cNodes.filter(n=>n.color_class==="entry").length;
  const dispatches=cNodes.filter(n=>n.color_class==="dispatch").length,maxD=d3.max(cNodes,n=>n.depth)||0;
  let vis=cNodes.length;
  if(hasActiveSelection())vis=computeHighlightSet().size;
  document.getElementById("stats").innerHTML=
    '<div class="stat"><span>Full codebase</span><span class="stat-val">'+s.total_functions+'</span></div>'+
    '<div class="stat"><span>Visible nodes</span><span class="stat-val" id="vis-count">'+vis+'</span></div>'+
    '<div class="stat"><span>Visible edges</span><span class="stat-val">'+cLinks.length+'</span></div>'+
    '<div class="stat"><span>Entry points</span><span class="stat-val" style="color:var(--purple)">'+entries+'</span></div>'+
    '<div class="stat"><span>Dispatch nodes</span><span class="stat-val" style="color:var(--orange)">'+dispatches+'</span></div>'+
    '<div class="stat"><span>Flow depth</span><span class="stat-val">'+maxD+' layers</span></div>'+
    '<div class="stat"><span>View</span><span class="stat-val">'+(viewMode==="class"?"Class":"Function")+'</span></div>';
}}

/* ──────── render ──────── */
function render(){{
  const container=document.getElementById("graph-container"),W=container.clientWidth,H=container.clientHeight;
  const c=C(),e=EC();
  const maxC=d3.max(cNodes,d=>d.in_degree+d.out_degree)||1;
  const rS=d3.scaleSqrt().domain([0,maxC]).range([3,18]);
  if(sim)sim.stop();orderG.selectAll("*").remove();laneG.selectAll("*").remove();

  const maxD=computeFlowDepths(),totalD=maxD+3,pad=60;
  const depthLabels=["Entry","","","Mid","","","","Deep","","Utility","External","Orphan"];
  for(let i=0;i<=Math.min(totalD,11);i++){{
    const x=pad+(i/totalD)*(W-2*pad);
    laneG.append("line").attr("class","depth-lane").attr("x1",x).attr("y1",30).attr("x2",x).attr("y2",H-10);
    if(depthLabels[i])laneG.append("text").attr("class","depth-label").attr("x",x).attr("y",18).text(depthLabels[i]);
  }}

  sim=d3.forceSimulation(cNodes)
    .force("link",d3.forceLink(cLinks).id(d=>d.id).distance(30).strength(0.05))
    .force("charge",d3.forceManyBody().strength(-30).distanceMax(150))
    .force("x",d3.forceX(d=>pad+(d.depth/totalD)*(W-2*pad)).strength(0.85))
    .force("y",d3.forceY(d=>{{
      const ln=cNodes.filter(n=>n.depth===d.depth),idx=ln.indexOf(d),cnt=ln.length;
      return cnt<=1?H/2:pad+((idx/(cnt-1))*(H-2*pad));
    }}).strength(0.15))
    .force("collision",d3.forceCollide().radius(d=>rS(d.in_degree+d.out_degree)+3).strength(0.8))
    .alphaDecay(0.025).velocityDecay(0.5);

  const lk=linkG.selectAll("line.edge").data(cLinks,d=>(d.source?.id||d.source)+"-"+(d.target?.id||d.target));
  lk.exit().remove();
  const lkE=lk.enter().append("line").attr("class","edge")
    .attr("stroke",d=>d.type==="option"?e.opt:e.def)
    .attr("stroke-width",d=>d.type==="option"?0.6:e.defW)
    .attr("stroke-dasharray",d=>d.type==="option"?"4,3":d.type==="dynamic"?"2,2":null)
    .attr("marker-end","url(#arr-def)");
  const lkM=lkE.merge(lk);

  const ht=linkG.selectAll("line.ehit").data(cLinks,d=>(d.source?.id||d.source)+"-"+(d.target?.id||d.target));
  ht.exit().remove();
  const htE=ht.enter().append("line").attr("class","ehit").attr("stroke","transparent").attr("stroke-width",12).style("cursor","pointer")
    .on("mouseover",edgeHover).on("mousemove",edgeMove).on("mouseout",edgeOut).on("click",onEdgeClick);
  const htM=htE.merge(ht);

  const nd=nodeG.selectAll(".node").data(cNodes,d=>d.id);nd.exit().remove();
  const ndE=nd.enter().append(d=>document.createElementNS("http://www.w3.org/2000/svg",
    d.color_class==="entry"||d.color_class==="dispatch"?"rect":"circle"))
    .attr("class","node").style("cursor","pointer")
    .on("mouseover",showTip).on("mouseout",hideTip)
    .on("click",(ev,d)=>{{ev.stopPropagation();onNodeClick(d);}})
    .call(d3.drag().on("start",ds).on("drag",dg).on("end",de));
  const ndM=ndE.merge(nd);
  ndM.each(function(d){{const el=d3.select(this),r=rS(d.in_degree+d.out_degree);
    if(d.color_class==="entry")el.attr("width",r*2).attr("height",r*2).attr("rx",3).attr("ry",3).attr("fill",c.entry).attr("opacity",0.9).attr("stroke","var(--node-stroke)").attr("stroke-width",1.5);
    else if(d.color_class==="dispatch")el.attr("width",r*2).attr("height",r*2).attr("rx",3).attr("ry",3).attr("fill",c.dispatch).attr("opacity",0.85).attr("stroke","var(--node-stroke)").attr("stroke-width",1.5);
    else if(d.color_class==="orphan")el.attr("r",Math.max(r,3)).attr("fill","none").attr("stroke",c.orphan).attr("stroke-width",1.5).attr("stroke-dasharray","3,2").attr("opacity",0.5);
    else el.attr("r",r).attr("fill",c[d.color_class]).attr("stroke",d.color_class==="top10"?"var(--node-stroke)":"none").attr("stroke-width",d.color_class==="top10"?2:0).attr("opacity",d.type==="external"?0.45:0.85);
  }});

  const topN=cNodes.filter(d=>["top10","top30","entry","dispatch"].includes(d.color_class));
  const lb=labelG.selectAll("text.nlabel").data(topN,d=>d.id);lb.exit().remove();
  const lbE=lb.enter().append("text").attr("class","nlabel")
    .text(d=>d.short_name.length>30?d.short_name.slice(0,27)+"...":d.short_name)
    .attr("font-size",d=>d.color_class==="top10"?9:7)
    .attr("fill",d=>d.color_class==="entry"?"var(--purple)":d.color_class==="dispatch"?"var(--orange)":d.color_class==="top10"?"var(--text-bright)":"var(--text2)")
    .attr("text-anchor","middle").attr("dy",d=>-rS(d.in_degree+d.out_degree)-4).attr("pointer-events","none");
  const lbM=lbE.merge(lb);

  sim.on("tick",()=>{{
    lkM.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
    htM.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
    ndM.each(function(d){{const el=d3.select(this);if(d.color_class==="entry"||d.color_class==="dispatch"){{const r=rS(d.in_degree+d.out_degree);el.attr("x",d.x-r).attr("y",d.y-r);}}else el.attr("cx",d.x).attr("cy",d.y);}});
    lbM.attr("x",d=>d.x).attr("y",d=>d.y);
    orderG.selectAll(".olabel").each(function(){{const el=d3.select(this),lnk=el.datum();
      if(lnk&&lnk.source&&lnk.target)el.attr("x",(lnk.source.x+lnk.target.x)/2).attr("y",(lnk.source.y+lnk.target.y)/2-6);
    }});
  }});
}}

/* ──────── call-order numbers ──────── */
function showOrderNumbers(nodeId){{
  orderG.selectAll("*").remove();
  cLinks.filter(l=>(l.source?.id||l.source)===nodeId).forEach(l=>{{
    if(l.order){{
      orderG.append("circle").attr("class","olabel").attr("r",7).attr("fill","var(--bg)").attr("stroke","var(--green)").attr("stroke-width",1.5).datum(l);
      orderG.append("text").attr("class","olabel").text(l.order).attr("fill","var(--green)").attr("font-size",8).attr("font-weight",700).attr("text-anchor","middle").attr("dominant-baseline","central").datum(l);
    }}
  }});
}}

/* ──────── tooltip ──────── */
function showTip(event,d){{
  const tt=document.getElementById("tooltip"),async_=d.is_async?" async":"";
  const decos=d.decorators&&d.decorators.length?'<div class="tt-meta">@'+d.decorators.join(", @")+'</div>':"";
  let tag="";
  if(d.color_class==="entry")tag='<span class="tt-tag tt-tag-entry">ENTRY POINT</span>';
  else if(d.color_class==="orphan")tag='<span class="tt-tag tt-tag-orphan">ORPHANED</span>';
  else if(d.color_class==="top10")tag='<span class="tt-tag tt-tag-ctrl">CONTROL POINT</span>';
  else if(d.color_class==="dispatch")tag='<span class="tt-tag tt-tag-dispatch">DISPATCH</span>';
  const methods=d.methods?'<div class="tt-meta">Methods: '+d.methods.length+'</div>':"";
  tt.innerHTML='<div class="tt-name">'+d.short_name+async_+'</div><div class="tt-file">'+d.file+":"+d.lineno+'</div>'+
    '<div class="tt-meta">in: '+d.in_degree+' | out: '+d.out_degree+' | '+d.lines+' lines | depth: '+d.depth+'</div>'+
    decos+methods+tag+(d.docstring?'<div class="tt-doc">'+d.docstring+'</div>':"");
  tt.style.display="block";tt.style.left=(event.pageX-320+15)+"px";tt.style.top=(event.pageY+15)+"px";
}}
function hideTip(){{document.getElementById("tooltip").style.display="none";}}

/* ──────── edge tooltip & hover ──────── */
function edgeHover(event,d){{
  if(hasActiveSelection()){{
    showEdgeTooltipOnly(event,d);return;
  }}
  const s=d.source?.id||d.source,t=d.target?.id||d.target,dep=getDepth(),e=EC();
  const down=bfs(t,fwd,dep),up=bfs(s,bwd,dep),all=new Set([s,t,...down,...up]);
  nodeG.selectAll(".node").attr("opacity",nd=>all.has(nd.id)?1:0.05);
  labelG.selectAll("text").attr("opacity",nd=>all.has(nd.id)?1:0.05);
  linkG.selectAll("line.edge").each(function(l){{const ls=l.source?.id||l.source,lt=l.target?.id||l.target;const el=d3.select(this);
    if(ls===s&&lt===t)el.attr("stroke","var(--text-bright)").attr("stroke-width",3).attr("opacity",1);
    else if(down.has(ls)&&down.has(lt)||ls===t&&down.has(lt))el.attr("stroke",e.down).attr("stroke-width",1.5).attr("opacity",0.7);
    else if(up.has(ls)&&up.has(lt)||lt===s&&up.has(ls))el.attr("stroke",e.inc).attr("stroke-width",1.5).attr("opacity",0.6);
    else el.attr("stroke",e.dim).attr("stroke-width",e.dimW).attr("opacity",1);
  }});
  showEdgeTooltipOnly(event,d);
}}
function showEdgeTooltipOnly(event,d){{
  const s=d.source?.id||d.source,t=d.target?.id||d.target,dep=getDepth();
  const down=bfs(t,fwd,dep),up=bfs(s,bwd,dep);
  const sn=cNodes.find(n=>n.id===s),tn=cNodes.find(n=>n.id===t);
  const ett=document.getElementById("edge-tooltip");
  ett.innerHTML='<div style="color:var(--text-bright);font-weight:600">'+(sn?sn.short_name:s)+' → '+(tn?tn.short_name:t)+'</div>'+
    (d.order?'<div style="color:var(--green)">Call order: #'+d.order+'</div>':'')+
    (d.type&&d.type!=="resolved"?'<div style="color:var(--orange)">Type: '+d.type+'</div>':'')+
    '<div style="color:var(--orange);margin-top:3px">Downstream: '+down.size+'</div><div style="color:var(--purple)">Upstream: '+up.size+'</div>'+
    '<div style="color:var(--dim);font-size:11px;margin-top:4px">Click to add to selection</div>';
  ett.style.display="block";
}}
function edgeMove(event){{const e=document.getElementById("edge-tooltip");e.style.left=(event.pageX-320+15)+"px";e.style.top=(event.pageY+15)+"px";}}
function edgeOut(){{document.getElementById("edge-tooltip").style.display="none";if(!hasActiveSelection())resetVisuals();}}

/* ──────── click handlers ──────── */
function onNodeClick(d){{
  if(selectedNodeIds.has(d.id)){{
    selectedNodeIds.delete(d.id);
    if(lockedImpact&&lockedImpact.focusId===d.id)lockedImpact=null;
  }}else{{
    selectedNodeIds.add(d.id);
    const dep=getDepth(),down=bfs(d.id,fwd,dep),up=bfs(d.id,bwd,dep);
    lockedImpact={{focusId:d.id,downIds:down,upIds:up,directOut:new Set(fwd[d.id]||[]),directIn:new Set(bwd[d.id]||[])}};
    showOrderNumbers(d.id);showImpactBox(d.short_name,down.size,up.size);
  }}
  applySelectionVisuals();
}}
function onEdgeClick(event,d){{
  event.stopPropagation();
  const s=d.source?.id||d.source,t=d.target?.id||d.target,dep=getDepth();
  const down=bfs(t,fwd,dep),up=bfs(s,bwd,dep);
  selectedNodeIds.add(s);selectedNodeIds.add(t);
  lockedImpact={{focusId:s,downIds:new Set([t,...down]),upIds:up,directOut:new Set(fwd[s]||[]),directIn:new Set(bwd[s]||[])}};
  showOrderNumbers(s);
  const sn=cNodes.find(n=>n.id===s),tn=cNodes.find(n=>n.id===t);
  showImpactBox((sn?sn.short_name:s)+" → "+(tn?tn.short_name:t),down.size+1,up.size);
  document.getElementById("edge-tooltip").style.display="none";
  applySelectionVisuals();
}}
function onLegendClick(cls){{
  if(selectedLegends.has(cls))selectedLegends.delete(cls);else selectedLegends.add(cls);
  document.querySelectorAll(".legend-item[data-filter]").forEach(el=>el.classList.toggle("active",selectedLegends.has(el.dataset.filter)));
  applySelectionVisuals();
}}

/* ──────── impact box ──────── */
function showImpactBox(title,down,up){{const box=document.getElementById("impact-box");box.classList.add("active");
  document.getElementById("ib-title").textContent=title;document.getElementById("ib-down").textContent=down;document.getElementById("ib-up").textContent=up;}}

/* ──────── reset helpers ──────── */
function resetVisuals(){{
  orderG.selectAll("*").remove();const c=C(),e=EC();
  nodeG.selectAll(".node").attr("opacity",d=>d.color_class==="orphan"?0.5:d.type==="external"?0.45:0.85);
  labelG.selectAll("text").attr("opacity",1);
  linkG.selectAll("line.edge").each(function(d){{
    d3.select(this).attr("stroke",d.type==="option"?e.opt:e.def)
      .attr("stroke-width",d.type==="option"?0.6:e.defW)
      .attr("stroke-dasharray",d.type==="option"?"4,3":d.type==="dynamic"?"2,2":null)
      .attr("marker-end","url(#arr-def)").attr("opacity",1);
  }});
}}
function debounce(fn,ms){{let t;return function(){{clearTimeout(t);t=setTimeout(fn,ms);}};}}
function ds(e,d){{if(!e.active)sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y;}}
function dg(e,d){{d.fx=e.x;d.fy=e.y;}}
function de(e,d){{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}}

/* ──────── init ──────── */
function init(){{
  applyTheme();
  svg=d3.select("#graph");g=svg.append("g");
  laneG=g.append("g");linkG=g.append("g");nodeG=g.append("g");labelG=g.append("g");orderG=g.append("g");
  const defs=svg.append("defs");
  [["arr-def",EC().def],["arr-out",EC().out],["arr-in",EC().inc],["arr-dn",EC().down]].forEach(([id,col])=>{{
    defs.append("marker").attr("id",id).attr("viewBox","0 -3 6 6").attr("refX",14).attr("refY",0)
      .attr("markerWidth",5).attr("markerHeight",5).attr("orient","auto")
      .append("path").attr("d","M0,-3L6,0L0,3").attr("fill",col);
  }});
  svg.call(d3.zoom().scaleExtent([0.05,10]).on("zoom",e=>g.attr("transform",e.transform)));
  svg.on("click",function(e){{if(e.target===this||e.target.tagName==="svg")clearAllSelections();}});

  document.getElementById("theme-toggle").addEventListener("click",()=>{{theme=theme==="dark"?"light":"dark";applyTheme();}});

  document.getElementById("search").addEventListener("input",debounce(applyFilter,300));
  ["in-slider","out-slider"].forEach(id=>document.getElementById(id).addEventListener("input",()=>{{
    document.getElementById("in-val").textContent=document.getElementById("in-slider").value;
    document.getElementById("out-val").textContent=document.getElementById("out-slider").value;
    applyFilter();
  }}));
  document.getElementById("depth-slider").addEventListener("input",()=>{{
    document.getElementById("depth-val").textContent=document.getElementById("depth-slider").value;
    if(lockedImpact){{
      const d=cNodes.find(n=>n.id===lockedImpact.focusId);
      if(d){{const dep=getDepth(),down=bfs(d.id,fwd,dep),up=bfs(d.id,bwd,dep);
        lockedImpact={{focusId:d.id,downIds:down,upIds:up,directOut:new Set(fwd[d.id]||[]),directIn:new Set(bwd[d.id]||[])}};
        showImpactBox(d.short_name,down.size,up.size);applySelectionVisuals();}}
    }}
  }});

  document.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>{{
    document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(x=>x.classList.remove("active"));
    t.classList.add("active");document.getElementById("panel-"+t.dataset.tab).classList.add("active");
  }}));
  document.querySelectorAll(".cp-item").forEach(el=>el.addEventListener("click",()=>{{
    const nd=cNodes.find(n=>n.id===el.dataset.id);if(nd)onNodeClick(nd);
  }}));

  document.querySelectorAll(".legend-item[data-filter]").forEach(el=>el.addEventListener("click",()=>onLegendClick(el.dataset.filter)));

  document.querySelectorAll(".vt-btn").forEach(btn=>btn.addEventListener("click",()=>{{
    viewMode=btn.dataset.view;
    document.querySelectorAll(".vt-btn").forEach(b=>b.classList.remove("active"));btn.classList.add("active");
    document.getElementById("view-hint").textContent=viewMode==="class"?"Methods collapsed into parent class nodes":"Each function/method is an individual node";
    applyFilter();
  }}));

  document.querySelectorAll("#dir-list input[type=checkbox]").forEach(cb=>cb.addEventListener("change",applyFilter));
  document.getElementById("dir-all").addEventListener("click",()=>{{
    document.querySelectorAll("#dir-list input[type=checkbox]").forEach(cb=>cb.checked=true);applyFilter();
  }});
  document.getElementById("dir-none").addEventListener("click",()=>{{
    document.querySelectorAll("#dir-list input[type=checkbox]").forEach(cb=>cb.checked=false);applyFilter();
  }});

  applyFilter();
}}
init();
</script></body></html>"""
