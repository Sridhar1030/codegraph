"""Tests for core data structures (models.py) and utility functions (filters.py).

Grouped because both modules are small, pure-logic, and have no external deps.
"""

from __future__ import annotations

import pytest

from codegraph.models import FunctionNode, RawCall, CallEdge, CodeGraph
from codegraph.filters import should_exclude


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_node(**overrides) -> FunctionNode:
    defaults = dict(
        id="mod.py:foo", file="mod.py", class_name=None, function="foo",
        short_name="foo", lineno=1, end_lineno=5, params=2, lines=4,
        docstring="A function", is_async=False, decorators=[],
        node_type="function",
    )
    defaults.update(overrides)
    return FunctionNode(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
#  FunctionNode
# ═══════════════════════════════════════════════════════════════════════════════


class TestFunctionNodeSerialization:
    """to_dict() output must be stable — the server sends it to the UI as JSON."""

    REQUIRED_KEYS = {
        "id", "short_name", "file", "class_name", "function", "lineno",
        "params", "lines", "docstring", "is_async", "decorators", "type",
        "in_degree", "out_degree", "color_class", "file_group", "depth",
        "is_generated",
    }

    def test_all_required_keys_present(self):
        d = _make_node().to_dict()
        assert self.REQUIRED_KEYS.issubset(d.keys())

    def test_type_field_maps_to_node_type(self):
        node = _make_node(node_type="kfp_component")
        assert node.to_dict()["type"] == "kfp_component"

    def test_kfp_params_excluded_when_empty(self):
        assert "kfp_params" not in _make_node().to_dict()

    def test_kfp_params_included_when_present(self):
        params = [{"name": "x", "annotation": "int", "kind": "input"}]
        assert _make_node(kfp_params=params).to_dict()["kfp_params"] == params

    def test_none_docstring_becomes_empty_string(self):
        assert _make_node(docstring=None).to_dict()["docstring"] == ""

    @pytest.mark.parametrize("field,default", [
        ("in_degree", 0),
        ("out_degree", 0),
        ("color_class", "internal"),
        ("depth", 0),
        ("is_generated", False),
        ("file_group", 0),
    ])
    def test_defaults(self, field, default):
        assert getattr(_make_node(), field) == default


class TestFunctionNodeKfpParams:
    """kfp_params is a mutable default — verify each instance gets its own list."""

    def test_independent_instances(self):
        a, b = _make_node(), _make_node()
        a.kfp_params.append({"name": "x"})
        assert b.kfp_params == []


# ═══════════════════════════════════════════════════════════════════════════════
#  CallEdge
# ═══════════════════════════════════════════════════════════════════════════════


class TestCallEdge:

    def test_to_dict_structure(self):
        edge = CallEdge(source="a:f", target="b:g", order=1, edge_type="resolved")
        assert edge.to_dict() == {
            "source": "a:f", "target": "b:g", "order": 1, "type": "resolved",
        }

    @pytest.mark.parametrize("etype", ["resolved", "option", "dynamic", "data_dependency:output -> x"])
    def test_arbitrary_edge_types(self, etype):
        assert CallEdge(source="a", target="b", edge_type=etype).to_dict()["type"] == etype

    def test_defaults(self):
        e = CallEdge(source="a", target="b")
        assert e.order == 0
        assert e.edge_type == "resolved"


# ═══════════════════════════════════════════════════════════════════════════════
#  RawCall
# ═══════════════════════════════════════════════════════════════════════════════


class TestRawCall:

    def test_fields(self):
        rc = RawCall(name="foo", receiver="self", lineno=10, order=1)
        assert (rc.name, rc.receiver, rc.lineno, rc.order) == ("foo", "self", 10, 1)

    def test_optional_receiver(self):
        assert RawCall(name="bar", receiver=None, lineno=1).receiver is None


# ═══════════════════════════════════════════════════════════════════════════════
#  CodeGraph
# ═══════════════════════════════════════════════════════════════════════════════


class TestCodeGraph:

    def test_empty_defaults(self):
        g = CodeGraph()
        assert (g.nodes, g.edges, g.external_packages, g.stats) == ([], [], [], {})
        assert g.kfp_pipelines_meta == {}

    def test_node_by_id_found(self):
        node = _make_node(id="a.py:f")
        assert CodeGraph(nodes=[node]).node_by_id("a.py:f") is node

    def test_node_by_id_not_found(self):
        assert CodeGraph().node_by_id("missing") is None

    def test_node_by_id_returns_first_match(self):
        n1 = _make_node(id="dup")
        n2 = _make_node(id="dup")
        assert CodeGraph(nodes=[n1, n2]).node_by_id("dup") is n1


# ═══════════════════════════════════════════════════════════════════════════════
#  Filters (should_exclude)
# ═══════════════════════════════════════════════════════════════════════════════


class TestShouldExclude:

    @pytest.mark.parametrize("path,patterns,expected", [
        # directory prefix
        ("tests/test_foo.py", ["tests/"], True),
        ("src/tests/test_x.py", ["tests/"], True),
        ("src/main.py", ["tests/"], False),
        # glob
        ("foo_pb2.py", ["*_pb2*"], True),
        ("foo.py", ["*_pb2*"], False),
        ("deep/dir/foo_pb2_grpc.py", ["*_pb2*"], True),
        # exact filename
        ("setup.py", ["setup.py"], True),
        ("main.py", ["setup.py"], False),
        # empty patterns
        ("anything.py", [], False),
    ])
    def test_matching(self, path, patterns, expected):
        assert should_exclude(path, patterns) is expected

    def test_multiple_patterns_any_matches(self):
        patterns = ["tests/", "*_pb2*", "setup.py"]
        assert should_exclude("tests/foo.py", patterns) is True
        assert should_exclude("gen_pb2.py", patterns) is True
        assert should_exclude("setup.py", patterns) is True
        assert should_exclude("main.py", patterns) is False
