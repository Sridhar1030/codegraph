"""End-to-end integration tests — full pipeline from scan to store queries.

Exercises the scan → resolve → store → query pipeline as a single unit,
verifying that data flows correctly through the entire system.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from codegraph.scanner import scan_codebase
from codegraph.resolver import build_graph
from codegraph.store import NetworkXStore


def _build_store(repo_path: Path, **scan_kwargs) -> NetworkXStore:
    scan = scan_codebase(str(repo_path), **scan_kwargs)
    graph = build_graph(scan)
    store = NetworkXStore()
    store.load(graph)
    return store


# ═══════════════════════════════════════════════════════════════════════════════
#  Full pipeline: plain Python repo
# ═══════════════════════════════════════════════════════════════════════════════


class TestPlainPipeline:
    """Scan → resolve → store → query for a standard Python project."""

    def test_nodes_queryable(self, simple_repo):
        store = _build_store(simple_repo)
        assert store.search("greet") != []

    def test_edges_traversable(self, simple_repo):
        store = _build_store(simple_repo)
        sub = store.get_neighbors("app.py:greet", direction="out")
        target_ids = {n["id"] for n in sub.nodes}
        assert "app.py:format_greeting" in target_ids

    def test_impact_analysis_works(self, simple_repo):
        store = _build_store(simple_repo)
        impact = store.get_impact("app.py:format_greeting")
        assert impact is not None
        assert impact.upstream_count >= 1

    def test_flowchart_tree(self, simple_repo):
        store = _build_store(simple_repo)
        tree = store.get_call_chain("app.py:greet")
        assert tree is not None
        assert any(c.name == "format_greeting" for c in tree.children)

    def test_persistence_roundtrip(self, simple_repo, tmp_path):
        store = _build_store(simple_repo)
        path = str(tmp_path / "graph.pkl")
        store.save(path)

        restored = NetworkXStore()
        assert restored.load_from_disk(path) is True
        assert restored.stats()["total_nodes"] == store.stats()["total_nodes"]
        assert restored.search("greet") != []


# ═══════════════════════════════════════════════════════════════════════════════
#  Full pipeline: KFP repo
# ═══════════════════════════════════════════════════════════════════════════════


class TestKFPPipeline:
    """Scan → resolve → store → query for a KFP pipeline project."""

    def test_kfp_pipelines_discoverable(self, kfp_repo):
        store = _build_store(kfp_repo)
        pipelines = store.get_kfp_pipelines()
        assert len(pipelines) == 1
        assert pipelines[0]["name"] == "ml_pipeline"

    def test_pipeline_subgraph_has_all_components(self, kfp_repo):
        store = _build_store(kfp_repo)
        pid = store.get_kfp_pipelines()[0]["pipeline_id"]
        sub = store.get_kfp_pipeline_subgraph(pid)
        names = {n["function"] for n in sub.nodes}
        assert {"load_data", "preprocess", "train_model", "evaluate", "ml_pipeline"} == names

    def test_data_edges_in_pipeline(self, kfp_repo):
        store = _build_store(kfp_repo)
        pid = store.get_kfp_pipelines()[0]["pipeline_id"]
        sub = store.get_kfp_pipeline_subgraph(pid)
        data_edges = [e for e in sub.edges if "data_dependency" in e.get("type", "")]
        assert len(data_edges) == 4

    def test_component_search_by_type(self, kfp_repo):
        store = _build_store(kfp_repo)
        results = store.search(node_type="kfp_component")
        assert len(results) == 4

    def test_stats_reflect_kfp(self, kfp_repo):
        store = _build_store(kfp_repo)
        s = store.stats()
        assert s["kfp_components"] == 4
        assert s["kfp_pipelines"] == 1
        assert s["kfp_data_edges"] == 4


# ═══════════════════════════════════════════════════════════════════════════════
#  Full pipeline: multi-file cross-references
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossFileResolution:
    """Verifies that cross-file call resolution works end-to-end."""

    def test_cross_file_edge_exists(self, multi_file_repo):
        store = _build_store(multi_file_repo)
        sub = store.get_neighbors("service.py:create_user", direction="out")
        callee_names = {n.get("function") for n in sub.nodes}
        assert "validate" in callee_names

    def test_file_list_complete(self, multi_file_repo):
        store = _build_store(multi_file_repo)
        files = {f["file"] for f in store.get_files()}
        assert {"models.py", "service.py", "helpers.py"}.issubset(files)


# ═══════════════════════════════════════════════════════════════════════════════
#  Edge case: empty and malformed repos
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:

    def test_empty_directory(self, tmp_path):
        store = _build_store(tmp_path)
        assert store.is_loaded() is False
        assert store.stats() == {"loaded": False}

    def test_only_syntax_errors(self, tmp_path):
        (tmp_path / "bad.py").write_text("def broken(:\n")
        store = _build_store(tmp_path)
        assert store.is_loaded() is False

    def test_exclude_patterns_applied(self, tmp_path):
        (tmp_path / "main.py").write_text("def keep(): pass\n")
        (tmp_path / "gen_pb2.py").write_text("def skip(): pass\n")
        store = _build_store(tmp_path, exclude_patterns=["*_pb2*"])
        names = {r["function"] for r in store.search(include_generated=True)}
        assert "keep" in names
        assert "skip" not in names

    def test_large_number_of_functions(self, tmp_path):
        """Stress test: 200 functions with call chains."""
        lines = ["def f0(): pass"]
        for i in range(1, 200):
            lines.append(f"def f{i}(): f{i-1}()")
        (tmp_path / "big.py").write_text("\n".join(lines) + "\n")
        store = _build_store(tmp_path)
        assert store.stats()["total_nodes"] == 200
        assert store.stats()["total_edges"] >= 199
