"""The autouse guard in tests/conftest.py must actually block live clients.

Incident 2026-09-02: a `pytest tests/reactive_core` run (reactive-core was
demolished in #1802) wrote 392 real `drain_infra_preflight_failure` rows into
the production events table in six minutes, and wake_driver started fanning
them into owner escalations. The suite had always *looked* isolated —
tests/conftest.py sets SUPABASE_URL / SUPABASE_KEY to test values — but it
sets them with `setdefault`, which loses to a real `.env`, so every unstubbed
`supabase.create_client()` call in the suite was pointed at production the
whole time.

The guard is only load-bearing if it fails when removed, hence this test.
"""

from __future__ import annotations

import pytest


def test_create_client_is_blocked() -> None:
    """A test constructing a Supabase client fails instead of reaching prod."""
    import supabase

    with pytest.raises(AssertionError, match="LIVE Supabase client"):
        supabase.create_client("https://real.supabase.co", "real-key")
