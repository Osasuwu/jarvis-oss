"""Direct tests for ``agents.scope_hash._hash_scope_files`` (issue #773).

Covers the three consolidation invariants:
- stable output across calls (same input → same hash)
- sensitivity to file *content* change (different file names → different hash)
- sensitivity to file *set* change (added/removed file → different hash)
"""

from __future__ import annotations

from agents.scope_hash import _hash_scope_files


def test_stable_across_calls() -> None:
    """Same input produces same hash on repeated calls."""
    files = ["a.py", "b.py", "c.py"]
    assert _hash_scope_files(files) == _hash_scope_files(files)


def test_order_independent() -> None:
    """Hash is order-independent (sort normalisation)."""
    assert _hash_scope_files(["b.py", "a.py"]) == _hash_scope_files(["a.py", "b.py"])


def test_sensitivity_to_file_name() -> None:
    """Different file names produce different hashes."""
    assert _hash_scope_files(["a.py"]) != _hash_scope_files(["b.py"])


def test_sensitivity_to_added_file() -> None:
    """Adding a file changes the hash."""
    assert _hash_scope_files(["a.py"]) != _hash_scope_files(["a.py", "b.py"])


def test_sensitivity_to_removed_file() -> None:
    """Removing a file changes the hash."""
    assert _hash_scope_files(["a.py", "b.py"]) != _hash_scope_files(["a.py"])


def test_empty_list_is_stable() -> None:
    """Empty file list produces consistent sha256 hex digest."""
    first = _hash_scope_files([])
    second = _hash_scope_files([])
    assert first == second
    assert len(first) == 64  # sha256 hex
