---
fit: works when checking whether an include-based write actually took effect
last_seen: 2026-09-17
pairs_with: docs/writing-into-user-owned-files.md
---

# `git config --includes`: a missing include target is silent

`include.path` pointing at a file that does not exist is not an error. Reproduced directly
(git 2.44.0):

```
$ git config -f main.cfg include.path ./does-not-exist.cfg
$ git config -f main.cfg testsection.testkey testvalue
$ git config -f main.cfg --includes --get testsection.testkey
testvalue
$ echo "exit code: $?"
exit code: 0
```

The other keys in `main.cfg` are returned normally; nothing reports that the included file is
missing. This is why the doc's option 4 says the check that belongs with an include is not
"is the line present" but "did the content load."
