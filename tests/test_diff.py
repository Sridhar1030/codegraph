"""Tests for codegraph.diff — graph comparison logic.

Tests only the pure-function code paths (_scan, _resolve, compare_graphs, GraphDiff)
and avoids git operations that require a real repository.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from codegraph.diff import _scan, _resolve, compare_graphs, GraphDiff


# ═══════════════════════════════════════════════════════════════════════════════
#  Lightweight scan + resolve
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiffScan:

    def test_extracts_nodes(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("def foo(): pass\ndef bar(): foo()\n")
        scan = _scan(str(tmp_path))
        assert {n.function for n in scan.nodes} == {"foo", "bar"}

    def test_extracts_methods(self, tmp_path: Path):
        (tmp_path / "m.py").write_text(textwrap.dedent("""\
            class C:
                def meth(self): pass
        """))
        scan = _scan(str(tmp_path))
        assert scan.nodes[0].class_name == "C"

    def test_raw_edges_collected(self, tmp_path: Path):
        (tmp_path / "e.py").write_text("def a(): pass\ndef b(): a()\n")
        scan = _scan(str(tmp_path))
        assert any(e["call_name"] == "a" for e in scan.raw_edges)

    def test_skips_excluded_dirs(self, tmp_path: Path):
        (tmp_path / "venv").mkdir()
        (tmp_path / "venv" / "lib.py").write_text("def hidden(): pass\n")
        (tmp_path / "main.py").write_text("def visible(): pass\n")
        assert {n.function for n in _scan(str(tmp_path)).nodes} == {"visible"}


class TestDiffResolve:

    def test_edges_resolved(self, tmp_path: Path):
        (tmp_path / "r.py").write_text("def target(): pass\ndef caller(): target()\n")
        graph = _resolve(_scan(str(tmp_path)))
        assert ("r.py:caller", "r.py:target") in graph.edge_set

    def test_degree_calculation(self, tmp_path: Path):
        (tmp_path / "d.py").write_text("def a(): pass\ndef b(): a()\n")
        graph = _resolve(_scan(str(tmp_path)))
        by_id = graph.node_by_id
        assert by_id["d.py:a"].in_degree == 1
        assert by_id["d.py:b"].out_degree == 1


# ═══════════════════════════════════════════════════════════════════════════════
#  compare_graphs
# ═══════════════════════════════════════════════════════════════════════════════


class TestCompareGraphs:

    def _build(self, tmp_path: Path, code: str, filename: str = "c.py"):
        (tmp_path / filename).write_text(code)
        return _resolve(_scan(str(tmp_path)))

    def test_identical_graphs(self, tmp_path: Path):
        code = "def f(): pass\n"
        g = self._build(tmp_path, code)
        diff = compare_graphs(g, g)
        assert not diff.has_changes

    def test_added_node(self, tmp_path: Path):
        old = self._build(tmp_path, "def f(): pass\n")
        (tmp_path / "c.py").write_text("def f(): pass\ndef g(): pass\n")
        new = _resolve(_scan(str(tmp_path)))
        diff = compare_graphs(old, new)
        assert "c.py:g" in diff.added_nodes
        assert diff.summary["added"] == 1

    def test_removed_node(self, tmp_path: Path):
        old = self._build(tmp_path, "def f(): pass\ndef g(): pass\n")
        (tmp_path / "c.py").write_text("def f(): pass\n")
        new = _resolve(_scan(str(tmp_path)))
        diff = compare_graphs(old, new)
        assert "c.py:g" in diff.removed_nodes

    def test_modified_node(self, tmp_path: Path):
        old = self._build(tmp_path, "def f():\n    return 1\n")
        (tmp_path / "c.py").write_text("def f():\n    return 2\n")
        new = _resolve(_scan(str(tmp_path)))
        diff = compare_graphs(old, new)
        assert "c.py:f" in diff.modified_nodes

    def test_added_edge(self, tmp_path: Path):
        old = self._build(tmp_path, "def a(): pass\ndef b(): pass\n")
        (tmp_path / "c.py").write_text("def a(): pass\ndef b(): a()\n")
        new = _resolve(_scan(str(tmp_path)))
        diff = compare_graphs(old, new)
        assert ("c.py:b", "c.py:a") in diff.added_edges

    def test_removed_edge(self, tmp_path: Path):
        old = self._build(tmp_path, "def a(): pass\ndef b(): a()\n")
        (tmp_path / "c.py").write_text("def a(): pass\ndef b(): pass\n")
        new = _resolve(_scan(str(tmp_path)))
        diff = compare_graphs(old, new)
        assert ("c.py:b", "c.py:a") in diff.removed_edges


# ═══════════════════════════════════════════════════════════════════════════════
#  GraphDiff serialization
# ═══════════════════════════════════════════════════════════════════════════════


class TestGraphDiffSerialization:

    def test_to_dict_fields(self):
        d = GraphDiff(
            added_nodes=["a"], removed_nodes=["b"], modified_nodes=["c"],
            added_edges=[("x", "y")], removed_edges=[("p", "q")],
            commit_a="abc", commit_b="def",
        )
        out = d.to_dict()
        assert out["commit_a"] == "abc"
        assert out["summary"]["added"] == 1
        assert out["added_edges"] == [["x", "y"]]

    def test_to_json_roundtrip(self):
        import json
        d = GraphDiff(added_nodes=["n"])
        parsed = json.loads(d.to_json())
        assert parsed["added_nodes"] == ["n"]

    def test_has_changes_false_when_empty(self):
        assert GraphDiff().has_changes is False

    @pytest.mark.parametrize("field,value", [
        ("added_nodes", ["a"]),
        ("removed_nodes", ["b"]),
        ("modified_nodes", ["c"]),
        ("added_edges", [("x", "y")]),
        ("removed_edges", [("p", "q")]),
    ])
    def test_has_changes_true_for_each_field(self, field, value):
        d = GraphDiff(**{field: value})
        assert d.has_changes is True
