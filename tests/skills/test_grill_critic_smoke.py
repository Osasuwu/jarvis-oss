"""Smoke run for the /grill cross-context CRITIC protocol (issue #692, AC5).

Issue body AC5: "Smoke run: grill a fake design fork; verify subagent fires,
returns critique, dialogue picks up."

The literal Agent dispatch happens only inside a running /grill skill session
(it is a Claude Code runtime behaviour, not a Python-callable). What this smoke
verifies *mechanically* is that the documented protocol is assembly-correct:

  1. CRITIC.md exposes a verbatim-pasteable System block.
  2. The system block survives a round-trip through the parser without losing
     the load-bearing constraints (fixed schema, severity, no prose).
  3. A sample critic response fitting the documented schema parses cleanly into
     (risks, alternatives, challenged_assumption) buckets.
  4. The disposition-loopback gate blocks AC-lock until every item has a
     recorded disposition in {accept, reject, defer}.

Pass ⇒ the protocol the SKILL.md driver describes is mechanically executable;
a future /grill session pointing at CRITIC.md will produce the same shape the
loopback expects.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
CRITIC_MD = REPO_ROOT / ".claude-userlevel" / "skills" / "grill" / "CRITIC.md"


# ---------------------------------------------------------------------------
# Lightweight protocol helpers — used by the smoke and re-usable by /grill.
# These are intentionally side-effect-free so a future skill runtime can import
# the same parsing/gating logic, or so a different smoke can swap fixtures.
# ---------------------------------------------------------------------------


def extract_system_block(critic_md_text: str) -> str:
    """Return the verbatim system-block body that operators paste into Agent.

    CRITIC.md follows the convention from NEUTRAL-RESEARCHER.md: a markdown
    heading containing 'System block' is followed by a fenced code block whose
    body is the verbatim prompt body.
    """
    # Find the System-block heading
    heading_match = re.search(r"^#{1,6}\s.*system block.*$", critic_md_text, re.IGNORECASE | re.MULTILINE)
    assert heading_match, "CRITIC.md must contain a heading mentioning 'System block'"
    # Find the next fenced code block after the heading
    tail = critic_md_text[heading_match.end():]
    fence = re.search(r"```[a-zA-Z]*\n(.+?)\n```", tail, re.DOTALL)
    assert fence, "CRITIC.md must contain a fenced code block under the System-block heading"
    return fence.group(1)


@dataclass(frozen=True)
class Risk:
    severity: str
    text: str


@dataclass(frozen=True)
class CriticVerdict:
    risks: tuple[Risk, ...]
    alternatives: tuple[str, ...]
    challenged_assumption: str


def parse_critic_verdict(verdict_text: str) -> CriticVerdict:
    """Parse a critic response that follows the CRITIC.md output schema.

    Raises ValueError if the verdict violates the documented hard ceilings:
      - more than 3 risks
      - more than 3 alternatives
      - more than 1 challenged assumption
      - a risk missing its [SEVERITY] tag
    """
    def _section(name: str) -> str:
        m = re.search(rf"##\s+{re.escape(name)}\s*\n(.*?)(?=^##\s+|\Z)", verdict_text, re.DOTALL | re.MULTILINE)
        if not m:
            return ""
        return m.group(1).strip()

    risks_section = _section("Risks")
    alts_section = _section("Unmentioned alternatives")
    assumption_section = _section("Challenged assumption")

    risk_lines = [line.strip("- ").strip() for line in risks_section.splitlines() if line.strip().startswith("-")]
    if len(risk_lines) > 3:
        raise ValueError(f"CRITIC schema violation: >3 risks ({len(risk_lines)})")
    risks = []
    for line in risk_lines:
        sev_match = re.match(r"\[(LOW|MEDIUM|HIGH|CRITICAL)\]\s+(.+)", line)
        if not sev_match:
            raise ValueError(f"CRITIC schema violation: risk missing [SEVERITY] tag: {line!r}")
        risks.append(Risk(severity=sev_match.group(1), text=sev_match.group(2)))

    alt_lines = [line.strip("- ").strip() for line in alts_section.splitlines() if line.strip().startswith("-")]
    if len(alt_lines) > 3:
        raise ValueError(f"CRITIC schema violation: >3 alternatives ({len(alt_lines)})")

    assumption_lines = [line.strip("- ").strip() for line in assumption_section.splitlines() if line.strip().startswith("-")]
    if len(assumption_lines) > 1:
        raise ValueError(f"CRITIC schema violation: >1 challenged assumption ({len(assumption_lines)})")
    challenged = assumption_lines[0] if assumption_lines else ""

    return CriticVerdict(
        risks=tuple(risks),
        alternatives=tuple(alt_lines),
        challenged_assumption=challenged,
    )


VALID_DISPOSITIONS = frozenset({"accept", "reject", "defer"})


def ac_lock_allowed(verdict: CriticVerdict, dispositions: dict[str, str]) -> bool:
    """Return True iff every verdict item has a valid recorded disposition.

    The grill driver calls this immediately before committing AC to the issue
    body / CONTEXT.md / record_decision. False ⇒ AC-lock is blocked.

    Items are addressed by stable keys:
      - 'risk:<idx>' for each risk in order
      - 'alt:<idx>' for each alternative
      - 'assumption' for the (at most one) challenged assumption
    """
    required: set[str] = set()
    for i in range(len(verdict.risks)):
        required.add(f"risk:{i}")
    for i in range(len(verdict.alternatives)):
        required.add(f"alt:{i}")
    if verdict.challenged_assumption:
        required.add("assumption")
    for key in required:
        d = dispositions.get(key)
        if d not in VALID_DISPOSITIONS:
            return False
    return True


# ---------------------------------------------------------------------------
# Fixtures — fake design fork + sample critic verdict.
# ---------------------------------------------------------------------------


FAKE_DESIGN_FORK_PROPOSAL = """\
Proposal: switch the memory-recall hook from Python to a Mustache template.

Why: avoid the venv re-exec dance on Windows; Mustache is portable.

AC drafted:
- recall.mustache renders the recall block from the recall payload JSON
- existing Python hook removed
- Windows + Linux smoke shows identical output
"""

# A schema-compliant critic verdict for the fake fork. The proposal has a
# known flaw (Mustache is logic-free, cannot execute Python expressions);
# the critic surfaces it as a CRITICAL risk plus one unmentioned alternative.
SAMPLE_VERDICT_TEXT = """\
## Risks
- [CRITICAL] Mustache is logic-free; the recall hook executes Python expressions and conditional formatting that Mustache cannot evaluate — mustache.github.io spec §Tags
- [HIGH] Removing the Python hook deletes the integration point other always_load probes depend on — scripts/memory-recall-hook.py:1-40

## Unmentioned alternatives
- Stay on Python and fix the venv re-exec on Windows narrowly — windows_shim_must_be_exe_for_node_spawn already documents the .exe-shim pattern, no rewrite needed
- Replace only the rendering layer with Mustache while keeping a Python parent — preserves expression evaluation, gets the portability win on the layer that actually benefits

## Challenged assumption
- Assumes "portable template engine" and "expression evaluator" are interchangeable; Mustache is the former, the recall hook needs the latter.
"""


# ---------------------------------------------------------------------------
# Smoke tests.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def critic_md_text() -> str:
    assert CRITIC_MD.exists(), f"CRITIC.md missing at {CRITIC_MD}"
    return CRITIC_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def system_block(critic_md_text: str) -> str:
    return extract_system_block(critic_md_text)


class TestSystemBlockExtraction:
    """The verbatim-paste convention must survive extraction without losing constraints."""

    def test_system_block_is_nonempty(self, system_block: str):
        assert system_block.strip(), "Extracted system block must not be empty"

    def test_system_block_preserves_fixed_schema_constraint(self, system_block: str):
        assert re.search(r"fixed schema|only the fixed schema|ONLY this", system_block, re.IGNORECASE), \
            "Extracted system block must preserve the fixed-schema constraint"

    def test_system_block_preserves_severity_constraint(self, system_block: str):
        # The {LOW, MEDIUM, HIGH, CRITICAL} ladder must survive into the paste body.
        for sev in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            assert sev in system_block, f"Extracted system block must enumerate severity {sev}"

    def test_system_block_preserves_no_recommendation_rule(self, system_block: str):
        assert re.search(r"no recommendation|no verdict|not.*recommend", system_block, re.IGNORECASE), \
            "Extracted system block must preserve the no-recommendation rule"

    def test_system_block_preserves_hard_ceilings(self, system_block: str):
        # The 3/3/1 ceilings are the load-bearing schema bounds.
        assert re.search(r"(at most|<=|≤|max(imum)?)\s*3\s*risks?", system_block, re.IGNORECASE)
        assert re.search(r"(at most|<=|≤|max(imum)?)\s*3\s*(unmentioned\s+)?alternatives?", system_block, re.IGNORECASE)
        assert re.search(r"(exactly|only)\s*1\s*challenged\s+assumption", system_block, re.IGNORECASE)


class TestSampleVerdictParses:
    """A schema-compliant verdict (fake design fork) must parse cleanly into typed buckets."""

    def test_parses_three_risks_with_severities(self):
        verdict = parse_critic_verdict(SAMPLE_VERDICT_TEXT)
        assert len(verdict.risks) == 2  # sample includes 2 risks; ≤3 is the ceiling, not the floor
        sevs = {r.severity for r in verdict.risks}
        assert sevs == {"CRITICAL", "HIGH"}

    def test_parses_alternatives(self):
        verdict = parse_critic_verdict(SAMPLE_VERDICT_TEXT)
        assert len(verdict.alternatives) == 2
        # Each alternative has a "—" or rationale clause
        for alt in verdict.alternatives:
            assert "—" in alt or "-" in alt

    def test_parses_single_challenged_assumption(self):
        verdict = parse_critic_verdict(SAMPLE_VERDICT_TEXT)
        assert verdict.challenged_assumption
        assert "Mustache" in verdict.challenged_assumption  # captures the load-bearing presupposition


class TestSchemaCeilingsEnforced:
    """Verdicts violating the documented hard ceilings must be rejected at parse time."""

    def test_more_than_three_risks_rejected(self):
        bad = """\
## Risks
- [LOW] r1 — src
- [LOW] r2 — src
- [LOW] r3 — src
- [LOW] r4 — src

## Unmentioned alternatives

## Challenged assumption
"""
        with pytest.raises(ValueError, match=">3 risks"):
            parse_critic_verdict(bad)

    def test_more_than_one_challenged_assumption_rejected(self):
        bad = """\
## Risks

## Unmentioned alternatives

## Challenged assumption
- assumption A
- assumption B
"""
        with pytest.raises(ValueError, match=">1 challenged assumption"):
            parse_critic_verdict(bad)

    def test_risk_missing_severity_rejected(self):
        bad = """\
## Risks
- no severity here — somewhere

## Unmentioned alternatives

## Challenged assumption
"""
        with pytest.raises(ValueError, match="missing \\[SEVERITY\\] tag"):
            parse_critic_verdict(bad)


class TestLoopbackGate:
    """Forced per-item disposition must block AC-lock until every item has one."""

    def test_ac_lock_blocked_with_no_dispositions(self):
        verdict = parse_critic_verdict(SAMPLE_VERDICT_TEXT)
        assert ac_lock_allowed(verdict, {}) is False

    def test_ac_lock_blocked_with_partial_dispositions(self):
        verdict = parse_critic_verdict(SAMPLE_VERDICT_TEXT)
        partial = {"risk:0": "accept", "risk:1": "defer"}  # missing alt:0, alt:1, assumption
        assert ac_lock_allowed(verdict, partial) is False

    def test_ac_lock_unblocked_with_full_dispositions(self):
        verdict = parse_critic_verdict(SAMPLE_VERDICT_TEXT)
        full = {
            "risk:0": "accept",
            "risk:1": "defer",
            "alt:0": "reject",
            "alt:1": "accept",
            "assumption": "accept",
        }
        assert ac_lock_allowed(verdict, full) is True

    def test_invalid_disposition_value_blocks_lock(self):
        verdict = parse_critic_verdict(SAMPLE_VERDICT_TEXT)
        bad = {
            "risk:0": "maybe",  # not in {accept, reject, defer}
            "risk:1": "defer",
            "alt:0": "reject",
            "alt:1": "accept",
            "assumption": "accept",
        }
        assert ac_lock_allowed(verdict, bad) is False

    def test_empty_verdict_unlocks_trivially(self):
        # The CRITIC.md contract explicitly permits empty categories — a critic that
        # finds nothing to flag must not block AC-lock indefinitely.
        empty = CriticVerdict(risks=(), alternatives=(), challenged_assumption="")
        assert ac_lock_allowed(empty, {}) is True


class TestFakeDesignForkEndToEnd:
    """End-to-end smoke: dispatch shape → verdict parse → loopback gate."""

    def test_dispatch_omits_owner_framing(self, system_block: str):
        # Sanity: the system block itself must not embed owner-side framing
        # (the dispatcher is responsible for stripping it, but the template
        # must not bias the critic with phrasing like "the owner believes…").
        for forbidden in ("the owner believes", "we think", "the user wants us to"):
            assert forbidden.lower() not in system_block.lower(), \
                f"System block must not embed owner-side framing: {forbidden!r}"

    def test_full_loop_completes_on_fake_design_fork(self, system_block: str):
        # 1. Operator concatenates system block + stripped proposal.
        dispatched_prompt = system_block + "\n\n" + FAKE_DESIGN_FORK_PROPOSAL
        assert FAKE_DESIGN_FORK_PROPOSAL in dispatched_prompt
        # Owner-side framing markers (none in our fake fork, but guard anyway)
        assert "I think" not in dispatched_prompt
        assert "we believe" not in dispatched_prompt
        # 2. Critic returns the sample verdict.
        verdict = parse_critic_verdict(SAMPLE_VERDICT_TEXT)
        # 3. Owner records dispositions for every returned item.
        dispositions = {
            "risk:0": "accept",   # owner accepts the CRITICAL Mustache flaw
            "risk:1": "defer",    # owner defers the hook-removal concern to follow-up
            "alt:0": "accept",    # owner pursues the narrow venv-fix alternative
            "alt:1": "reject",    # owner rejects the hybrid Mustache+Python alternative
            "assumption": "accept",
        }
        # 4. Gate now permits AC-lock; without these dispositions it would not.
        assert ac_lock_allowed(verdict, dispositions) is True
