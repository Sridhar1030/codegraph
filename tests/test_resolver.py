"""Tests for codegraph.resolver — call resolution, KFP edges, and pipeline metadata."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from codegraph.models import CodeGraph
from codegraph.scanner import scan_codebase
from codegraph.resolver import build_graph, filter_graph


# ═══════════════════════════════════════════════════════════════════════════════
#  Call resolution
# ═══════════════════════════════════════════════════════════════════════════════


class TestBasicResolution:

    def test_direct_call_resolved(self, simple_graph: CodeGraph):
        assert any(
            e.source == "app.py:greet" and e.target == "app.py:format_greeting"
            for e in simple_graph.edges
        )

    def test_self_call_resolved(self, simple_graph: CodeGraph):
        assert any(
            e.source == "app.py:Calculator.multiply" and e.target == "app.py:Calculator.add"
            for e in simple_graph.edges
        )

    def test_self_recursive_excluded(self, tmp_path: Path):
        """A function calling itself should NOT produce an edge (cycle avoidance)."""
        (tmp_path / "r.py").write_text("def recurse(n):\n    recurse(n-1)\n")
        graph = build_graph(scan_codebase(str(tmp_path)))
        assert not any(e.source == e.target for e in graph.edges)

    def test_builtin_calls_not_resolved(self, simple_graph: CodeGraph):
        assert not any(e.target.endswith(":print") for e in simple_graph.edges)
        assert not any(e.target.endswith(":len") for e in simple_graph.edges)


class TestDegreeCalculation:

    def test_caller_has_nonzero_out_degree(self, simple_graph: CodeGraph):
        greet = simple_graph.node_by_id("app.py:greet")
        assert greet is not None and greet.out_degree > 0

    def test_callee_has_nonzero_in_degree(self, simple_graph: CodeGraph):
        fmt = simple_graph.node_by_id("app.py:format_greeting")
        assert fmt is not None and fmt.in_degree > 0

    def test_isolated_node_zero_degree(self, simple_graph: CodeGraph):
        unused = simple_graph.node_by_id("util.py:unused")
        assert unused is not None
        assert unused.in_degree == 0 and unused.out_degree == 0


class TestGraphStats:

    @pytest.mark.parametrize("key", [
        "total_functions", "total_edges_resolved", "external_packages",
        "files_scanned", "dispatch_nodes", "option_edges", "dynamic_edges",
        "kfp_components", "kfp_pipelines", "kfp_data_edges",
    ])
    def test_required_stat_keys(self, simple_graph: CodeGraph, key):
        assert key in simple_graph.stats

    def test_kfp_stats_zero_for_plain_repo(self, simple_graph: CodeGraph):
        assert simple_graph.stats["kfp_components"] == 0
        assert simple_graph.stats["kfp_pipelines"] == 0
        assert simple_graph.stats["kfp_data_edges"] == 0

    def test_kfp_stats_populated(self, kfp_graph: CodeGraph):
        assert kfp_graph.stats["kfp_components"] == 4
        assert kfp_graph.stats["kfp_pipelines"] == 1
        assert kfp_graph.stats["kfp_data_edges"] == 4


# ═══════════════════════════════════════════════════════════════════════════════
#  KFP edge resolution
# ═══════════════════════════════════════════════════════════════════════════════


class TestKFPDataEdges:

    def _data_edges(self, graph):
        return [e for e in graph.edges if e.edge_type.startswith("data_dependency")]

    def test_edge_count(self, kfp_graph: CodeGraph):
        assert len(self._data_edges(kfp_graph)) == 4

    @pytest.mark.parametrize("src,tgt,label_fragment", [
        ("pipeline.py:load_data", "pipeline.py:preprocess", "output -> raw"),
        ("pipeline.py:preprocess", "pipeline.py:train_model", "output -> data"),
        ("pipeline.py:preprocess", "pipeline.py:evaluate", "output -> test_data"),
        ("pipeline.py:train_model", "pipeline.py:evaluate", "output -> model"),
    ])
    def test_specific_edge(self, kfp_graph, src, tgt, label_fragment):
        match = [
            e for e in kfp_graph.edges
            if e.source == src and e.target == tgt
            and label_fragment in e.edge_type
        ]
        assert len(match) == 1, f"Expected edge {src} -> {tgt} ({label_fragment})"

    def test_no_data_edges_in_plain_repo(self, simple_graph: CodeGraph):
        assert len(self._data_edges(simple_graph)) == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  KFP pipeline metadata
# ═══════════════════════════════════════════════════════════════════════════════


class TestKFPPipelineMeta:

    def test_meta_populated(self, kfp_graph: CodeGraph):
        assert len(kfp_graph.kfp_pipelines_meta) == 1

    def test_meta_fields(self, kfp_graph: CodeGraph):
        meta = next(iter(kfp_graph.kfp_pipelines_meta.values()))
        for key in ("pipeline_id", "pipeline_node_id", "name", "file",
                     "lineno", "component_node_ids", "all_node_ids",
                     "edge_count", "task_count"):
            assert key in meta, f"Missing key: {key}"

    def test_meta_component_count(self, kfp_graph: CodeGraph):
        meta = next(iter(kfp_graph.kfp_pipelines_meta.values()))
        assert len(meta["component_node_ids"]) == 4

    def test_meta_task_count(self, kfp_graph: CodeGraph):
        meta = next(iter(kfp_graph.kfp_pipelines_meta.values()))
        assert meta["task_count"] == 4


# ═══════════════════════════════════════════════════════════════════════════════
#  filter_graph
# ═══════════════════════════════════════════════════════════════════════════════


class TestFilterGraph:

    def test_isolates_removed(self, simple_graph: CodeGraph):
        filtered = filter_graph(simple_graph, min_in=1, min_out=1)
        ids = {n.id for n in filtered.nodes}
        assert "util.py:unused" not in ids

    def test_connected_nodes_kept(self, simple_graph: CodeGraph):
        filtered = filter_graph(simple_graph, min_in=1, min_out=0)
        ids = {n.id for n in filtered.nodes}
        assert "app.py:format_greeting" in ids

    def test_edges_pruned_with_nodes(self, simple_graph: CodeGraph):
        filtered = filter_graph(simple_graph, min_in=1, min_out=1)
        surviving_ids = {n.id for n in filtered.nodes}
        for e in filtered.edges:
            assert e.source in surviving_ids and e.target in surviving_ids

    def test_stats_preserved(self, simple_graph: CodeGraph):
        filtered = filter_graph(simple_graph, min_in=0, min_out=0)
        assert filtered.stats == simple_graph.stats


class TestExternalPackageResolution:

    def test_known_external_detected(self, tmp_path: Path):
        (tmp_path / "a.py").write_text(textwrap.dedent("""\
            import pandas
            def process():
                df = pandas.DataFrame()
        """))
        graph = build_graph(scan_codebase(str(tmp_path)))
        ext_nodes = [n for n in graph.nodes if n.node_type == "external"]
        assert any(n.function == "pandas" for n in ext_nodes)
