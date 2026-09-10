"""Tests for /weekly-release S2 routine-mode helpers (#1658).

``is_routine_host`` gates on the device's ``routine_host`` flag
(``config/device.json``) — decision ``1b7ff8d1-bbca-4207-a7e4-4c1edddef67e``: one
device is the sole routine host, all others refuse.

``weekly_release_notification_for`` produces the ``notify_text`` subject/body
pair per AC3's fixed phrasing, or ``None`` when the run produced no release —
the routine caller treats ``None`` as "stay silent, no weekly spam" rather
than calling ``notify_text`` at all.

#1670: ``version`` is passed through verbatim — the caller already carries
its own ``v`` prefix (or not) via its semver classification, so these tests
pass a pre-prefixed ``v1.2.3`` rather than expecting the function to add one.
"""

from __future__ import annotations

from scripts.weekly_release_engine import is_routine_host, weekly_release_notification_for


def test_is_routine_host_true_when_flag_set():
    assert is_routine_host({"routine_host": True}) is True


def test_is_routine_host_false_when_flag_false():
    assert is_routine_host({"routine_host": False}) is False


def test_is_routine_host_false_when_flag_absent():
    assert is_routine_host({}) is False


def test_notification_for_draft():
    subject, _body = weekly_release_notification_for("jarvis", "v1.2.3", "draft")
    assert subject == "Draft awaiting publication: jarvis v1.2.3"


def test_notification_for_published():
    subject, _body = weekly_release_notification_for("jarvis", "v1.2.3", "published")
    assert subject == "Published release: jarvis v1.2.3"


def test_notification_for_no_release_is_none():
    assert weekly_release_notification_for("jarvis", "v1.2.3", "none") is None
