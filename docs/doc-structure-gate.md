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
from the tool's documentation). Quotes were checked on 2026-09-17.

## The options

### 1. A template and instructions, no check

**How it works.** A template file or an agent skill describes the shape; the writer follows it
and a reviewer compares. Our [`write-doc`](../.agents/skills/write-doc/SKILL.md) skill is this,
and [log4brains](https://github.com/thomvaill/log4brains) generates architecture decision records
from a "Customizable template (default: MADR)" with no validation step.

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

**Cost.** Low to adopt. Giant Swarm's rules are "designed for Giant Swarm requirements"; flint
says "This library is still experimental."

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
[Velite](https://velite.js.org/guide/introduction) does the same with Zod for any stack. Other
generators check links rather than frontmatter: Docusaurus `onBrokenLinks` defaults to throwing
([config](https://docusaurus.io/docs/api/docusaurus-config)), MkDocs has `--strict`
([configuration](https://www.mkdocs.org/user-guide/configuration/)), Hugo fails the build on a
template `errorf` ([errorf](https://gohugo.io/functions/fmt/errorf/)).

**Best pick when** you already build the docs with one of these.

**Cost.** No extra tool, but every pull request needs a site build in CI. A `reference()` says an
example points at a doc; it does not say every doc has an example.

**Lifecycle.** Part of the site config. Status: sourced. Dropped on merit: Contentlayer — its
README says it "is no longer maintained due to lack of funding".

### 6. A markdown or prose linter

**How it works.** markdownlint's
[MD043](https://github.com/DavidAnson/markdownlint/blob/main/doc/md043.md) "can be used to enforce
a standard heading structure for a set of files"; custom rules see frontmatter lines but are
"called once for each file/string input"
([custom rules](https://github.com/DavidAnson/markdownlint/blob/main/doc/CustomRules.md)).
[Vale](https://docs.vale.sh/formats/front-matter) can target frontmatter fields, but "Only
string-valued fields are linted", and its `metric` check can cap words.

**Best pick when** you already lint prose and want required headings or a word limit in the same
run.

**Cost.** One heading list per config. Not built to require that a key exists. No cross-file
rules.

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
[Danger JS](https://danger.systems/js/) adds pull-request-level rules such as "a change to this doc
must also touch that file". Ours is [`structure_gate.py`](../tests/structure_gate.py): required
frontmatter keys, a 20000-byte cap, relative links that must resolve, `pairs_with` targets that
must exist, example provenance and staleness, and the sign-off ledger rules.

**Best pick when** you need a rule across files — pairings, directory-dependent rules — which no
off-the-shelf linter found expresses in general.

**Cost.** You write, test and maintain a parser. Ours reads only flat `key: value` frontmatter.

**Lifecycle.** Code with its own tests. Status: tried — this repo, required on `main`.

## What every option depends on

A check blocks nothing unless it runs on every pull request as a **required** status check. One
project's frontmatter script sat in the repo while its only workflow was disabled; the issue that
found it is titled "The doc-frontmatter check runs in no workflow, and accumulated 40 new
offenders in four weeks", and says "Without a job that runs the check, the count returns"
([`doc-check-in-no-workflow.md`](../examples/doc-check-in-no-workflow.md)). The same goes for who
can change the rule: if the pull request under check can also edit the check, only review stops it.

## How to choose

First, in order:

1. **Do you build the docs into a site?** Yes → 5, and add rules there before adding a tool.
2. **Do you need a rule across files** (every doc has an example; a field names a real file)?
   Yes → 8, or 5's references if one direction is enough. Per-file tools (2, 4, 6) cannot say it.
3. **Do docs of a kind share fixed headings?** Yes → 4, which also covers keys, size and links.

| Option | Fits only if |
|---|---|
| 1 | you accept that misses are found by readers |
| 2 | keys are the contract; sections and size are not enforced |
| 3 | its built-in rules match yours |
| 4 | each doc type has one fixed heading structure |
| 5 | you build the docs with that generator and run the build on pull requests |
| 6 | you already run the linter; headings or word count are the rule |
| 7 | combined with another option — links only |
| 8 | you will maintain the code and its tests |

Among what is left: 4 is the most coverage for the least code; 8 is the only general answer to
cross-file rules and costs a parser; 2 and 3 are cheap and shallow. Pair any of them with 7 unless
it already checks links. If nothing fits, keep 1 and put the shape rules in the review checklist.

**At more than one developer.** Make the check required and apply it to administrators, or
someone will merge around it. Give the check file and its config an owner (code owners), so a
pull request cannot loosen the rule it is failing without that person. Prefer checking the
pull request's changed files, or keep `main` green at all times: with several open pull requests,
a whole-tree scan turns one bad merge into everyone's red check.

**Examples.** An Astro docs site: 5 — collection schemas for keys, `reference()` from examples to
docs — with no separate linter; this points away from our choice. A team writing decision
records to a fixed template: 4 (the Structured MADR action or mdschema) plus 7. A plain folder
where only frontmatter matters: 2 or 3, plus 7. A repo that stopped running its check:
[`doc-check-in-no-workflow.md`](../examples/doc-check-in-no-workflow.md).

**Our own choice.** Plain markdown, no site build, and rules that span files (`pairs_with`, the
sign-off ledger), so 8, with 1 for everything the script does not check. It costs a 317-line
script and its tests. Gaps: sections and option fields are not checked, so a doc can drop "How to
choose" and pass; nothing checks that every doc has an example and a resource — `pairs_with`
points from example to doc, not back; the ledger rule was met by splitting commits with nothing
read ([`signoff-same-commit-violation.md`](../examples/signoff-same-commit-violation.md)); the
scan is whole-tree, which is how one squash merge turned `main` red; and a pull request can edit
the gate, which only the review hold catches
([`publishing-discipline.md`](publishing-discipline.md)). The gate and how to run it:
[`structure-gate.md`](../resources/structure-gate.md).
