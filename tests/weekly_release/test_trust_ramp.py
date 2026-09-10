"""#1572 AC10 - trust ramp: first 4 releases per repo are drafts; 4
consecutive published-without-edits -> auto-publish. State is derived
entirely from the release history handed in (the caller reads it live
from the GitHub releases API - no local state is stored by this function).
"""

from __future__ import annotations

from scripts.weekly_release_engine import trust_ramp_state


def test_no_prior_releases_is_draft():
    assert trust_ramp_state([]) == "draft"


def test_fewer_than_four_prior_releases_is_draft():
    releases = [{"published": True, "edited_after_publish": False}] * 3
    assert trust_ramp_state(releases) == "draft"


def test_four_consecutive_clean_publishes_graduates_to_auto():
    releases = [{"published": True, "edited_after_publish": False}] * 4
    assert trust_ramp_state(releases) == "auto"


def test_a_post_publish_edit_in_the_last_four_keeps_it_on_draft():
    releases = [
        {"published": True, "edited_after_publish": False},
        {"published": True, "edited_after_publish": True},
        {"published": True, "edited_after_publish": False},
        {"published": True, "edited_after_publish": False},
    ]
    assert trust_ramp_state(releases) == "draft"


def test_an_unpublished_draft_in_the_last_four_keeps_it_on_draft():
    releases = [
        {"published": False, "edited_after_publish": False},
        {"published": True, "edited_after_publish": False},
        {"published": True, "edited_after_publish": False},
        {"published": True, "edited_after_publish": False},
    ]
    assert trust_ramp_state(releases) == "draft"


def test_only_the_four_most_recent_releases_matter():
    # An old bad release beyond the last-4 window must not hold the ramp
    # back forever once 4 clean releases have happened since.
    releases = [{"published": True, "edited_after_publish": False}] * 4 + [
        {"published": True, "edited_after_publish": True}
    ]
    assert trust_ramp_state(releases) == "auto"
