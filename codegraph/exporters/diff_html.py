"""Generate a diff visualization HTML comparing two code graph snapshots.

Uses D3.js v7 with a left-to-right flow layout.
Nodes and edges are color-coded by diff status: added, removed, modified, unchanged.
"""

from __future__ import annotations

import json
import html as html_mod
from pathlib import Path

from codegraph.diff import GraphDiff


def generate_diff_html(diff: GraphDiff, output_path: str | Path) -> int:
    """Write an interactive diff visualization HTML file.

    Returns the total number of nodes in the combined graph.
    """
    old_g = diff.old_graph
    new_g = diff.new_graph
    if not old_g or not new_g:
        raise ValueError("GraphDiff must include old_graph and new_graph for HTML export")

    added_set = set(diff.added_nodes)
    removed_set = set(diff.removed_nodes)
    modified_set = set(diff.modified_nodes)
    added_edges_set = set(diff.added_edges)
    removed_edges_set = set(diff.removed_edges)

    # Union of nodes from both graphs
    seen_ids: set[str] = set()
    all_node_ids: set[str] = set()
    graph_nodes: list[dict] = []

    def _node_dict(n, status: str) -> dict:
        return {
            "id": n.id, "short_name": n.short_name, "file": n.file,
            "class_name": n.class_name, "function": n.function,
            "lineno": n.lineno, "params": n.params, "lines": n.lines,
            "docstring": n.docstring or "", "is_async": n.is_async,
            "decorators": n.decorators, "type": n.type,
            "in_degree": n.in_degree, "out_degree": n.out_degree,
            "diff_status": status,
        }

    for n in new_g.nodes:
        all_node_ids.add(n.id)
        seen_ids.add(n.id)
        if n.id in added_set:
            status = "added"
        elif n.id in modified_set:
            status = "modified"
        else:
            status = "unchanged"
        graph_nodes.append(_node_dict(n, status))

    for n in old_g.nodes:
        if n.id not in seen_ids and n.id in removed_set:
            all_node_ids.add(n.id)
            seen_ids.add(n.id)
            graph_nodes.append(_node_dict(n, "removed"))

    # Union of edges
    seen_edge_keys: set[str] = set()
    graph_links: list[dict] = []

    for s, t, order in new_g.edges:
        key = f"{s}->{t}"
        if key in seen_edge_keys or s not in all_node_ids or t not in all_node_ids:
            continue
        seen_edge_keys.add(key)
        status = "added" if (s, t) in added_edges_set else "unchanged"
        graph_links.append({"source": s, "target": t, "order": order, "diff_status": status})

    for s, t, order in old_g.edges:
        key = f"{s}->{t}"
        if key in seen_edge_keys or s not in all_node_ids or t not in all_node_ids:
            continue
        seen_edge_keys.add(key)
        if (s, t) in removed_edges_set:
            graph_links.append({"source": s, "target": t, "order": order, "diff_status": "removed"})

    # Sidebar lists
    def _item(nid: str, tag: str, cls: str) -> str:
        nd = next((n for n in graph_nodes if n["id"] == nid), None)
        if not nd:
            return ""
        name = html_mod.escape(nd["short_name"])
        fpath = html_mod.escape(nd["file"])
        return (
            f'<div class="cp-item" data-id="{html_mod.escape(nid)}">'
            f'<span class="diff-tag {cls}">{tag}</span> '
            f'<span class="cp-name">{name}</span> '
            f'<span class="cp-degree">{fpath}</span></div>'
        )

    added_list = "\n".join(_item(nid, "NEW", "tag-added") for nid in diff.added_nodes)
    removed_list = "\n".join(_item(nid, "DEL", "tag-removed") for nid in diff.removed_nodes)
    modified_list = "\n".join(_item(nid, "MOD", "tag-modified") for nid in diff.modified_nodes)

    sm = diff.summary
    graph_data = {
        "nodes": graph_nodes, "links": graph_links, "summary": sm,
        "commit_a": diff.commit_a, "commit_b": diff.commit_b,
    }
    json_data = json.dumps(graph_data, separators=(",", ":"))

    html_content = _TEMPLATE.format(
        json_data=json_data,
        commit_a=html_mod.escape(diff.commit_a),
        commit_b=html_mod.escape(diff.commit_b),
        added_count=sm["added"], removed_count=sm["removed"],
        modified_count=sm["modified"],
        edges_added_count=sm["edges_added"], edges_removed_count=sm["edges_removed"],
        added_list=added_list, removed_list=removed_list, modified_list=modified_list,
        total_nodes=len(graph_nodes), total_edges=len(graph_links),
    )

    Path(output_path).write_text(html_content, encoding="utf-8")
    return len(graph_nodes)


# ── HTML template ────────────────────────────────────────────────────────────

_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CodeGraph Diff — {commit_a} → {commit_b}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;overflow:hidden;height:100vh;width:100vw}}
#app{{display:flex;height:100vh}}
#sidebar{{width:340px;min-width:340px;background:#161b22;border-right:1px solid #30363d;display:flex;flex-direction:column;overflow:hidden}}
#sidebar h1{{font-size:15px;padding:14px 16px;border-bottom:1px solid #30363d;color:#f0f6fc}}
#sidebar h1 .ver{{color:#f0883e;font-size:11px;margin-left:6px}}
.section{{padding:10px 16px;border-bottom:1px solid #30363d;font-size:13px}}
.stat{{display:flex;justify-content:space-between;margin:3px 0}}
.stat-val{{font-weight:600}}
.stat-added{{color:#3fb950}} .stat-removed{{color:#f85149}} .stat-modified{{color:#d29922}} .stat-default{{color:#58a6ff}}
.controls label{{font-size:12px;color:#8b949e;display:block;margin-bottom:2px}}
.controls input[type=text]{{width:100%;padding:6px 10px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:13px;outline:none;margin-bottom:8px}}
.controls input[type=text]:focus{{border-color:#58a6ff}}
.controls input[type=range]{{width:100%;accent-color:#58a6ff}}
.tval{{font-size:11px;color:#58a6ff;float:right}}
.slider-help{{font-size:10px;color:#484f58;margin-bottom:6px}}
.tabs{{display:flex;border-bottom:1px solid #30363d}}
.tab{{flex:1;padding:8px;text-align:center;font-size:12px;cursor:pointer;color:#8b949e;border-bottom:2px solid transparent;transition:all 0.15s}}
.tab:hover{{color:#c9d1d9}} .tab.active{{color:#58a6ff;border-bottom-color:#58a6ff}}
.tab-panel{{flex:1;overflow-y:auto;padding:8px 16px;display:none}} .tab-panel.active{{display:block}}
.tab-panel h2{{font-size:12px;color:#8b949e;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px}}
.cp-item{{padding:5px 8px;border-radius:4px;cursor:pointer;font-size:12px;margin-bottom:2px;display:flex;align-items:center;gap:6px;transition:background 0.15s}}
.cp-item:hover{{background:#21262d}}
.cp-name{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cp-degree{{color:#484f58;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:140px}}
.diff-tag{{display:inline-block;padding:1px 5px;border-radius:3px;font-size:9px;font-weight:700;min-width:30px;text-align:center}}
.tag-added{{background:rgba(63,185,80,0.2);color:#3fb950}}
.tag-removed{{background:rgba(248,81,73,0.2);color:#f85149}}
.tag-modified{{background:rgba(210,153,34,0.2);color:#d29922}}
#graph-container{{flex:1;position:relative;overflow:hidden}}
svg{{width:100%;height:100%}}
.tooltip{{position:absolute;pointer-events:none;background:#1c2128;border:1px solid #30363d;border-radius:8px;padding:12px;font-size:12px;line-height:1.5;max-width:350px;box-shadow:0 8px 24px rgba(0,0,0,0.4);display:none;z-index:100}}
.tooltip .tt-name{{color:#f0f6fc;font-weight:600;font-size:14px}}
.tooltip .tt-file{{color:#58a6ff}} .tooltip .tt-meta{{color:#8b949e}}
.tooltip .tt-doc{{color:#7ee787;font-style:italic;margin-top:4px}}
.tt-diff-tag{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;margin-top:6px}}
.tt-diff-added{{background:rgba(63,185,80,0.2);color:#3fb950}}
.tt-diff-removed{{background:rgba(248,81,73,0.2);color:#f85149}}
.tt-diff-modified{{background:rgba(210,153,34,0.2);color:#d29922}}
.legend{{position:absolute;bottom:16px;right:16px;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 14px;font-size:11px}}
.legend-item{{display:flex;align-items:center;gap:8px;margin:3px 0;cursor:pointer;padding:2px 4px;border-radius:4px;transition:background 0.15s}}
.legend-item:hover{{background:#21262d}} .legend-item.active{{background:#1c2128;outline:1px solid #58a6ff}}
.legend-dot{{width:12px;height:12px;border-radius:50%;flex-shrink:0}}
.legend-line{{width:24px;height:3px;border-radius:2px;flex-shrink:0}}
.legend-dash{{width:24px;height:0;border-top:3px dashed;flex-shrink:0}}
.legend-sep{{margin-top:6px;padding-top:6px;border-top:1px solid #30363d}}
</style></head><body>
<div id="app">
<div id="sidebar">
  <h1>CodeGraph <span style="color:#58a6ff">Diff</span><span class="ver">{commit_a} → {commit_b}</span></h1>
  <div class="section" id="stats">
    <div class="stat"><span>Total nodes</span><span class="stat-default">{total_nodes}</span></div>
    <div class="stat"><span>Total edges</span><span class="stat-default">{total_edges}</span></div>
    <div class="stat"><span>Added functions</span><span class="stat-val stat-added">+{added_count}</span></div>
    <div class="stat"><span>Removed functions</span><span class="stat-val stat-removed">-{removed_count}</span></div>
    <div class="stat"><span>Modified functions</span><span class="stat-val stat-modified">~{modified_count}</span></div>
    <div class="stat"><span>Edges added</span><span class="stat-val stat-added">+{edges_added_count}</span></div>
    <div class="stat"><span>Edges removed</span><span class="stat-val stat-removed">-{edges_removed_count}</span></div>
  </div>
  <div class="section controls">
    <input type="text" id="search" placeholder="Search functions..." autocomplete="off">
    <label>Min in-degree: <span class="tval" id="in-val">0</span></label>
    <input type="range" id="in-slider" min="0" max="20" value="0">
    <div class="slider-help">0 = show all changed nodes regardless of connectivity</div>
    <label>Min out-degree: <span class="tval" id="out-val">0</span></label>
    <input type="range" id="out-slider" min="0" max="30" value="0">
  </div>
  <div class="tabs">
    <div class="tab active" data-tab="added">Added ({added_count})</div>
    <div class="tab" data-tab="removed">Removed ({removed_count})</div>
    <div class="tab" data-tab="modified">Modified ({modified_count})</div>
  </div>
  <div class="tab-panel active" id="panel-added"><h2>New functions</h2><div id="list-added">{added_list}</div></div>
  <div class="tab-panel" id="panel-removed"><h2>Removed functions</h2><div id="list-removed">{removed_list}</div></div>
  <div class="tab-panel" id="panel-modified"><h2>Modified functions</h2><div id="list-modified">{modified_list}</div></div>
</div>
<div id="graph-container">
  <svg id="graph"></svg>
  <div class="tooltip" id="tooltip"></div>
  <div class="legend" id="legend">
    <div class="legend-item" data-filter="added"><div class="legend-dot" style="background:#3fb950"></div>Added (NEW)</div>
    <div class="legend-item" data-filter="removed"><div class="legend-dot" style="background:#f85149"></div>Removed (DEL)</div>
    <div class="legend-item" data-filter="modified"><div class="legend-dot" style="background:#d29922"></div>Modified (MOD)</div>
    <div class="legend-item" data-filter="unchanged"><div class="legend-dot" style="background:#30363d"></div>Unchanged</div>
    <div class="legend-sep">
      <div class="legend-item" style="cursor:default"><div class="legend-line" style="background:#3fb950"></div>Edge added</div>
      <div class="legend-item" style="cursor:default"><div class="legend-dash" style="border-color:#f85149"></div>Edge removed</div>
      <div class="legend-item" style="cursor:default"><div class="legend-line" style="background:#30363d"></div>Edge unchanged</div>
    </div>
  </div>
</div>
</div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const DATA={json_data};
const DCOL={{added:"#3fb950",removed:"#f85149",modified:"#d29922",unchanged:"#30363d"}};
const DCOL_BRIGHT={{added:"#56d364",removed:"#ff7b72",modified:"#e3b341",unchanged:"#484f58"}};
const ECOL={{added:"#3fb950",removed:"#f85149",unchanged:"#21262d"}};
let sim,svg,g,linkG,nodeG,labelG;
let cNodes=[],cLinks=[];
let locked=null,legendFilter=null;
let fwd={{}},bwd={{}};

function buildAdj(){{fwd={{}};bwd={{}};cLinks.forEach(l=>{{const s=l.source?.id||l.source,t=l.target?.id||l.target;if(!fwd[s])fwd[s]=[];fwd[s].push(t);if(!bwd[t])bwd[t]=[];bwd[t].push(s);}});}}
function bfs(start,adj,depth){{const v=new Set(),q=[[start,0]];v.add(start);while(q.length){{const[n,d]=q.shift();if(d>=depth)continue;(adj[n]||[]).forEach(nb=>{{if(!v.has(nb)){{v.add(nb);q.push([nb,d+1]);}}}});}};v.delete(start);return v;}}

function computeFlowDepths(){{
  const depths={{}};const queue=[];
  cNodes.forEach(n=>{{if(n.in_degree===0&&n.out_degree>0){{depths[n.id]=0;queue.push(n.id);}}}});
  let head=0;
  while(head<queue.length){{const nid=queue[head++];(fwd[nid]||[]).forEach(t=>{{if(!(t in depths)){{depths[t]=depths[nid]+1;queue.push(t);}}}});}}
  const maxD=Math.max(...Object.values(depths),1);
  cNodes.forEach(n=>{{
    if(n.id in depths)n.depth=depths[n.id];
    else if(n.out_degree===0&&n.in_degree>0)n.depth=maxD;
    else if(n.in_degree===0&&n.out_degree===0)n.depth=maxD+1;
    else n.depth=Math.round(maxD/2);
  }});
  return maxD;
}}

function init(){{
  svg=d3.select("#graph");g=svg.append("g");
  linkG=g.append("g");nodeG=g.append("g");labelG=g.append("g");
  const defs=svg.append("defs");
  ["added","removed","unchanged"].forEach(s=>{{
    defs.append("marker").attr("id","arr-"+s).attr("viewBox","0 -3 6 6").attr("refX",14).attr("refY",0)
      .attr("markerWidth",5).attr("markerHeight",5).attr("orient","auto")
      .append("path").attr("d","M0,-3L6,0L0,3").attr("fill",ECOL[s]);
  }});
  svg.call(d3.zoom().scaleExtent([0.05,10]).on("zoom",e=>g.attr("transform",e.transform)));
  svg.on("click",function(e){{if(e.target===this||e.target.tagName==="svg")reset();}});
  document.getElementById("search").addEventListener("input",debounce(applyFilter,300));
  ["in-slider","out-slider"].forEach(id=>document.getElementById(id).addEventListener("input",()=>{{
    document.getElementById("in-val").textContent=document.getElementById("in-slider").value;
    document.getElementById("out-val").textContent=document.getElementById("out-slider").value;
    applyFilter();
  }}));
  document.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>{{
    document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(x=>x.classList.remove("active"));
    t.classList.add("active");document.getElementById("panel-"+t.dataset.tab).classList.add("active");
  }}));
  document.querySelectorAll(".cp-item").forEach(el=>el.addEventListener("click",()=>{{
    const nd=cNodes.find(n=>n.id===el.dataset.id);if(nd)lockNode(nd);
  }}));
  document.querySelectorAll(".legend-item[data-filter]").forEach(el=>el.addEventListener("click",()=>{{
    const f=el.dataset.filter;
    if(legendFilter===f){{legendFilter=null;el.classList.remove("active");resetVisuals();return;}}
    legendFilter=f;locked=null;
    document.querySelectorAll(".legend-item[data-filter]").forEach(x=>x.classList.remove("active"));
    el.classList.add("active");highlightByStatus(f);
  }}));
  applyFilter();
}}

function highlightByStatus(status){{
  const matchIds=new Set();cNodes.forEach(n=>{{if(n.diff_status===status)matchIds.add(n.id);}});
  nodeG.selectAll(".node").attr("opacity",d=>matchIds.has(d.id)?1:0.06);
  labelG.selectAll("text").attr("opacity",d=>matchIds.has(d.id)?1:0.06);
  linkG.selectAll("line.edge").each(function(l){{
    const s=l.source?.id||l.source,t=l.target?.id||l.target;
    const related=matchIds.has(s)||matchIds.has(t);
    d3.select(this).attr("opacity",related?1:0.03).attr("stroke-width",related?2:0.2);
  }});
}}

function applyFilter(){{
  const mI=+document.getElementById("in-slider").value,mO=+document.getElementById("out-slider").value;
  const q=document.getElementById("search").value.toLowerCase();
  const keep=new Set();
  DATA.nodes.forEach(n=>{{
    if(n.diff_status!=="unchanged")keep.add(n.id);
    else if(n.in_degree>=mI||n.out_degree>=mO)keep.add(n.id);
  }});
  const changed=new Set(DATA.nodes.filter(n=>n.diff_status!=="unchanged").map(n=>n.id));
  DATA.links.forEach(l=>{{
    const s=l.source?.id||l.source,t=l.target?.id||l.target;
    if(changed.has(s))keep.add(t);if(changed.has(t))keep.add(s);
  }});
  cNodes=DATA.nodes.filter(n=>keep.has(n.id));
  cLinks=DATA.links.filter(l=>keep.has(l.source?.id||l.source)&&keep.has(l.target?.id||l.target));
  if(q){{const m=new Set();
    cNodes.forEach(n=>{{if(n.short_name.toLowerCase().includes(q)||n.file.toLowerCase().includes(q))m.add(n.id);}});
    cLinks.forEach(l=>{{const s=l.source?.id||l.source,t=l.target?.id||l.target;if(m.has(s))m.add(t);if(m.has(t))m.add(s);}});
    cNodes=cNodes.filter(n=>m.has(n.id));cLinks=cLinks.filter(l=>m.has(l.source?.id||l.source)&&m.has(l.target?.id||l.target));
  }}
  locked=null;legendFilter=null;
  document.querySelectorAll(".legend-item[data-filter]").forEach(x=>x.classList.remove("active"));
  buildAdj();render();
}}

function nodeColor(d){{return DCOL[d.diff_status]||DCOL.unchanged;}}
function nodeR(d){{const base=d.diff_status==="unchanged"?3:6;return Math.max(base,Math.sqrt(d.in_degree+d.out_degree)*2.5);}}

function render(){{
  const c=document.getElementById("graph-container"),W=c.clientWidth,H=c.clientHeight;
  if(sim)sim.stop();
  const maxD=computeFlowDepths();const totalD=maxD+2;const pad=80;

  sim=d3.forceSimulation(cNodes)
    .force("link",d3.forceLink(cLinks).id(d=>d.id).distance(40).strength(0.04))
    .force("charge",d3.forceManyBody().strength(-25).distanceMax(180))
    .force("x",d3.forceX(d=>pad+(d.depth/totalD)*(W-2*pad)).strength(0.8))
    .force("y",d3.forceY(d=>{{
      const ln=cNodes.filter(n=>n.depth===d.depth),idx=ln.indexOf(d),cnt=ln.length;
      if(cnt<=1)return H/2;return pad+((idx/(cnt-1))*(H-2*pad));
    }}).strength(0.12))
    .force("collision",d3.forceCollide().radius(d=>nodeR(d)+4).strength(0.7))
    .alphaDecay(0.025).velocityDecay(0.5);

  const lk=linkG.selectAll("line.edge").data(cLinks,d=>(d.source?.id||d.source)+"-"+(d.target?.id||d.target));
  lk.exit().remove();const lkE=lk.enter().append("line").attr("class","edge");const lkM=lkE.merge(lk);
  lkM.each(function(d){{const el=d3.select(this),s=d.diff_status||"unchanged";
    el.attr("stroke",ECOL[s]).attr("stroke-width",s==="unchanged"?0.6:1.8)
      .attr("marker-end","url(#arr-"+s+")").attr("stroke-dasharray",s==="removed"?"5,3":null)
      .attr("opacity",s==="unchanged"?0.25:0.85);
  }});

  const nd=nodeG.selectAll(".node").data(cNodes,d=>d.id);nd.exit().remove();
  const ndE=nd.enter().append("circle").attr("class","node").style("cursor","pointer")
    .on("mouseover",showTip).on("mouseout",hideTip)
    .on("click",(e,d)=>{{e.stopPropagation();lockNode(d);}})
    .call(d3.drag().on("start",ds).on("drag",dg).on("end",de));
  const ndM=ndE.merge(nd);
  ndM.each(function(d){{const el=d3.select(this),r=nodeR(d),col=nodeColor(d);
    el.attr("r",r).attr("fill",col);
    if(d.diff_status==="removed")el.attr("fill","none").attr("stroke",col).attr("stroke-width",2).attr("stroke-dasharray","4,2").attr("opacity",0.7);
    else if(d.diff_status==="unchanged")el.attr("opacity",0.25).attr("stroke","none");
    else el.attr("stroke","#0d1117").attr("stroke-width",1.5).attr("opacity",0.9);
  }});

  const changedNodes=cNodes.filter(d=>d.diff_status!=="unchanged");
  const lb=labelG.selectAll("text.nlabel").data(changedNodes,d=>d.id);lb.exit().remove();
  const lbE=lb.enter().append("text").attr("class","nlabel")
    .text(d=>d.short_name.length>28?d.short_name.slice(0,25)+"...":d.short_name)
    .attr("font-size",9).attr("fill",d=>DCOL_BRIGHT[d.diff_status]||"#8b949e")
    .attr("text-anchor","middle").attr("dy",d=>-nodeR(d)-5).attr("pointer-events","none");
  const lbM=lbE.merge(lb);

  sim.on("tick",()=>{{
    lkM.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
    ndM.attr("cx",d=>d.x).attr("cy",d=>d.y);
    lbM.attr("x",d=>d.x).attr("y",d=>d.y);
  }});
}}

function showTip(event,d){{
  const tt=document.getElementById("tooltip");
  let tag="";
  if(d.diff_status==="added")tag='<div class="tt-diff-tag tt-diff-added">NEW — added in {commit_b}</div>';
  else if(d.diff_status==="removed")tag='<div class="tt-diff-tag tt-diff-removed">REMOVED — was in {commit_a}</div>';
  else if(d.diff_status==="modified")tag='<div class="tt-diff-tag tt-diff-modified">MODIFIED — body changed</div>';
  tt.innerHTML=`<div class="tt-name">${{d.short_name}}${{d.is_async?" async":""}}</div>
    <div class="tt-file">${{d.file}}:${{d.lineno}}</div>
    <div class="tt-meta">in:${{d.in_degree}} out:${{d.out_degree}} | ${{d.lines}} lines</div>
    ${{d.docstring?'<div class="tt-doc">'+d.docstring+'</div>':''}}${{tag}}`;
  tt.style.display="block";tt.style.left=(event.pageX-340+15)+"px";tt.style.top=(event.pageY+15)+"px";
}}
function hideTip(){{document.getElementById("tooltip").style.display="none";}}

function lockNode(d){{
  if(locked===d.id){{reset();return;}}locked=d.id;legendFilter=null;
  document.querySelectorAll(".legend-item[data-filter]").forEach(x=>x.classList.remove("active"));
  const down=bfs(d.id,fwd,4),up=bfs(d.id,bwd,4),all=new Set([d.id,...down,...up]);
  nodeG.selectAll(".node").attr("opacity",nd=>{{if(nd.id===d.id)return 1;if(all.has(nd.id))return 0.7;return 0.05;}});
  labelG.selectAll("text").attr("opacity",nd=>all.has(nd.id)?1:0.05);
  linkG.selectAll("line.edge").each(function(l){{
    const s=l.source?.id||l.source,t=l.target?.id||l.target,el=d3.select(this);
    if(s===d.id||t===d.id)el.attr("stroke-width",3).attr("opacity",1);
    else if(all.has(s)&&all.has(t))el.attr("opacity",0.5);
    else el.attr("opacity",0.03);
  }});
}}

function reset(){{locked=null;legendFilter=null;
  document.querySelectorAll(".legend-item[data-filter]").forEach(x=>x.classList.remove("active"));resetVisuals();}}
function resetVisuals(){{
  nodeG.selectAll(".node").each(function(d){{const el=d3.select(this);
    if(d.diff_status==="removed")el.attr("opacity",0.7);
    else if(d.diff_status==="unchanged")el.attr("opacity",0.25);
    else el.attr("opacity",0.9);
  }});
  labelG.selectAll("text").attr("opacity",1);
  linkG.selectAll("line.edge").each(function(d){{const s=d.diff_status||"unchanged";
    d3.select(this).attr("stroke-width",s==="unchanged"?0.6:1.8).attr("opacity",s==="unchanged"?0.25:0.85);
  }});
}}
function debounce(fn,ms){{let t;return function(){{clearTimeout(t);t=setTimeout(fn,ms);}};}}
function ds(e,d){{if(!e.active)sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y;}}
function dg(e,d){{d.fx=e.x;d.fy=e.y;}}
function de(e,d){{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}}
init();
</script></body></html>"""
