"""Shared fixtures for the CodeGraph test suite.

Fixture hierarchy (most tests should use the highest-level fixture they need):

    tmp_path (built-in)
      └─ simple_repo / kfp_repo / multi_file_repo   (filesystem fixtures)
           └─ *_scan                                  (ScanResult)
                └─ *_graph                            (CodeGraph)
                     └─ *_store                       (NetworkXStore)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from codegraph.scanner import scan_codebase
from codegraph.resolver import build_graph
from codegraph.store import NetworkXStore


# ── Filesystem fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def simple_repo(tmp_path: Path) -> Path:
    """Vanilla Python project — functions, classes, methods, no KFP."""
    (tmp_path / "app.py").write_text(textwrap.dedent("""\
        def greet(name: str) -> str:
            \"\"\"Say hello.\"\"\"
            return format_greeting(name)

        def format_greeting(name: str) -> str:
            return f"Hello, {name}!"

        class Calculator:
            def add(self, a: int, b: int) -> int:
                return a + b

            def multiply(self, a: int, b: int) -> int:
                return self.add(a, 0)
    """))

    (tmp_path / "util.py").write_text(textwrap.dedent("""\
        def helper():
            pass

        def unused():
            pass
    """))
    return tmp_path


@pytest.fixture
def kfp_repo(tmp_path: Path) -> Path:
    """KFP pipeline with 4 components and data-dependency wiring."""
    (tmp_path / "pipeline.py").write_text(textwrap.dedent("""\
        from kfp import dsl
        from kfp.dsl import Input, Output, Dataset, Model, Metrics

        @dsl.component
        def load_data(url: str) -> Output[Dataset]:
            pass

        @dsl.component
        def preprocess(raw: Input[Dataset], threshold: float) -> Output[Dataset]:
            pass

        @dsl.component
        def train_model(data: Input[Dataset], epochs: int) -> Output[Model]:
            pass

        @dsl.component
        def evaluate(model: Input[Model], test_data: Input[Dataset]) -> Output[Metrics]:
            pass

        @dsl.pipeline(name="ml_pipeline")
        def ml_pipeline(source_url: str = "gs://bucket/data.csv"):
            load = load_data(url=source_url)
            prep = preprocess(raw=load.output, threshold=0.5)
            model = train_model(data=prep.output, epochs=10)
            ev = evaluate(model=model.output, test_data=prep.output)
    """))
    return tmp_path


@pytest.fixture
def multi_file_repo(tmp_path: Path) -> Path:
    """Multi-file project for testing cross-file resolution and imports."""
    (tmp_path / "models.py").write_text(textwrap.dedent("""\
        class User:
            def validate(self):
                pass

            def save(self):
                self.validate()
    """))

    (tmp_path / "service.py").write_text(textwrap.dedent("""\
        from models import User

        def create_user(name):
            u = User()
            u.validate()
            return u

        async def fetch_user(uid):
            return User()
    """))

    (tmp_path / "helpers.py").write_text(textwrap.dedent("""\
        import os

        def get_env(key):
            return os.getenv(key)

        def make_path(*parts):
            return os.path.join(*parts)
    """))
    return tmp_path


# ── Scan / Graph / Store fixtures ────────────────────────────────────────────


@pytest.fixture
def simple_scan(simple_repo):
    return scan_codebase(str(simple_repo))


@pytest.fixture
def simple_graph(simple_scan):
    return build_graph(simple_scan)


@pytest.fixture
def kfp_scan(kfp_repo):
    return scan_codebase(str(kfp_repo))


@pytest.fixture
def kfp_graph(kfp_scan):
    return build_graph(kfp_scan)


@pytest.fixture
def kfp_store(kfp_graph) -> NetworkXStore:
    store = NetworkXStore()
    store.load(kfp_graph)
    return store


@pytest.fixture
def multi_file_scan(multi_file_repo):
    return scan_codebase(str(multi_file_repo))


@pytest.fixture
def multi_file_graph(multi_file_scan):
    return build_graph(multi_file_scan)
