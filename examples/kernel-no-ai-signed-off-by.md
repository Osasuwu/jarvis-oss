---
fit: works when your project records sign-off in commit trailers and you want a published rule for what an AI assistant may and may not sign
source: https://docs.kernel.org/process/coding-assistants.html
verified: 2026-09-17
claim: the Linux kernel forbids AI agents from adding Signed-off-by, requires the human submitter to review and sign, and asks for an Assisted-by trailer instead
pairs_with: docs/publishing-discipline.md
---

# The Linux kernel: agents may not sign, people must

The kernel records who certified a patch in commit trailers. Its guidance for AI coding assistants
draws the line between drafting and signing in writing:

> AI agents MUST NOT add Signed-off-by tags. Only humans can legally certify the Developer
> Certificate of Origin (DCO). The human submitter is responsible for:
> Reviewing all AI-generated code […] Adding their own Signed-off-by tag to certify the DCO […]
> Taking full responsibility for the contribution

Attribution goes in a separate trailer:

```
Assisted-by: LLM [TOOL1] [TOOL2]
```

where the tools are specialised analysers such as `coccinelle` or `sparse`, not git or an editor.
The same page's bug-fixing procedure repeats it: "Do not add a Signed-off-by tag, and add an
Assisted-by tag".

**What makes it work there.** The trailer is not the barrier — any agent that can write a commit
message can write `Signed-off-by:`. What stands behind it is the rest of the kernel's process:
patches go to maintainers, who reply and apply them under their own sign-off. The trailer is a
record (option 6 of [`publishing-discipline.md`](../docs/publishing-discipline.md)); the people
downstream are the proof.

**What to take from it.** Split the two trailers — one for what drafted, one for who is answerable
— and make the rule public, so a reviewer can reject a patch whose only sign-off is plainly not a
person's. It does not stop an agent running as you from signing as you.
