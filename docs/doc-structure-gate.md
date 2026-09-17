---
applies_when: docs in your repo follow a required shape — frontmatter keys, sections, a size limit, links to companion files — agents or several people write them, and you want a pull request that breaks the shape to fail instead of relying on someone to notice
applies_when_not: a handful of free-form docs written by one person who reviews them by eye; a site that only needs its links to work (a link checker alone covers that); whether a person actually read a doc is docs/publishing-discipline.md, not a shape check
signed_off:
---

# Making a pull request fail when a doc breaks its required shape

## The problem

A template tells the writer what a doc should contain. Nothing about a template stops a doc that
ignores it from merging, and agents drift from templates the way people do: a missing key, a
renamed section, a link to a file that was never written. A check that fails the pull request
closes that gap, and brings its own ways to go wrong:

- **Runs nowhere** — the script is in the repo, but no workflow calls it, or the workflow was
  disabled, and offenders pile up.
- **Advisory only** — the check runs and goes red, but it is not required, so the pull request
  merges anyway.
- **Shape, not substance** — every required key is present and says nothing; a rule about commits
  is met by splitting commits.
- **One file at a time** — most linters see a single file, so "every doc has an example" cannot
  be expressed.
- **Whole-tree red** — the check scans everything, so one bad merge fails every later pull
  request and invites the fastest fix, not the right one.
- **Template drift** — the template changes and the check does not, or the reverse.
- **The writer edits the check** — the pull request that breaks the shape also loosens the rule.

Each option is marked **tried** (we run or ran it; the example says where) or **sourced** (read
from the tool's documentation). Quotes were checked against the linked pages on 2026-09-17, in
two review runs on the pull request that added this doc.

## The options

### 1. A template and instructions, no check

**How it works.** A template file or an agent skill describes the shape; the writer follows it
and a reviewer compares. Our [`write-doc`](../.agents/skills/write-doc/SKILL.md) skill is this,
and [log4brains](https://github.com/thomvaill/log4brains) generates architecture decision records
from a "Customizable template (default: MADR)"; its README describes no validation. Editor
extensions such as Front Matter CMS added "Schema and validation for front matter in markdown
files" ([v10.10.0](https://frontmatter.codes/updates/v10.10.0)) — in the editor, not in CI.

**Best pick when** docs are few, one person reviews all of them, or the shape is still changing
weekly.

**Cost.** Free to start. Every miss is found by a reader, if at all.

**Lifecycle.** A file to keep current. Status: tried — `write-doc` is how our docs are drafted;
the shape it asks for beyond frontmatter (sections, option fields) is checked by nobody but
review.

### 2. A JSON Schema for frontmatter

**How it works.** A schema declares required keys, types, enums and lengths; a tool extracts each
doc's frontmatter and validates it.
[remark-lint-frontmatter-schema](https://github.com/JulianCataldo/remark-lint-frontmatter-schema)
does it as a remark-lint rule, mapping schemas to files by glob;
[frontmatter-json-schema-action](https://github.com/mheap/frontmatter-json-schema-action) does it
as a GitHub Action. MDN runs its own Ajv script against a schema whose `required` list is
`"title", "slug", "page-type"` ([linter](https://github.com/mdn/content/blob/main/scripts/front-matter_linter.js)).

**Best pick when** frontmatter is the contract and you want one schema file any language can read.

**Cost.** Small. Covers keys only: no sections, no size, nothing across files. The small Actions
have few users; check they are maintained before depending on them.

**Lifecycle.** A schema file plus a CI job. Status: sourced.

### 3. A purpose-built frontmatter linter

**How it works.** A tool with ready rules instead of a schema. Giant Swarm's
[frontmatter-validator](https://github.com/giantswarm/frontmatter-validator) checks, for example,
`REVIEW_TOO_LONG_AGO` — "the `last_review_date` is older than the expiration period (default 365
days…)" — and runs as a pre-commit hook. [flint](https://github.com/hay-kot/flint) has required
fields, regexes, enums, dates, lengths and "Asset Existence", which checks that a path named in a
field exists.

**Best pick when** you want a freshness or review-date rule without writing code.

**Cost.** Low to adopt. Giant Swarm's rules are "designed for Giant Swarm requirements" — its
checks name their own fields (`NO_LAST_REVIEW_DATE`), and its README does not say the field names
are configurable, so a different key may have no rule at all; flint says "This library is still
experimental."

**Lifecycle.** A binary or hook plus config. Status: sourced.

### 4. A schema for the whole document

**How it works.** One config declares frontmatter, the required headings in order, a word range
and link checks. [mdschema](https://github.com/jackchuka/mdschema) has `structure`,
`frontmatter`, `word_count` and `validate_files: true # Check relative file links (./file.md)`, and can
generate the template from the same schema, so template and check cannot drift. The
[Structured MADR action](https://smadr.dev/reference/github-action/) checks decision records'
frontmatter against a JSON Schema and their required sections in order.

**Best pick when** every doc of a kind has the same headings — decision records, runbooks,
templated guides.

**Cost.** One config per doc type. mdschema is young and small; weigh that. Checks that a link
resolves, not that a required companion exists.

**Lifecycle.** A config file plus a CI job. Status: sourced.

### 5. The site generator's own content schema

**How it works.** If the docs are built into a site, the build can refuse bad content. Astro
content collections validate frontmatter with Zod — "If any file violates its collection schema,
Astro will provide a helpful error" — and "With the `reference()` function, you can define a
property in a collection schema as an entry from another collection"
([Astro](https://docs.astro.build/en/guides/content-collections/)).
[Velite](https://velite.js.org/guide/introduction) does the same with Zod for "any JavaScript
framework or library", with no site needed. [Markdoc](https://markdoc.dev/docs/validation)
validates tags and attributes inside the body against its schema. Other
generators check links rather than frontmatter: Docusaurus `onBrokenLinks` defaults to throwing
([config](https://docusaurus.io/docs/api/docusaurus-config)) and MkDocs has `--strict`
([configuration](https://www.mkdocs.org/user-guide/configuration/)). Hugo is in between: a
template that calls [`errorf`](https://gohugo.io/functions/fmt/errorf/) "prints the result to the
ERROR log and fails the build", so a required key can be checked in the layout you already build
with — the documented example fails on a missing shortcode argument.

**Best pick when** you already build the docs with Astro, Velite or Markdoc, or with Hugo and can
write the rule into a layout. Docusaurus and MkDocs are option 7 in practice.

**Cost.** No extra tool, but every pull request needs a build in CI. A `reference()` says an
example points at a doc; it does not say every doc has an example. A collection schema validates
frontmatter, not links.

**Lifecycle.** Part of the site config. Status: sourced. Dropped on merit: Contentlayer — its
README says it "is no longer maintained due to lack of funding".

### 6. A markdown or prose linter

**How it works.** markdownlint's
[MD043](https://github.com/DavidAnson/markdownlint/blob/main/doc/md043.md) "can be used to enforce
a standard heading structure for a set of files"; custom rules see frontmatter lines but are
"called once for each file/string input"
([custom rules](https://github.com/DavidAnson/markdownlint/blob/main/doc/CustomRules.md)).
[Vale](https://docs.vale.sh/formats/front-matter) can target frontmatter fields, but "Only
string-valued fields are linted"; its `metric` check can cap words, and its
[`occurrence`](https://docs.vale.sh/checks/occurrence) check with `min: 1` can require a token.

**Best pick when** you already lint prose and want required headings, a required phrase or a
word limit in the same run.

**Cost.** One heading list per config. Keys are awkward: Vale sees string values only. Custom
rules run per file, so a cross-file rule means writing code (8).

**Lifecycle.** Config plus a CI step or hook. Status: sourced.

### 7. A link checker

**How it works.** [lychee](https://github.com/lycheeverse/lychee) with `--offline` will "Only check
local files and block network requests"; [remark-validate-links](https://github.com/remarkjs/remark-validate-links) checks
"that markdown links and images point to existing local files and headings in a Git repo".

**Best pick when** broken relative links are the main failure, alongside any option above.

**Cost.** Cheap. It proves a link resolves, never that a required link is there.

**Lifecycle.** A CI step. Status: sourced; our gate does its own relative-link check.

### 8. A custom script in CI

**How it works.** Code that parses the files and applies whatever rules you need, run as a
required check. GitHub Docs builds custom rules on markdownlint, including "GHD012: Frontmatter
must conform to the schema" and a cross-file one, "GHD063: Children frontmatter paths must exist"
([content linter](https://docs.github.com/en/contributing/collaborating-on-github-docs/using-the-content-linter)).
Kubernetes' [`verify-toc-vs-template.sh`](https://github.com/kubernetes/enhancements/blob/master/hack/verify-toc-vs-template.sh)
diffs each changed proposal's headings against the template's, so the template is the rule — but
it ends `# TODO(soltysh): for now this should not fail, but print problems` and `exit 0`, which is
this doc's "Advisory only" failure mode written into the script.
[Danger JS](https://danger.systems/js/) adds pull-request-level rules, such as failing when a doc
changes and its companion file does not. [conftest](https://www.conftest.dev/options/)
`--combine` hands all parsed files to one Rego policy: a cross-file rule without a parser of your
own, once frontmatter is extracted to YAML. Ours is [`structure_gate.py`](../tests/structure_gate.py): required
frontmatter keys, a 20000-byte cap, relative links that must resolve, `pairs_with` targets that
must exist, example provenance and staleness, and the sign-off ledger rules.

**Best pick when** you need a rule across files — pairings, directory-dependent rules — or a
rule no tool above has.

**Cost.** You write, test and maintain a parser. Ours reads only flat `key: value` frontmatter.

**Lifecycle.** Code with its own tests. Status: tried — this repo, required on `main`.

### 9. A model reviews the doc as a check

**How it works.** A prompt says what a good doc contains, and an agent reads each pull request's
docs and reports a status check. [Continue checks](https://github.com/continuedev/checks) are
markdown prompts — the agent will "create tailored check files in `.checks/`" — run on each pull
request by the workflow it ships. It is the only option aimed at
substance: a key that is present and says nothing.

**Best pick when** shape passes and content still fails, and a person reads what it flags.

**Cost.** Model calls on every pull request; answers vary between runs, so a red check can be
wrong, and text in the doc can steer the model that reads it.

**Lifecycle.** A prompt file plus a CI job. Status: sourced. Not ours, on fit: a person reviews
substance here ([`publishing-discipline.md`](publishing-discipline.md)).

## What every option depends on

A check blocks nothing unless it runs on every pull request as a **required** status check. One
project's frontmatter script sat in the repo while its only workflow was disabled; the issue that
found it is titled "The doc-frontmatter check runs in no workflow, and accumulated 40 new
offenders in four weeks", and says "Without a job that runs the check, the count returns"
([`doc-check-in-no-workflow.md`](../examples/doc-check-in-no-workflow.md)). A pre-commit hook
alone never counts: it can be skipped — but the same config runs in CI, since "adding
`pre-commit run --all-files` as a CI step will ensure everything stays in tip-top shape"
([pre-commit](https://pre-commit.com/)), and that job can be required. On a self-hosted forge a server-side `pre-receive` hook can
reject the push itself.

Required checks need branch protection or rulesets. On GitHub Free, private repositories have
neither, so every option there reports and nothing blocks — and the review hold that replaces it
is itself advisory, because a draft or a label only holds while whoever merges respects it
([`publishing-discipline.md`](publishing-discipline.md)). Two cheap exits: GitHub Pro lists
"Protected branches" among its tools for private repositories, and a public repository gets both
protected branches and rulesets on Free. And if the pull request under check can also edit the
check, only review stops it — see "At more than one developer".

## How to choose

Most options below read markdown: 2, 3, 4 and 7's tools, and 6's MD043. For reStructuredText or
AsciiDoc, what is left is Vale (6), a script (8), a model check (9), review alone (1), and the
generator's own build (5) — for Sphinx that is `-W`, which will "Turn warnings into errors …
exits with exit status 1 if any warnings are generated"
([sphinx-build](https://www.sphinx-doc.org/en/master/man/sphinx-build.html)). Answer steps 3 and 4
accordingly.

First, in order:

1. **Can a check be required on your repo?** No (a private repo on GitHub Free) → any option still
   reports, but nothing below blocks a merge; a review hold is the fallback, and it holds only as
   far as whoever merges respects it. Paying for Pro, or making the repo public, turns this to
   yes.
2. **Do you need a rule across files?** "Every doc has an example" → 8 (a script, or conftest on
   extracted frontmatter). "This field names an entry" → also 5's `reference()`, if you build with
   Astro. "This field names a file" → also flint's Asset Existence (3).
3. **Do docs of a kind share fixed headings?** Yes → 4, or 6's MD043 if you lint already. mdschema
   (4) also covers keys, word count and links; the Structured MADR action covers keys and
   sections only.
4. **Does a build already validate content?** Astro, Velite, Markdoc → 5. Hugo → 5 too, if you
   will write the rule into a layout with `errorf`; otherwise its link checking, and MkDocs' and
   Docusaurus', counts as 7 and the keys come from 2 or 3.

| Option | Fits only if |
|---|---|
| 1 | docs are few and one person reviews all of them, or the shape still changes weekly |
| 2 | keys, types and enums are the part you want declared (pair it for headings) |
| 3 | its built-in list covers your key rules — review dates; fields, dates, file paths |
| 4 | each doc type has one fixed heading structure |
| 5 | the docs build with Astro, Velite or Markdoc, or with Hugo and a layout you will edit, and the build runs on pull requests |
| 6 | you already run markdownlint or Vale, and it covers the headings, phrase or word count half |
| 7 | combined with another option — links only |
| 8 | you need a rule no tool above has, and someone maintains it; a cross-file rule alone can be conftest `--combine` over `yq --front-matter=extract` output, with no parser of your own |
| 9 | a person reads what it flags; never the only required check |

Rows are not exclusive: 2 or 3 for keys plus 6's MD043 for headings is a normal pair, and so is
3 plus a generator's `--strict`. If several are left, list your rules and count what each covers: mdschema (4) takes keys,
headings, size and links in one config; 5 adds no tool if the build already runs; 8 takes
anything and costs a parser; 2 and 3 take keys only. Add 7 unless the option already checks
links (mdschema, Docusaurus, MkDocs `--strict`). If nothing fits, keep 1 and put the shape rules
in the review checklist.

**At more than one developer.** One owner keeps the template, and the check changes in the same
pull request as the template it encodes. The rest of this is about who can edit the check rather
than headcount, and applies just as much to one developer whose agent opens every pull request.
Make the check required and apply it to administrators, or someone will merge around it. Then:
code owners on the check files, a ruleset that restricts those paths, an organization ruleset
that requires a workflow kept in another repo, or `pull_request_target`, which runs the workflow
from the base repository's **default** branch — not the branch the pull request targets — and
under which you must never run the pull request's code. All four are GitHub. Rulesets themselves
are free on a public repository, but restricting paths is a *push* ruleset, "available for the
GitHub Team plan in internal and private repositories", and the organization ruleset that requires
a workflow from another repo is documented only for Enterprise Cloud
([available rules](https://docs.github.com/en/enterprise-cloud@latest/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets));
GitLab's code owners are "Premium, Ultimate". So on a personal account, code owners and those two
rulesets are all out: `pull_request_target` and review are what is left. On GitLab Free, review
alone stands between a pull request and the check it fails. Check only changed files —
[changed-files](https://github.com/tj-actions/changed-files) lists them,
[reviewdog](https://github.com/reviewdog/reviewdog) `-filter-mode` filters findings — or, when
adopting a check on a tree that already fails it, record a baseline and fail only on new offences
([Betterer](https://phenomnomnominal.github.io/betterer/docs/introduction)). With several open
pull requests, a whole-tree scan turns one bad merge into everyone's red check.

**Examples.** An Astro docs site: 5 — collection schemas for keys, `reference()` from examples to
docs — plus 7 for links; this points away from our choice. A MkDocs site with owner and
review-date keys: `--strict` for links, 3 for the review date. A team writing decision
records to a fixed template: 4 (the Structured MADR action or mdschema) plus 7. A plain folder
where only frontmatter matters: 2 or 3, plus 7. A repo that stopped running its check:
[`doc-check-in-no-workflow.md`](../examples/doc-check-in-no-workflow.md).

**Our own choice.** Plain markdown, no site build, and rules that span files (`pairs_with`, the
sign-off ledger), so 8, with 1 for everything the script does not check. It costs a 317-line
script and its tests. Not taken yet: 4 or MD043 for headings, which would close the first gap
below; 9, since a person reviews substance. Gaps: sections and option fields are not checked, so a doc can drop "How to
choose" and pass; nothing checks that every doc has an example and a resource — `pairs_with`
points from example to doc, not back; the ledger rule was met by splitting commits with nothing
read ([`signoff-same-commit-violation.md`](../examples/signoff-same-commit-violation.md)); the
scan is whole-tree, which is how one squash merge turned `main` red; and a pull request can edit
the gate, which only the review hold catches
([`publishing-discipline.md`](publishing-discipline.md)). The gate and how to run it:
[`structure-gate.md`](../resources/structure-gate.md).
