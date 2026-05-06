"""File/directory exclusion filters for CodeGraph scanning."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath


def should_exclude(filepath: str, patterns: list[str]) -> bool:
    """Check whether *filepath* (relative to scan root) matches any exclusion pattern.

    Patterns can be:
    - Directory prefixes ending with /  -> "tests/"  matches tests/foo.py
    - Glob patterns                     -> "*_pb2*"  matches anything_pb2.py
    - Exact filenames                   -> "setup.py"
    """
    path = PurePosixPath(filepath)
    for pat in patterns:
        if pat.endswith("/"):
            prefix = pat.rstrip("/")
            if any(part == prefix for part in path.parts):
                return True
        else:
            if fnmatch(path.name, pat):
                return True
            if fnmatch(filepath, pat):
                return True
    return False
