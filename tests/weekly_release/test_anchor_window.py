"""#1572 AC11 / #1667 - draft-aware anchor window: window anchors on the last
*published* release -> now, capped at 1 month; cap-truncation must be
disclosed by the caller (release body says "covers the period from X to
Y") whenever `truncated` is True.

#1667: an unpublished pending draft must NEVER anchor the window - doing so
collapsed the window to near-empty on every re-run. compute_window() no
longer accepts a pending_draft_at argument at all; SKILL.md Step 2.6 handles
the draft-in-place-update case separately, by scanning prior_releases.
"""

from __future__ import annotations

from scripts.weekly_release_engine import compute_window


def test_window_anchors_to_last_release():
    r = compute_window(
        last_release_at="2026-08-01T00:00:00Z",
        now="2026-08-20T00:00:00Z",
    )
    assert r.start.startswith("2026-08-01")
    assert r.truncated is False


def test_window_ignores_a_more_recent_pending_draft():
    # #1667 regression: a pending draft's created_at must NOT win over the
    # last published release, even though it is more recent - anchoring on
    # it collapsed the window to near-empty on every re-run.
    r = compute_window(
        last_release_at="2026-07-25T00:00:00Z",
        now="2026-08-20T00:00:00Z",
    )
    assert r.start.startswith("2026-07-25")
    assert r.truncated is False


def test_window_caps_at_one_month_and_flags_truncation():
    r = compute_window(
        last_release_at="2026-01-01T00:00:00Z",
        now="2026-08-20T00:00:00Z",
        cap_days=30,
    )
    assert r.truncated is True
    # capped start is (now - 30d), not the real last-release date
    assert not r.start.startswith("2026-01-01")


def test_no_prior_anchor_at_all_falls_back_to_cap_and_flags_truncation():
    r = compute_window(last_release_at=None, now="2026-08-20T00:00:00Z")
    assert r.truncated is True
