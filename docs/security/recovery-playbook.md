# Recovery Playbook

How to recover from common agent failures.

## Agent broke a file

```bash
# Restore specific file from main
git checkout main -- path/to/file

# Or revert entire commit
git revert <commit-hash>
```

## Agent corrupted memory

```bash
# If memory was overwritten with bad content — soft delete was implemented,
# but overwrite (store) doesn't create a backup of the old version.
# Check audit_log for what changed:
```

```sql
SELECT * FROM audit_log
WHERE tool_name = 'memory_store' AND target = '<memory_name>'
ORDER BY timestamp DESC LIMIT 5;
```

If memory was deleted: use `memory_restore(name=..., project=...)` within 30 days.

## Agent created bad PR

```bash
# Close PR without merging
gh pr close <N> --repo Osasuwu/jarvis

# Delete the remote branch
git push origin --delete feat/<N>-<slug>

# Delete local branch
git branch -D feat/<N>-<slug>
```

## Agent committed to wrong branch

```bash
# Note the commit hash
git log --oneline -5

# Switch to correct branch and cherry-pick
git checkout main && git pull
git checkout -b feat/<N>-correct-branch
git cherry-pick <commit-hash>

# Remove from wrong branch
git checkout wrong-branch
git reset --hard HEAD~1  # only if not pushed
# If pushed: revert instead
git revert <commit-hash>
```

## Agent left uncommitted changes

```bash
# Check what's there
git status
git diff

# If good — commit it
git add <files> && git commit -m "complete agent work"

# If bad — discard
git checkout -- .
```

## Agent created merge conflict

```bash
# See what's conflicting
git status

# For simple conflicts: resolve manually
# For complex conflicts: abort and retry
git merge --abort  # or git rebase --abort

# Start fresh
git checkout main && git pull
git checkout -b feat/<N>-retry
```

## Supabase data issue

```sql
-- Check recent audit log
SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 20;

-- Find soft-deleted memories
SELECT name, project, deleted_at FROM memories WHERE deleted_at IS NOT NULL;

-- Restore a specific memory
UPDATE memories SET deleted_at = NULL WHERE name = '<name>' AND project = '<project>';

-- Manual cleanup of old soft-deleted
SELECT cleanup_soft_deleted_memories(30);
```
