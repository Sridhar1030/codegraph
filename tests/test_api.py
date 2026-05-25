"""Tests for codegraph.server — FastAPI endpoint integration tests.

Uses TestClient for synchronous HTTP testing against the real app.
Each test method clears state to avoid cross-test contamination.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codegraph.server import app, _store, _scan_state


@pytest.fixture
def client():
    """Fresh test client with clean server state."""
    _store._graph = None
    _store._code_graph = None
    _store._meta = {}
    _store._kfp_meta = {}
    _scan_state.update({
        "repo_path": None, "exclude_patterns": [],
        "last_scan_time": None, "scan_duration": None, "scanning": False,
    })
    return TestClient(app)


@pytest.fixture
def loaded_client(client: TestClient, simple_repo: Path) -> TestClient:
    """Client with a simple repo already scanned."""
    resp = client.post("/scan", json={"path": str(simple_repo)})
    assert resp.status_code == 200
    return client


@pytest.fixture
def kfp_client(client: TestClient, kfp_repo: Path) -> TestClient:
    """Client with a KFP repo already scanned."""
    resp = client.post("/scan", json={"path": str(kfp_repo)})
    assert resp.status_code == 200
    return client


# ═══════════════════════════════════════════════════════════════════════════════
#  Health / status
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatus:

    def test_status_before_scan(self, client):
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert data["total_nodes"] == 0
        assert data["repo_path"] is None

    def test_status_after_scan(self, loaded_client):
        r = loaded_client.get("/status")
        data = r.json()
        assert data["total_nodes"] > 0
        assert data["repo_path"] is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  Scan
# ═══════════════════════════════════════════════════════════════════════════════


class TestScan:

    def test_scan_success(self, client, simple_repo):
        r = client.post("/scan", json={"path": str(simple_repo)})
        assert r.status_code == 200
        data = r.json()
        assert data["total_nodes"] > 0
        assert data["total_edges"] >= 0

    def test_scan_invalid_path(self, client):
        r = client.post("/scan", json={"path": "/nonexistent/path"})
        assert r.status_code == 400

    def test_scan_with_exclude(self, client, tmp_path):
        (tmp_path / "keep.py").write_text("def kept(): pass\n")
        (tmp_path / "skip.py").write_text("def skipped(): pass\n")
        r = client.post("/scan", json={"path": str(tmp_path), "exclude": ["skip.py"]})
        assert r.status_code == 200
        nodes = client.get("/graph/nodes?limit=100").json()
        names = {n["function"] for n in nodes}
        assert "kept" in names
        assert "skipped" not in names


# ═══════════════════════════════════════════════════════════════════════════════
#  Resync / Clear
# ═══════════════════════════════════════════════════════════════════════════════


class TestResyncAndClear:

    def test_resync_without_scan(self, client):
        assert client.post("/resync").status_code == 400

    def test_resync_after_scan(self, loaded_client):
        r = loaded_client.post("/resync")
        assert r.status_code == 200
        assert r.json()["total_nodes"] > 0

    def test_clear(self, loaded_client):
        r = loaded_client.post("/clear")
        assert r.status_code == 200
        assert r.json()["status"] == "cleared"
        assert loaded_client.get("/status").json()["total_nodes"] == 0

    def test_clear_resets_kfp_meta(self, kfp_client):
        """Regression: /clear must reset KFP pipeline metadata too."""
        pipelines = kfp_client.get("/graph/kfp/pipelines").json()
        assert len(pipelines) >= 1

        kfp_client.post("/clear")
        assert kfp_client.get("/graph/kfp/pipelines").json() == []


# ═══════════════════════════════════════════════════════════════════════════════
#  Graph query endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestGraphNodes:

    def test_empty_before_scan(self, client):
        assert client.get("/graph/nodes").json() == []

    def test_returns_nodes(self, loaded_client):
        nodes = loaded_client.get("/graph/nodes").json()
        assert len(nodes) > 0

    def test_search_filter(self, loaded_client):
        nodes = loaded_client.get("/graph/nodes?search=greet").json()
        assert all("greet" in n["short_name"].lower() for n in nodes)

    def test_type_filter(self, kfp_client):
        nodes = kfp_client.get("/graph/nodes?type=kfp_component").json()
        assert len(nodes) == 4


class TestGraphNode:

    def test_found(self, loaded_client):
        r = loaded_client.get("/graph/node/app.py:greet")
        assert r.status_code == 200
        assert r.json()["function"] == "greet"

    def test_not_found(self, loaded_client):
        assert loaded_client.get("/graph/node/fake:id").status_code == 404

    def test_404_when_no_graph(self, client):
        assert client.get("/graph/node/any:id").status_code == 404


class TestNeighborsEndpoint:

    def test_returns_subgraph(self, loaded_client):
        r = loaded_client.get("/graph/node/app.py:greet/neighbors")
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data and "edges" in data

    def test_404_when_no_graph(self, client):
        assert client.get("/graph/node/any:id/neighbors").status_code == 404


class TestImpactEndpoint:

    def test_valid(self, loaded_client):
        r = loaded_client.get("/graph/node/app.py:greet/impact")
        assert r.status_code == 200
        data = r.json()
        assert "focus" in data
        assert "downstream" in data
        assert "upstream" in data

    def test_missing(self, loaded_client):
        assert loaded_client.get("/graph/node/fake:id/impact").status_code == 404


class TestFlowchartEndpoint:

    def test_valid(self, loaded_client):
        r = loaded_client.get("/graph/node/app.py:greet/flowchart")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "app.py:greet"
        assert "children" in data

    def test_missing(self, loaded_client):
        assert loaded_client.get("/graph/node/fake:id/flowchart").status_code == 404


class TestFileEndpoints:

    def test_graph_files(self, loaded_client):
        files = loaded_client.get("/graph/files").json()
        assert len(files) >= 1
        assert any(f["file"] == "app.py" for f in files)

    def test_graph_file_nodes(self, loaded_client):
        nodes = loaded_client.get("/graph/file/app.py").json()
        assert len(nodes) >= 2

    def test_graph_full(self, loaded_client):
        data = loaded_client.get("/graph/full").json()
        assert len(data["nodes"]) > 0

    def test_graph_full_empty(self, client):
        data = client.get("/graph/full").json()
        assert data["nodes"] == []


# ═══════════════════════════════════════════════════════════════════════════════
#  KFP endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestKFPEndpoints:

    def test_pipelines_empty(self, client):
        assert client.get("/graph/kfp/pipelines").json() == []

    def test_pipelines_listed(self, kfp_client):
        pipelines = kfp_client.get("/graph/kfp/pipelines").json()
        assert len(pipelines) == 1
        assert pipelines[0]["name"] == "ml_pipeline"

    def test_pipeline_subgraph(self, kfp_client):
        pipelines = kfp_client.get("/graph/kfp/pipelines").json()
        pid = pipelines[0]["pipeline_id"]
        r = kfp_client.get(f"/graph/kfp/pipeline/{pid}")
        assert r.status_code == 200
        data = r.json()
        assert data["stats"]["node_count"] == 5

    def test_pipeline_not_found(self, kfp_client):
        assert kfp_client.get("/graph/kfp/pipeline/nonexistent").status_code == 404

    def test_pipeline_no_graph(self, client):
        assert client.get("/graph/kfp/pipeline/any").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
#  UI
# ═══════════════════════════════════════════════════════════════════════════════


class TestUI:

    def test_html_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "CodeGraph" in r.text
