"""#1670 - weekly_release_notification_for(): `version` is passed through
verbatim, never prefixed with a literal `v` - the caller's version string
already carries its own `v` when it should (semver classification), so a
hardcoded `v{version}` doubled it (e.g. "vv0.5.0").
"""

from __future__ import annotations

from scripts.weekly_release_engine import weekly_release_notification_for


def test_draft_notification_does_not_double_the_v_prefix():
    subject, _ = weekly_release_notification_for("o/r", "v0.5.0", "draft")
    assert "vv0.5.0" not in subject
    assert "v0.5.0" in subject


def test_published_notification_does_not_double_the_v_prefix():
    subject, _ = weekly_release_notification_for("o/r", "v0.5.0", "published")
    assert "vv0.5.0" not in subject
    assert "v0.5.0" in subject


def test_version_without_v_prefix_is_passed_through_unchanged():
    # weekly_release_notification_for must not itself add a `v` either.
    subject, _ = weekly_release_notification_for("o/r", "0.5.0", "published")
    assert "v0.5.0" not in subject
    assert "0.5.0" in subject


def test_unknown_status_yields_none():
    assert weekly_release_notification_for("o/r", "v0.5.0", "unchanged") is None
