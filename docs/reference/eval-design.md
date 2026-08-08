# Eval design — pull-only reference

Evicted from the always-loaded [`docs/context/invariants.md`](../context/invariants.md) by
[#1418](https://github.com/Osasuwu/jarvis/issues/1418). Four consecutive eval-design facts were being
paid in every session on all three devices, and again after every compaction, to serve the rare
session that actually designs an eval run. Pull this file when planning, scoring, or baselining one.

## Holdout secrecy is unachievable solo

One principal wears every hat — author, scorer, and subject — so a holdout set cannot be kept secret
from the person interpreting it. The defense is not secrecy but **paraphrase regeneration plus paired
scoring per run**: regenerate the scenario wording each run, and score the pair rather than the
absolute.

## The regression unit is a matched pair, not a scenario

A regression is only demonstrated when the **flawed twin draws pushback AND the clean twin does not**.
A single scenario passing or failing says nothing — an agent that pushes back on everything scores
identically to one that reads the flaw.

## The baseline is content-addressed, not scheduled

The baseline key is `hash(paths + model + scenarios)`. PRs compare against their merge-base, not
against a nightly or dated run. A schedule-driven baseline silently compares across model or scenario
changes; a content-addressed one cannot.

## Full eval runs are quota-exclusive on Max x5

A full run needs a fresh 5-hour window and must **never** run concurrently with other Claude use —
concurrent usage distorts the measurement and can exhaust the window mid-run.
