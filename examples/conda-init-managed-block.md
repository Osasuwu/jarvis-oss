---
fit: works when you want a widely deployed installer that installs, updates and removes its own lines in a file it does not own, with the rough edges visible in its own source
source: https://github.com/conda/conda/blob/main/conda/core/initialize.py
verified: 2026-09-17
claim: conda init keeps its shell-profile edits between fixed markers, replaces that region on every run, and removes it with --reverse
pairs_with: docs/writing-into-user-owned-files.md
---

# `conda init`: a managed block in someone else's shell profile

`conda init` has to make `conda activate` work in a shell profile the user already maintains.
It writes a block like this into `~/.bashrc`:

```
# >>> conda initialize >>>
# !! Contents within this block are managed by 'conda init' !!
...
# <<< conda initialize <<<
```

PowerShell profiles get `#region conda initialize` … `#endregion` instead.

**Re-run.** `initialize.py` finds the block with a regex (`CONDA_INITIALIZE_RE_BLOCK`) and puts
fresh content in its place. It appends a new block only
`if "# >>> conda initialize >>>" not in rc_content`. Anything a user typed between the markers is
replaced, which is what the "managed by" line warns about.

**Uninstall.** `conda init --reverse`, whose help text is "Undo effects of last conda init."
([main_init.py](https://github.com/conda/conda/blob/main/conda/cli/main_init.py)). It removes the
sections and uncomments lines that an earlier run had commented out.

**Preview.** `--dry-run` will "Only display what would have been done". With `--verbose` it also
prints a diff of each file.

**Rough edges, in its own source:**

- It edits outside its block too. Older conda lines it recognises are commented out and tagged
  `# commented out by conda initialize`, which is why `--reverse` has to uncomment them.
- Duplicate blocks are not merged away:
  `# TODO: maybe remove all but last of replace_str, if there's more than one occurrence`.
