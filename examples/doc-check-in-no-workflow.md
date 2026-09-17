---
fit: works when you have a doc-shape check in the repo and want to see what happens when nothing runs it — a working, well-designed check that enforced nothing for four weeks
source: https://github.com/dc0sk/OpenPulseHF/issues/1349
verified: 2026-09-17
claim: a frontmatter check whose only calling workflow was disabled accumulated 40 new offenders in four weeks, and the issue concludes that without a job running it the count returns
pairs_with: docs/doc-structure-gate.md
---

# A doc-frontmatter check that ran in no workflow

OpenPulseHF had a frontmatter checker, `scripts/validate-doc-frontmatter.sh`, with a sensible
design: a baseline file recorded 71 existing offences on 2026-08-15, and the check failed only on
new ones. The issue that found the problem is titled "The doc-frontmatter check runs in no
workflow, and accumulated 40 new offenders in four weeks".

**Why nothing ran it.**

- `docs.yml`, "the only workflow that calls the script", was `disabled_manually`.
- The local gate script did not call it either, "so a local green gate says nothing about it."
- The repo's agent instructions recorded the situation and said to run it by hand. "In practice
  nobody did, which is how this was found."

**What piled up.** 40 new offences, every one in a doc written after the baseline: 39 with no
frontmatter block at all (37 of them review records) and one with an illegal `status` whose value
was also stale. "Three of the 39 were committed this week, so this is a live rate, not a
historical backlog."

**The conclusion.** The backlog was fixed in a pull request, but the issue separates that from the
real hole: "Without a job that runs the check, the count returns." It lists three places the
check could run — re-enable the workflow, add a step to a workflow that already runs on every pull
request, or add it to a local gate that "does not run at merge" — and leaves the choice open.

**What to take from it.** A check file in the repo, and an instruction to run it, are option 1 of
[`doc-structure-gate.md`](../docs/doc-structure-gate.md) with extra steps. A check enforces
something only while a required job runs it on every pull request — and the baseline design shows
that a good check and an enforced check are separate properties.
