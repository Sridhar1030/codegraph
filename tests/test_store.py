"""Tests for codegraph.store — NetworkXStore operations, search, graph traversal, and KFP queries."""

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.models import CodeGraph
from codegraph.scanner import scan_codebase
from codegraph.resolver import build_graph
from codegraph.store import NetworkXStore, SubGraph


# ═══════════════════════════════════════════════════════════════════════════════
#  Basic operations
# ═══════════════════════════════════════════════════════════════════════════════


class TestStoreLifecycle:

    def test_empty_store_not_loaded(self):
        assert NetworkXStore().is_loaded() is False

    def test_loaded_after_load(self, simple_graph):
        store = NetworkXStore()
        store.load(simple_graph)
        assert store.is_loaded() is True

    def test_stats_when_not_loaded(self):
        assert NetworkXStore().stats() == {"loaded": False}

    def test_stats_keys_after_load(self, simple_graph):
        store = NetworkXStore()
        store.load(simple_graph)
        s = store.stats()
        assert s["loaded"] is True
        assert s["total_nodes"] > 0
        assert s["total_edges"] >= 0
        assert "node_types" in s
        assert "edge_types" in s


class TestGetNode:

    def test_existing_node(self, kfp_store: NetworkXStore):
        node = kfp_store.get_node("pipeline.py:load_data")
        assert node is not None
        assert node["function"] == "load_data"
        assert "calls" in node
        assert "called_by" in node

    def test_missing_node(self, kfp_store: NetworkXStore):
        assert kfp_store.get_node("nonexistent:func") is None

    def test_node_on_unloaded_store(self):
        assert NetworkXStore().get_node("any:id") is None


# ═══════════════════════════════════════════════════════════════════════════════
#  Search
# ═══════════════════════════════════════════════════════════════════════════════


class TestSearch:

    def test_search_by_name(self, kfp_store: NetworkXStore):
        results = kfp_store.search("load_data")
        assert len(results) >= 1
        assert results[0]["function"] == "load_data"

    def test_search_by_file(self, kfp_store: NetworkXStore):
        results = kfp_store.search(file="pipeline.py")
        assert all(r["file"] == "pipeline.py" for r in results)

    def test_search_by_type(self, kfp_store: NetworkXStore):
        results = kfp_store.search(node_type="kfp_component")
        assert len(results) == 4
        assert all(r["type"] == "kfp_component" for r in results)

    def test_search_with_limit(self, kfp_store: NetworkXStore):
        results = kfp_store.search(limit=2)
        assert len(results) <= 2

    def test_search_min_in_degree(self, kfp_store: NetworkXStore):
        results = kfp_store.search(min_in=1)
        assert all(r.get("in_degree", 0) >= 1 for r in results)

    def test_search_empty_query_returns_all(self, kfp_store: NetworkXStore):
        results = kfp_store.search(limit=1000)
        assert len(results) >= 5

    def test_search_case_insensitive(self, kfp_store: NetworkXStore):
        results = kfp_store.search("LOAD_DATA")
        assert len(results) >= 1

    def test_search_no_results(self, kfp_store: NetworkXStore):
        assert kfp_store.search("xyznonexistent") == []

    def test_search_on_empty_store(self):
        assert NetworkXStore().search("any") == []


# ═══════════════════════════════════════════════════════════════════════════════
#  Neighbors
# ═══════════════════════════════════════════════════════════════════════════════


class TestNeighbors:

    def test_outgoing(self, kfp_store: NetworkXStore):
        sub = kfp_store.get_neighbors("pipeline.py:load_data", direction="out")
        assert sub.stats["node_count"] >= 2

    def test_incoming(self, kfp_store: NetworkXStore):
        sub = kfp_store.get_neighbors("pipeline.py:preprocess", direction="in")
        assert sub.stats["node_count"] >= 1

    def test_both_directions(self, kfp_store: NetworkXStore):
        sub = kfp_store.get_neighbors("pipeline.py:preprocess", direction="both")
        assert sub.stats["node_count"] >= 2

    def test_missing_node_returns_empty(self, kfp_store: NetworkXStore):
        sub = kfp_store.get_neighbors("fake:node")
        assert sub.stats == {}

    def test_depth_increases_reach(self, kfp_store: NetworkXStore):
        sub1 = kfp_store.get_neighbors("pipeline.py:load_data", direction="out", depth=1)
        sub2 = kfp_store.get_neighbors("pipeline.py:load_data", direction="out", depth=3)
        assert sub2.stats["node_count"] >= sub1.stats["node_count"]


# ═══════════════════════════════════════════════════════════════════════════════
#  Impact analysis
# ═══════════════════════════════════════════════════════════════════════════════


class TestImpact:

    def test_valid_node(self, kfp_store: NetworkXStore):
        result = kfp_store.get_impact("pipeline.py:load_data")
        assert result is not None
        assert result.focus["function"] == "load_data"
        assert result.downstream_count >= 1

    def test_missing_node(self, kfp_store: NetworkXStore):
        assert kfp_store.get_impact("fake:node") is None


# ═══════════════════════════════════════════════════════════════════════════════
#  Call chain (flowchart)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCallChain:

    def test_tree_structure(self, kfp_store: NetworkXStore):
        tree = kfp_store.get_call_chain("pipeline.py:load_data")
        assert tree is not None
        assert tree.id == "pipeline.py:load_data"
        d = tree.to_dict()
        assert "children" in d

    def test_missing_node(self, kfp_store: NetworkXStore):
        assert kfp_store.get_call_chain("fake:node") is None

    def test_depth_limits_tree(self, kfp_store: NetworkXStore):
        tree = kfp_store.get_call_chain("pipeline.py:load_data", depth=0)
        assert tree.children == []


# ═══════════════════════════════════════════════════════════════════════════════
#  File operations
# ═══════════════════════════════════════════════════════════════════════════════


class TestFiles:

    def test_get_files(self, kfp_store: NetworkXStore):
        files = kfp_store.get_files()
        assert len(files) >= 1
        assert files[0]["file"] == "pipeline.py"
        assert files[0]["node_count"] >= 5

    def test_get_file_nodes(self, kfp_store: NetworkXStore):
        nodes = kfp_store.get_file_nodes("pipeline.py")
        assert len(nodes) >= 5

    def test_get_file_nodes_nonexistent(self, kfp_store: NetworkXStore):
        assert kfp_store.get_file_nodes("nonexistent.py") == []


# ═══════════════════════════════════════════════════════════════════════════════
#  KFP-specific queries
# ═══════════════════════════════════════════════════════════════════════════════


class TestKFPQueries:

    def test_list_pipelines(self, kfp_store: NetworkXStore):
        pipelines = kfp_store.get_kfp_pipelines()
        assert len(pipelines) == 1
        p = pipelines[0]
        assert p["name"] == "ml_pipeline"
        assert p["task_count"] == 4

    def test_no_pipelines_in_plain_store(self, simple_graph):
        store = NetworkXStore()
        store.load(simple_graph)
        assert store.get_kfp_pipelines() == []

    def test_pipeline_subgraph(self, kfp_store: NetworkXStore):
        pipelines = kfp_store.get_kfp_pipelines()
        pid = pipelines[0]["pipeline_id"]
        sub = kfp_store.get_kfp_pipeline_subgraph(pid)
        assert sub is not None
        assert sub.stats["node_count"] == 5
        assert sub.stats["edge_count"] >= 4

    def test_pipeline_subgraph_missing(self, kfp_store: NetworkXStore):
        assert kfp_store.get_kfp_pipeline_subgraph("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════════════════
#  Full graph data
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullGraph:

    def test_returns_everything(self, kfp_store: NetworkXStore):
        full = kfp_store.get_full_graph_data()
        assert full.stats["total_nodes"] >= 5
        assert len(full.nodes) == full.stats["total_nodes"]

    def test_empty_store_returns_empty(self):
        full = NetworkXStore().get_full_graph_data()
        assert full.nodes == [] and full.edges == []


# ═══════════════════════════════════════════════════════════════════════════════
#  Persistence (save / load)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistence:

    def test_round_trip(self, kfp_store: NetworkXStore, tmp_path: Path):
        save_path = str(tmp_path / "graph.pkl")
        kfp_store.save(save_path)

        restored = NetworkXStore()
        assert restored.load_from_disk(save_path) is True
        assert restored.is_loaded() is True
        assert restored.stats()["total_nodes"] == kfp_store.stats()["total_nodes"]

    def test_round_trip_preserves_kfp_meta(self, kfp_store: NetworkXStore, tmp_path: Path):
        """KFP pipeline metadata must survive save/load — regression for _kfp_meta persistence."""
        save_path = str(tmp_path / "graph.pkl")
        pipelines_before = kfp_store.get_kfp_pipelines()
        assert len(pipelines_before) >= 1

        kfp_store.save(save_path)
        restored = NetworkXStore()
        restored.load_from_disk(save_path)

        pipelines_after = restored.get_kfp_pipelines()
        assert len(pipelines_after) == len(pipelines_before)
        assert pipelines_after[0]["name"] == pipelines_before[0]["name"]

        pid = pipelines_after[0]["pipeline_id"]
        sub = restored.get_kfp_pipeline_subgraph(pid)
        assert sub is not None
        assert sub.stats["node_count"] == 5

    def test_load_nonexistent_file(self):
        assert NetworkXStore().load_from_disk("/tmp/no_such_file.pkl") is False

    def test_load_corrupt_file(self, tmp_path: Path):
        bad = tmp_path / "corrupt.pkl"
        bad.write_text("not pickle data")
        assert NetworkXStore().load_from_disk(str(bad)) is False

    def test_save_when_not_loaded(self, tmp_path: Path):
        """save() on empty store should be a no-op, not an error."""
        path = str(tmp_path / "empty.pkl")
        NetworkXStore().save(path)
        assert not Path(path).exists()
