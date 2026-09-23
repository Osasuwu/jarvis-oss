"""Checks for scripts/doc_review.py and .github/workflows/doc-review.yml (#104).

Unit tests cover each pure function the workflow relies on: classify, full or delta, drift key,
model resolution, findings validation, report/findings agreement, union and rounds. The CLI tests
run `verdict` end to end on a state directory, with the AC15 fixture key. The contract tests read
the workflow as text, as in test_quote_cron.py.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "doc-review.yml").read_text(encoding="utf-8")
FIXTURE_CALIBRATION = ROOT / "tests" / "fixtures" / "doc_review" / "CALIBRATION.md"

_spec = importlib.util.spec_from_file_location("doc_review", ROOT / "scripts" / "doc_review.py")
dr = importlib.util.module_from_spec(_spec)
sys.modules["doc_review"] = dr
_spec.loader.exec_module(dr)

HEAD = "a" * 40
PREV = "b" * 40
OLDER = "c" * 40
MODEL = "claude-opus-5"


def _steps() -> list[str]:
    body = WORKFLOW.split("\n    steps:\n", 1)[1]
    return ["      - " + s for s in re.split(r"^      - ", body, flags=re.M) if s.strip()]


def _step(name: str) -> str:
    [step] = [s for s in _steps() if f"name: {name}\n" in s]
    return step


def _bot(body: str, login: str = dr.BOT_LOGIN) -> dict:
    return {"user": {"login": login}, "body": body, "author_association": "NONE"}


def _block_comment(commit: str, docs: dict, findings: list, report: str = "") -> dict:
    block = {"commit": commit, "docs": docs, "findings": findings, "status": "x"}
    return _bot(report + "\n\n" + dr.render_block(block))


def _f(fid="M1", file="docs/a.md", line=3, cls="quote", label="blocking") -> dict:
    return {"id": fid, "file": file, "line": line, "class": cls, "label": label}


# --- classify -------------------------------------------------------------


def test_reviewable_docs_are_docs_markdown_except_the_excluded_ones():
    changed = ["docs/a.md", "docs/sub/b.md", "docs/SIGNOFF.md", "docs/adr/0001-x.md", "README.md",
               "docs/x.txt", "examples/e.md", "scripts/s.py", "docs/adr.md", "docs/adr-notes/y.md"]
    assert dr.reviewable_docs(changed) == ["docs/a.md", "docs/adr-notes/y.md", "docs/adr.md",
                                           "docs/sub/b.md"]


def test_doc_exclusion_is_one_rule_shared_with_the_structure_gate():
    sys.path.insert(0, str(ROOT / "tests"))
    import structure_gate

    assert dr.SIGNOFF_LEDGER_PATH == structure_gate.SIGNOFF_LEDGER_PATH
    assert dr.EXCLUDED_DOC_DIRS == structure_gate.EXCLUDED_DOC_DIRS
    for path in ["docs/SIGNOFF.md", "docs/adr/0001-x.md", "docs/adr/deep/y.md", "docs/a.md",
                 "docs/adr.md", "docs/sub/SIGNOFF.md"]:
        assert dr.is_excluded_doc(path) == structure_gate.is_excluded_doc(path), path
    assert dr.is_excluded_doc("docs/adr/0001-x.md") and dr.is_excluded_doc("docs/SIGNOFF.md")
    assert not dr.is_excluded_doc("docs/a.md")


@pytest.mark.parametrize(
    "fork, draft, changed, status, message",
    [
        (True, True, ["docs/a.md"], "fail", "not reviewed: fork, no secret"),
        (False, True, ["docs/a.md"], "fail", "not reviewed: draft"),
        (False, True, ["README.md"], "fail", "not reviewed: draft"),
        (False, False, ["README.md", "docs/SIGNOFF.md", "docs/adr/0001-x.md"], "pass", "no doc change"),
        (False, False, ["docs/a.md", "README.md"], "review", "reviewing 1 doc(s)"),
    ],
)
def test_classify_checks_fork_then_draft_then_doc_change(fork, draft, changed, status, message):
    result = dr.classify(event="pull_request", is_fork=fork, is_draft=draft, changed=changed)
    assert result.status == status
    assert result.message.startswith(message)


def test_classify_dispatch_reviews_given_docs_and_fails_on_none():
    ok = dr.classify(event="workflow_dispatch", is_fork=False, is_draft=False,
                     changed=["docs/a.md", "README.md"])
    assert (ok.status, ok.docs) == ("review", ("docs/a.md",))
    none = dr.classify(event="workflow_dispatch", is_fork=False, is_draft=False, changed=[])
    assert none.status == "fail"


def test_parse_files_input_accepts_spaces_commas_and_newlines():
    assert dr.parse_files_input("docs/a.md, docs/b.md\ndocs/c.md") == [
        "docs/a.md", "docs/b.md", "docs/c.md"]


# --- full or delta --------------------------------------------------------

DOC = "# Title\n\n" + "".join(f"line {i}\n" for i in range(1, 21)) + "## Options\n\nmore\n"


def test_no_prior_full_review_means_full():
    assert dr.choose_kind(has_prior_full=False, old_text=DOC, new_text=DOC) == "full"


def test_missing_old_text_means_full():
    assert dr.choose_kind(has_prior_full=True, old_text=None, new_text=DOC) == "full"


def test_small_change_means_delta():
    new = DOC.replace("line 5\n", "line five\n")
    assert dr.choose_kind(has_prior_full=True, old_text=DOC, new_text=new) == "delta"


def test_unchanged_text_means_delta():
    assert dr.choose_kind(has_prior_full=True, old_text=DOC, new_text=DOC) == "delta"


@pytest.mark.parametrize(
    "new",
    [
        DOC.replace("## Options", "## Choices"),  # renamed
        DOC.replace("## Options\n", ""),  # removed
        DOC + "\n### Added\n",  # added
    ],
)
def test_any_heading_change_means_full(new):
    assert dr.choose_kind(has_prior_full=True, old_text=DOC, new_text=new) == "full"


def test_heading_lines_ignore_fenced_code():
    text = "# Real\n```\n# not a heading\n```\n~~~\n## nor this\n~~~\n## Real two\n"
    assert dr.heading_lines(text) == ["# Real", "## Real two"]


def test_changed_share_threshold_sits_at_thirty_percent():
    assert dr.DELTA_MAX_CHANGED == 0.30
    old = "".join(f"l{i}\n" for i in range(100))
    at = "".join((f"x{i}\n" if i < 30 else f"l{i}\n") for i in range(100))
    over = "".join((f"x{i}\n" if i < 31 else f"l{i}\n") for i in range(100))
    assert dr.changed_fraction(old, at) == pytest.approx(0.30)
    assert dr.choose_kind(has_prior_full=True, old_text=old, new_text=at) == "delta"
    assert dr.choose_kind(has_prior_full=True, old_text=old, new_text=over) == "full"


def test_plan_doc_ignores_runs_on_the_head_commit_so_a_rerun_repeats_the_decision():
    blobs = {(PREV, "docs/a.md"): DOC, (HEAD, "docs/a.md"): DOC.replace("line 5", "line V")}

    def read(rev, path):
        return blobs.get((rev, path))

    earlier = dr.parse_blocks([
        _block_comment(PREV, {"docs/a.md": "full"}, [],
                       report="## review-doc: docs/a.md @ " + PREV + "\n\n- M1 docs/a.md:3 — x"),
    ])
    first = dr.plan_doc("docs/a.md", HEAD, earlier, read)
    assert (first.kind, first.reviewed_commit, first.first_reviewed_commit) == ("delta", PREV, PREV)
    assert first.earlier and "M1" in first.earlier[0][1]
    after_rerun = earlier + dr.parse_blocks([_block_comment(HEAD, {"docs/a.md": "delta"}, [])])
    assert dr.plan_doc("docs/a.md", HEAD, after_rerun, read).kind == "delta"


def test_unreviewable_runs_are_not_prior_reviews():
    """Live check on #104: a run whose review step failed must not turn the next commit into a
    delta against text nobody reviewed."""
    failed = dr.parse_blocks([_bot(dr.render_block(
        {"commit": PREV, "docs": {"docs/a.md": "full"}, "findings": [], "status": "unreviewable"}))])
    plan = dr.plan_doc("docs/a.md", HEAD, failed, lambda rev, path: DOC)
    assert (plan.kind, plan.reviewed_commit, plan.first_reviewed_commit) == ("full", None, None)
    assert dr.count_rounds(failed) == 0


def test_plan_doc_without_earlier_runs_is_full():
    plan = dr.plan_doc("docs/a.md", HEAD, [], lambda rev, path: DOC)
    assert (plan.kind, plan.reviewed_commit, plan.first_reviewed_commit) == ("full", None, None)


# --- drift key ------------------------------------------------------------


def test_action_sha_is_read_from_the_workflow():
    assert dr.action_sha(WORKFLOW) == "cfc3eb22bfed5c26ef66e3223c982af27e4524de"
    with pytest.raises(dr.Unreviewable):
        dr.action_sha("uses: anthropics/claude-code-action@v1\n")


def test_drift_key_changes_with_each_part():
    base = dict(workflow=b"wf", action="a" * 40, model=MODEL, skill=b"skill")
    key = dr.drift_key(**base)
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert dr.drift_key(**base) == key
    for part, value in (("workflow", b"wf2"), ("action", "b" * 40), ("model", "claude-opus-6"),
                        ("skill", b"skill2")):
        assert dr.drift_key(**dict(base, **{part: value})) != key, part
    # Length-prefixed: moving bytes between parts changes the key.
    assert dr.drift_key(workflow=b"ab", action="", model="", skill=b"c") != dr.drift_key(
        workflow=b"a", action="", model="", skill=b"bc")


def test_stored_key_absent_means_uncalibrated_not_failure():
    assert dr.parse_stored_key("# calibration\nno key here\n") is None
    state, message = dr.check_drift(None, "f" * 64, MODEL)
    assert state == "uncalibrated" and message.startswith("uncalibrated")


def test_stored_key_malformed_is_an_error():
    with pytest.raises(ValueError):
        dr.parse_stored_key("drift-key: nothex\n")
    with pytest.raises(ValueError):
        dr.parse_stored_key(f"drift-key: {'a' * 64}\ndrift-key: {'b' * 64}\n")


def test_committed_key_matches_the_current_inputs():
    """#106: the key in CALIBRATION.md is the one a run on these inputs computes. An edit to the
    workflow, the action pin or SKILL.md without a recalibration fails here, before any run."""
    text = (ROOT / dr.CALIBRATION_PATH).read_text(encoding="utf-8")
    stored = dr.parse_stored_key(text)
    assert stored is not None, "CALIBRATION.md has no drift-key line"
    key, model = stored
    assert model, "the key line names no model"
    # read_text gives LF line ends, as git stores them and the runner checks them out, also on a
    # Windows checkout that converts to CRLF.
    skill = (ROOT / dr.SKILL_PATH).read_text(encoding="utf-8").encode()
    assert key == dr.drift_key(workflow=WORKFLOW.encode(), action=dr.action_sha(WORKFLOW),
                               model=model, skill=skill)
    assert dr.check_drift(stored, key, model)[0] == "calibrated"


def test_fixture_key_mismatch_with_other_model_is_drift_model_changed():
    stored = dr.parse_stored_key(FIXTURE_CALIBRATION.read_text(encoding="utf-8"))
    assert stored == ("0123456789abcdef" * 4, "claude-opus-4-1-20250805")
    key = dr.drift_key(workflow=WORKFLOW.encode(), action=dr.action_sha(WORKFLOW), model=MODEL,
                       skill=b"skill")
    state, message = dr.check_drift(stored, key, MODEL)
    assert state == "drift"
    assert message == "drift: model changed (claude-opus-4-1-20250805 → claude-opus-5)"


def test_key_mismatch_with_same_model_is_drift():
    state, message = dr.check_drift(("0" * 64, MODEL), "1" * 64, MODEL)
    assert state == "drift" and "key mismatch" in message


def test_matching_key_is_calibrated():
    key = "e" * 64
    stored = dr.parse_stored_key(dr.format_key_line(key, MODEL) + "\n")
    assert dr.check_drift(stored, key, MODEL)[0] == "calibrated"


# --- resolved model -------------------------------------------------------


def _result(usage, **extra) -> dict:
    return dict({"type": "result", "subtype": "success", "is_error": False, "modelUsage": usage},
                **extra)


def test_resolved_model_is_the_single_alias_family_key_of_model_usage():
    msgs = [{"type": "system", "subtype": "init", "model": MODEL},
            _result({MODEL: {}, "claude-haiku-4-5-20251001": {}})]
    assert dr.resolve_model(msgs, "opus") == MODEL


@pytest.mark.parametrize(
    "msgs",
    [
        [],
        [_result({})],
        [_result({"claude-haiku-4-5": {}})],
        [_result({"claude-opus-5": {}, "claude-opus-4-1": {}})],
        [_result({MODEL: {}}, subtype="error_max_turns", is_error=True)],
        [{"type": "result", "subtype": "success"}],
    ],
)
def test_unresolvable_model_is_unreviewable_never_the_alias(msgs):
    with pytest.raises(dr.Unreviewable):
        dr.resolve_model(msgs, "opus")


def test_session_notes_name_denied_tools_and_final_text():
    msgs = [_result({MODEL: {}}, num_turns=24, result="Could not write the report.",
                    permission_denials=[
                        {"tool_name": "Bash", "tool_use_id": "t1",
                         "tool_input": {"command": "python scripts/check_quotes.py docs/a.md"}},
                        {"tool_name": "Write", "tool_use_id": "t2",
                         "tool_input": {"file_path": "/tmp/x.md", "content": "secret body"}},
                    ])]
    notes = dr.session_notes(msgs)
    assert notes[0] == "turns: 24, subtype: success"
    assert "denied: Bash python scripts/check_quotes.py docs/a.md" in notes
    assert "denied: Write /tmp/x.md" in notes
    assert not any("secret body" in n for n in notes)
    assert notes[-1] == "final text: Could not write the report."
    assert dr.session_notes([]) == ["no result message"]


# --- findings -------------------------------------------------------------


def test_findings_validate_and_every_mismatch_is_blocking():
    data = {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [
        _f("M1", label="follow-up"),
        _f("O1", line=None, cls="missing-option", label="follow-up"),
        _f("U1", cls="unverifiable", label="blocking"),
    ]}]}
    out = dr.validate_findings(data, {"docs/a.md": "full"})
    labels = {f.id: f.label for f in out["docs/a.md"]}
    assert labels == {"M1": "blocking", "O1": "follow-up", "U1": "blocking"}


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"reports": [{"doc": "docs/other.md", "kind": "full", "findings": []}]},
        {"reports": [{"doc": "docs/a.md", "kind": "delta", "findings": []}]},
        {"reports": []},
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [_f(fid="X1")]}]},
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [_f(cls="typo")]}]},
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [_f(label="minor")]}]},
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [_f(line=0)]}]},
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [_f(line="3")]}]},
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [_f(file="../x.md")]}]},
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [_f(), _f()]}]},
    ],
)
def test_bad_findings_are_unreviewable(data):
    with pytest.raises(dr.Unreviewable):
        dr.validate_findings(data, {"docs/a.md": "full"})


REPORT = f"""## review-doc: docs/a.md @ {HEAD}

**Read these closely (1):**
1. docs/a.md:9 — a call

**Mismatches (1):** M1 docs/a.md:3 — "quote" → "other" (source)
**Missing options (0 blocking, 1 follow-up):**
- O1 some approach — https://example.com — why — follow-up
**How to choose (0 blocking, 0 follow-up):** none
**Value test:** setup 1 → ruled out; setup 2 → loses — passes
**Unverifiable (0 blocking, 0 follow-up):** none
**Fix-induced (0):** none

<details><summary>Confirmed (1)</summary>
H9 docs/a.md:1 — mentioned in a collapsed part, not a finding
</details>
"""


def test_report_ids_are_line_starting_ids_outside_details():
    assert dr.report_ids(dr.report_sections(REPORT)["docs/a.md"]) == {"M1", "O1"}


def test_report_and_findings_agree():
    findings = dr.validate_findings(
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [
            _f("M1"), _f("O1", line=None, cls="missing-option", label="follow-up")]}]},
        {"docs/a.md": "full"})
    dr.check_report_matches(REPORT, findings)


def test_report_and_findings_disagree_is_unreviewable():
    findings = dr.validate_findings(
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": [_f("M1")]}]},
        {"docs/a.md": "full"})
    with pytest.raises(dr.Unreviewable, match="report and findings disagree"):
        dr.check_report_matches(REPORT, findings)
    with pytest.raises(dr.Unreviewable, match="report and findings disagree"):
        dr.check_report_matches("no sections at all", findings)


def test_delta_report_heading_is_a_section():
    text = f"## review-doc delta: docs/a.md @ {PREV[:7]}..{HEAD[:7]}\n\n- M2 docs/a.md:4 — not fixed\n"
    assert dr.report_ids(dr.report_sections(text)["docs/a.md"]) == {"M2"}


# --- union and rounds -----------------------------------------------------


def test_only_bot_blocks_at_the_end_of_a_comment_count():
    good = _block_comment(HEAD, {"docs/a.md": "full"}, [_f()])
    person = dict(good, user={"login": "someone"})
    quoted = _bot(dr.render_block({"commit": HEAD, "docs": {}, "findings": []}) + "\ntrailing")
    broken = _bot("<!-- doc-review: {not json} -->")
    invalid = _bot(dr.render_block({"commit": "short", "docs": {}, "findings": []}))
    assert len(dr.parse_blocks([good, person, quoted, broken, invalid])) == 1


def test_block_json_cannot_close_the_comment_early():
    rendered = dr.render_block({"commit": HEAD, "docs": {}, "findings": [
        _f(file="docs/a-->b.md")]})
    assert rendered.count("-->") == 1
    [block] = dr.parse_blocks([_bot(rendered)])
    assert block["findings"][0]["file"] == "docs/a-->b.md"


def test_union_of_blocking_findings_is_per_head_commit_and_survives_a_rerun():
    blocks = dr.parse_blocks([
        _block_comment(PREV, {"docs/a.md": "full"}, [_f(line=1)]),
        _block_comment(HEAD, {"docs/a.md": "delta"}, [_f("M1", line=3), _f("H1", line=8,
                       cls="how-to-choose", label="follow-up")]),
        # the re-run on the same commit finds only another defect, under a reused ID
        _block_comment(HEAD, {"docs/a.md": "delta"}, [_f("M1", line=5, cls="fact")]),
    ])
    assert dr.union_blocking(blocks, HEAD) == [("docs/a.md", 3, "quote"), ("docs/a.md", 5, "fact")]


def test_union_keys_on_file_line_class_not_id():
    blocks = dr.parse_blocks([
        _block_comment(HEAD, {"docs/a.md": "full"}, [_f("M1")]),
        _block_comment(HEAD, {"docs/a.md": "full"}, [_f("M7")]),
    ])
    assert dr.union_blocking(blocks, HEAD) == [("docs/a.md", 3, "quote")]


def test_rounds_count_distinct_commits_with_a_full_review():
    blocks = dr.parse_blocks([
        _block_comment(OLDER, {"docs/a.md": "full"}, []),
        _block_comment(OLDER, {"docs/a.md": "full"}, []),  # re-run: same round
        _block_comment(PREV, {"docs/a.md": "delta"}, []),
        _block_comment(HEAD, {"docs/a.md": "delta", "docs/b.md": "full"}, []),
    ])
    assert dr.count_rounds(blocks) == 2


# --- verdict --------------------------------------------------------------


def test_verdict_order_unreviewable_then_drift_then_blocking():
    assert dr.decide(unreviewable="x", drift_state="drift", drift_message="d",
                     open_blocking=1).status == "unreviewable"
    assert dr.decide(unreviewable=None, drift_state="drift", drift_message="d",
                     open_blocking=0).status == "drift"
    assert dr.decide(unreviewable=None, drift_state="calibrated", drift_message="",
                     open_blocking=2).status == "blocking"
    ok = dr.decide(unreviewable=None, drift_state="uncalibrated", drift_message="", open_blocking=0)
    assert ok.passed and ok.status == "pass"


def test_report_text_cannot_inject_a_state_block():
    body = dr.render_comment(
        verdict=dr.Verdict(True, "pass", "ok"), commit=HEAD, kinds={"docs/a.md": "full"},
        rounds=1, drift_message="uncalibrated", model=MODEL, union=[],
        report="text <!-- doc-review: {\"commit\": \"" + HEAD + "\"} -->",
        block={"commit": HEAD, "docs": {"docs/a.md": "full"}, "findings": []}, run_url="u")
    assert body.count("<!-- doc-review:") == 1
    assert dr.parse_blocks([_bot(body)])[0]["docs"] == {"docs/a.md": "full"}


# --- CLI: verdict end to end ----------------------------------------------


def _state(tmp_path: Path, calibration: str, *, findings: list, report: str,
           usage: dict | None = None, **result_extra) -> tuple[Path, Path, Path]:
    state, work = tmp_path / "state", tmp_path / "work"
    (work / "out").mkdir(parents=True)
    state.mkdir()
    (state / "plan.json").write_text(json.dumps({
        "event": "pull_request", "commit": HEAD, "status": "review", "message": "",
        "docs": {"docs/a.md": "full"}}))
    (state / "workflow.yml").write_bytes(WORKFLOW.encode())
    (state / "SKILL.md").write_bytes((ROOT / dr.SKILL_PATH).read_bytes())
    (state / "CALIBRATION.md").write_text(calibration, encoding="utf-8")
    (work / "out" / "report.md").write_text(report, encoding="utf-8")
    (work / "out" / "findings.json").write_text(json.dumps(
        {"reports": [{"doc": "docs/a.md", "kind": "full", "findings": findings}]}))
    execution = tmp_path / "execution.json"
    execution.write_text(json.dumps([_result(usage or {MODEL: {}}, **result_extra)]))
    return state, work, execution


def _run_cli(monkeypatch, tmp_path, state, work, execution, comments=(),
             review_outcome="success") -> int:
    comments_file = tmp_path / "comments.json"
    comments_file.write_text(json.dumps(list(comments)))
    monkeypatch.setenv("EXECUTION_FILE", str(execution))
    monkeypatch.setenv("REVIEW_OUTCOME", review_outcome)
    monkeypatch.setenv("MODEL_ALIAS", "opus")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return dr.main(["verdict", "--state-dir", str(state), "--work-dir", str(work),
                    "--comments-json", str(comments_file), "--no-post"])


CLEAN_REPORT = f"## review-doc: docs/a.md @ {HEAD}\n\n**Mismatches (0):** none\n"


def test_cli_drift_key_mismatch_fails_the_check(monkeypatch, tmp_path):
    """AC15: a stored key that does not match fails the run, even with no finding."""
    state, work, execution = _state(
        tmp_path, FIXTURE_CALIBRATION.read_text(encoding="utf-8"), findings=[],
        report=CLEAN_REPORT)
    assert _run_cli(monkeypatch, tmp_path, state, work, execution) == 1
    comment = (work / "out" / "comment.md").read_text(encoding="utf-8")
    assert "drift: model changed (claude-opus-4-1-20250805 → claude-opus-5)" in comment
    [block] = dr.parse_blocks([_bot(comment)])
    assert block["status"] == "drift" and block["model"] == MODEL


def test_cli_matching_key_and_no_blocking_passes(monkeypatch, tmp_path):
    key = dr.drift_key(workflow=WORKFLOW.encode(), action=dr.action_sha(WORKFLOW), model=MODEL,
                       skill=(ROOT / dr.SKILL_PATH).read_bytes())
    state, work, execution = _state(tmp_path, dr.format_key_line(key, MODEL) + "\n", findings=[],
                                    report=CLEAN_REPORT)
    assert _run_cli(monkeypatch, tmp_path, state, work, execution) == 0
    assert "calibrated: drift key matches" in (work / "out" / "comment.md").read_text("utf-8")


def test_calibration_record_is_outside_the_hashed_path(monkeypatch, tmp_path):
    """#106: recording results in CALIBRATION.md must not change the key they record. The same
    key line under two different records is calibrated both times."""
    key = dr.drift_key(workflow=WORKFLOW.encode(), action=dr.action_sha(WORKFLOW), model=MODEL,
                       skill=(ROOT / dr.SKILL_PATH).read_bytes())
    line = dr.format_key_line(key, MODEL) + "\n"
    records = ("", "# results\n\n| class | caught |\n|---|---|\n| fact | 3 |\n")
    for n, record in enumerate(records):
        run = tmp_path / str(n)
        run.mkdir()
        state, work, execution = _state(run, record + line, findings=[], report=CLEAN_REPORT)
        assert _run_cli(monkeypatch, run, state, work, execution) == 0, record
        comment = (work / "out" / "comment.md").read_text("utf-8")
        assert "calibrated: drift key matches" in comment, record
        [block] = dr.parse_blocks([_bot(comment)])
        assert block["drift_key"] == key, record


def test_cli_uncalibrated_reports_and_blocking_from_an_earlier_run_still_fails(monkeypatch,
                                                                                 tmp_path):
    state, work, execution = _state(tmp_path, "# no key\n", findings=[], report=CLEAN_REPORT)
    earlier = _block_comment(HEAD, {"docs/a.md": "full"}, [_f()])
    assert _run_cli(monkeypatch, tmp_path, state, work, execution, [earlier]) == 1
    comment = (work / "out" / "comment.md").read_text(encoding="utf-8")
    assert "uncalibrated" in comment and "1 blocking finding(s) open" in comment
    assert "`docs/a.md:3` quote" in comment


def test_cli_unresolved_model_is_unreviewable(monkeypatch, tmp_path):
    state, work, execution = _state(tmp_path, "# no key\n", findings=[], report=CLEAN_REPORT,
                                    usage={"claude-sonnet-5": {}})
    assert _run_cli(monkeypatch, tmp_path, state, work, execution) == 1
    comment = (work / "out" / "comment.md").read_text(encoding="utf-8")
    assert "unreviewable: cannot resolve the 'opus' model" in comment
    assert "**Rounds (full reviews on distinct commits):** 0" in comment


# --- run stats: cost, duration, turns (#145) --------------------------------

RUN_STATS = {"total_cost_usd": 1.23456, "duration_ms": 754_321, "num_turns": 87}
STAT_LINES = ("**Cost (USD):** 1.2346", "**Duration:** 12m 34s", "**Turns:** 87")
UNKNOWN_LINES = ("**Cost (USD):** unknown", "**Duration:** unknown", "**Turns:** unknown")


def _matching_key_line() -> str:
    key = dr.drift_key(workflow=WORKFLOW.encode(), action=dr.action_sha(WORKFLOW), model=MODEL,
                       skill=(ROOT / dr.SKILL_PATH).read_bytes())
    return dr.format_key_line(key, MODEL) + "\n"


def test_run_stats_come_from_the_last_result_message():
    stats = dr.run_stats([{"type": "assistant"}, _result({MODEL: {}}, total_cost_usd=0.5),
                          _result({MODEL: {}}, **RUN_STATS)])
    assert stats == {"cost_usd": 1.23456, "duration_ms": 754_321, "turns": 87}
    assert dr.run_stats([]) == {"cost_usd": None, "duration_ms": None, "turns": None}
    assert dr.run_stats(["not a dict", {"type": "result"}]) == \
        {"cost_usd": None, "duration_ms": None, "turns": None}


@pytest.mark.parametrize("value", ["1.5", None, True, -1, [1]])
def test_run_stats_reject_a_non_number(value):
    stats = dr.run_stats([_result({MODEL: {}}, total_cost_usd=value, duration_ms=value,
                                  num_turns=value)])
    assert stats == {"cost_usd": None, "duration_ms": None, "turns": None}


def test_duration_is_rendered_in_minutes_and_seconds_or_hours():
    assert dr.format_duration(0) == "0m 00s"
    assert dr.format_duration(754_321) == "12m 34s"
    assert dr.format_duration(3_600_000) == "1h 00m 00s"
    assert dr.format_duration(5_025_499) == "1h 23m 45s"


def test_stats_section_says_which_fields_are_unknown():
    full = dr.render_stats_section({"cost_usd": 1.23456, "duration_ms": 754_321, "turns": 87})
    assert full.startswith("## Run\n\n") and all(line in full for line in STAT_LINES)
    assert "unknown" not in full
    partial = dr.render_stats_section({"cost_usd": None, "duration_ms": 754_321, "turns": None})
    assert "**Cost (USD):** unknown" in partial and "**Duration:** 12m 34s" in partial
    assert "The execution file has no usable `total_cost_usd`, `num_turns`; the field reads unknown." \
        in partial


def test_cli_report_and_comment_carry_cost_duration_and_turns(monkeypatch, tmp_path):
    """#145 AC1: the three values from the execution file land in the comment and the report."""
    state, work, execution = _state(tmp_path, _matching_key_line(), findings=[],
                                    report=CLEAN_REPORT, **RUN_STATS)
    assert _run_cli(monkeypatch, tmp_path, state, work, execution) == 0
    comment = (work / "out" / "comment.md").read_text(encoding="utf-8")
    report = (work / "out" / "report.md").read_text(encoding="utf-8")
    for line in STAT_LINES:
        assert line in comment and line in report, line
    assert report.startswith(CLEAN_REPORT) and "\n## Run\n" in report
    assert "unknown" not in comment and "unknown" not in report
    # The stats sit in the header, before the report and the state block.
    assert comment.index("**Turns:** 87") < comment.index("---") < comment.index("<!-- doc-review:")


def test_cli_missing_cost_fields_read_unknown_and_the_run_still_gets_a_verdict(monkeypatch,
                                                                                tmp_path):
    """#145 AC2: an execution file without the cost field still produces a verdict."""
    state, work, execution = _state(tmp_path, _matching_key_line(), findings=[],
                                    report=CLEAN_REPORT)
    assert _run_cli(monkeypatch, tmp_path, state, work, execution) == 0
    comment = (work / "out" / "comment.md").read_text(encoding="utf-8")
    report = (work / "out" / "report.md").read_text(encoding="utf-8")
    assert "calibrated: drift key matches" in comment
    for line in UNKNOWN_LINES:
        assert line in comment and line in report, line
    assert "has no usable `total_cost_usd`, `duration_ms`, `num_turns`; the field reads unknown." \
        in report
    [block] = dr.parse_blocks([_bot(comment)])
    assert block["status"] == "pass"


def test_cli_stats_are_recorded_even_when_the_review_step_failed(monkeypatch, tmp_path):
    """The cost of a failed run is still part of the record: the stats are read outside the
    verdict path, so an unreviewable run reports them."""
    state, work, execution = _state(tmp_path, _matching_key_line(), findings=[],
                                    report=CLEAN_REPORT, **RUN_STATS)
    assert _run_cli(monkeypatch, tmp_path, state, work, execution, review_outcome="failure") == 1
    comment = (work / "out" / "comment.md").read_text(encoding="utf-8")
    assert "unreviewable: review step failure" in comment
    for line in STAT_LINES:
        assert line in comment, line


def test_cli_unreadable_execution_file_is_unreviewable_with_unknown_stats(monkeypatch, tmp_path):
    state, work, execution = _state(tmp_path, _matching_key_line(), findings=[],
                                    report=CLEAN_REPORT)
    execution.write_text("not json", encoding="utf-8")
    assert _run_cli(monkeypatch, tmp_path, state, work, execution) == 1
    comment = (work / "out" / "comment.md").read_text(encoding="utf-8")
    assert "unreviewable: the execution file is empty or not a JSON list of messages" in comment
    for line in UNKNOWN_LINES:
        assert line in comment, line


# --- workflow contract ----------------------------------------------------


def test_trigger_is_pull_request_and_dispatch_never_pull_request_target():
    on = WORKFLOW.split("\non:\n")[1].split("\npermissions:")[0]
    assert re.findall(r"^  (\w+):", on, re.M) == ["pull_request", "workflow_dispatch"]
    assert "pull_request_target" not in WORKFLOW
    assert "converted_to_draft" in on and "ready_for_review" in on


def test_no_paths_filter_on_the_trigger():
    on = WORKFLOW.split("\non:\n")[1].split("\npermissions:")[0]
    assert not re.search(r"^\s+paths(-ignore)?:", on, re.M)


def test_permissions_are_exactly_contents_read_and_pull_requests_write():
    perms = re.search(r"^permissions:\n((?:  .*\n)+)", WORKFLOW, re.M).group(1)
    assert sorted(perms.split()) == sorted(["contents:", "read", "pull-requests:", "write"])
    assert WORKFLOW.count("permissions:") == 1


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert len(uses) == 4
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_oauth_token_reaches_the_review_step_only():
    assert WORKFLOW.count("secrets.") == 1
    assert "secrets.CLAUDE_CODE_OAUTH_TOKEN" in _step("Review")


def test_no_event_data_is_interpolated_into_run_bodies():
    for step in _steps():
        run = re.search(r"^        run: (.*(?:\n(?: {10}.*|\s*))*)", step, re.M)
        if run:
            assert "${{" not in run.group(1), step
    assert "github.event" not in _step("Review").split("prompt: |", 1)[1]


def test_model_is_passed_as_the_alias_the_verdict_resolves():
    assert re.search(r"^  MODEL_ALIAS: opus$", WORKFLOW, re.M)
    assert "--model ${{ env.MODEL_ALIAS }}" in _step("Review")


def test_classify_runs_before_the_review_and_verdict_runs_the_saved_copy():
    names = re.findall(r"^      - (?:name|uses): (.+)$", WORKFLOW, re.M)
    assert names.index("Classify") < names.index("Review") < names.index("Verdict")
    assert "python scripts/doc_review.py classify" in _step("Classify")
    assert 'python "$RUNNER_TEMP/doc-review-state/doc_review.py" verdict' in _step("Verdict")


def test_review_posts_nothing_and_cannot_run_gh():
    review = _step("Review")
    tools = re.search(r'--allowedTools "([^"]+)"', review).group(1).split(",")
    assert not [t for t in tools if t == "Bash" or "gh " in t or "curl" in t or "python" in t]
    assert 'classify_inline_comments: "false"' in review


def test_concurrency_is_per_pr_and_cancels_in_progress():
    assert "group: doc-review-${{ github.event.pull_request.number" in WORKFLOW
    assert "cancel-in-progress: true" in WORKFLOW


def test_rounds_is_a_job_output():
    assert "rounds: ${{ steps.verdict.outputs.rounds }}" in WORKFLOW


def test_workflow_file_is_the_one_the_drift_key_hashes():
    assert (ROOT / dr.WORKFLOW_PATH).read_text(encoding="utf-8") == WORKFLOW
