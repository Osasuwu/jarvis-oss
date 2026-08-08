---
title: Workflow change plan — что менять в работе с Claude Code (синтез 7 исследований)
date: 2026-05-18
status: draft
depth: synthesis
inputs:
  - single-agent-workflows-2026-05-18.md
  - single-vs-multi-agent-architecture-2026-05-18.md
  - gh-workflows-solo-vs-team-2026-05-18.md
  - cc-vs-codex-vs-alternatives-2026-05-18.md
  - cheap-models-cost-reduction-2026-05-18.md
  - gap-discovery-patterns-2026-05-18.md
  - session-behavioral-analysis-2026-05-18.md
---

## TL;DR

Workflow **не сломан**. Сломана пропорция: ты переинструментировал человеческую сторону (decisions / reflections / outcomes / SOUL / always_load), и почти не инструментировал системную сторону (evals, transcript-observability, cross-model second opinion, subagent verifier). Все 4 ошибки 90-дневного аудита ("85 BAD") сводятся к этому перекосу, и все 6 внешних исследований указывают в одно и то же место.

**Что НЕ менять** (исследования согласованно говорят "оставь как есть"):
- Не мигрировать на Codex как primary. Анти-кейс из миграционных гайдов прямо описывает твой профиль (Crosley: 84 hooks + 48 skills и сам не мигрировал).
- Не уходить в peer multi-agent (CrewAI / Ruflo / Agent Teams as default). Поле в 2026 сошлось на **orchestrator + изолированные subagents** — это уже твой `/delegate` + `/grill` CRITIC. Cognition в марте развернулся в эту же сторону.
- Не роутить Claude Code-трафик на DeepSeek для экономии. Max покрывает токены маржинально бесплатно. Issue #39903 (Max-юзер случайно слил $152 через subagent + случайный `ANTHROPIC_API_KEY` в `~/.env`) — это твой риск, не выигрыш.
- Не ставить Spec-Kit / BMAD / GSD. Твой `/grill → /to-prd → /to-issues → /implement` — это уже Reed + Pocock formalized. Тяжёлые фреймворки будут драться с цепочкой.
- Не вводить markdown ADR в `docs/adr/`, не дублировать GitHub Projects v2 sprint planning поверх milestones, не возвращать термин "epic" — каждое уже решено правильно.

**Что менять — 6 изменений ранжированных по impact'у.** Большинство — гибрид: ставим Codex CLI как **side-channel** (не замену), добавляем leading-indicator eval-слой, и закрываем 3 хук-дыры (skill creation gate, decision-omission detector, subagent post-flight verifier). Roadmap ниже укладывается в ~2 рабочих недели, если порядок соблюсти.

**Самое важное:** перед любыми новыми скиллами и фичами — **установить L1 eval harness** (расширение M#43 sycophancy). Без него каждый новый скилл компаундирует skill-proliferation, и ты этого не увидишь.

---

## Карта изменений

| # | Изменение | Закрывает паттерн | Источник | Effort | Risk |
|---|---|---|---|---|---|
| 1 | Codex CLI side-channel + `/cross-critique` skill | personalization-sycophancy, doc-vs-code drift | [cc-vs-codex](cc-vs-codex-vs-alternatives-2026-05-18.md), [gap-discovery](gap-discovery-patterns-2026-05-18.md) | 1 day | low |
| 2 | L1 golden-set eval harness (расширение M#43) | record_decision post-hoc, skill regression, sycophancy | [gap-discovery](gap-discovery-patterns-2026-05-18.md), [single-agent](single-agent-workflows-2026-05-18.md) | 2-3 days | medium |
| 3 | Subagent post-flight verifier (PostToolUse hook on Task) | non-determinism subagent dispatch | [behavior](session-behavioral-analysis-2026-05-18.md), [single-vs-multi](single-vs-multi-agent-architecture-2026-05-18.md) | 1-2 days | medium |
| 4 | PreToolUse hook на skill creation (justified-flag gate) | skill proliferation поверх собственного antipattern | [behavior](session-behavioral-analysis-2026-05-18.md), [single-agent](single-agent-workflows-2026-05-18.md) | 0.5 day | medium-high (FP) |
| 5 | Stop-hook scan "decision-shaped exchange без record_decision" | post-hoc captures (Tier-2 промах) | [behavior](session-behavioral-analysis-2026-05-18.md), [gap-discovery](gap-discovery-patterns-2026-05-18.md) | 1 day | low (shadow-mode) |
| 6 | IssueOps layer (label-as-state-machine на 1-2 workflow) + gh-dash + gh-poi | агент-friendly state surface; cross-repo визибилити | [gh-workflows](gh-workflows-solo-vs-team-2026-05-18.md) | 0.5-1 day | low |

Опциональные (deferred до 2-4 недель после Wave 1):
- Self-hosted embeddings (Qwen3-Embedding-8B) → убивает VoyageAI bill на 50-80%
- Weekly "claim-vs-code drift sweep" на CLAUDE.md / CONTEXT.md / SOUL.md (DeepSeek V4-Flash классификатор)
- Iron Laws + red-flag rationalizations copy в `/grill` и `/implement` (steal from Superpowers)
- Linear Walkthrough как `/walkthrough` для возвращения к dormant code (steal from Simon Willison)
- MoA для `/grill` (3× DeepSeek proposers + 1× Claude aggregator)

---

## Деталь по каждому изменению

### 1. Codex CLI side-channel + `/cross-critique` skill (top priority)

**Зачем.** Личная сторона identity-слоя (SOUL.md + always_load) **измеримо увеличивает сикофантизм** (M#43 показал, это в CONTEXT.md glossary). Каждый Claude-судья над Claude-кодом разделяет blind spots модели — within-family bias 60-69% (CIP audit). Cross-model — единственный реальный антидот.

**Как.** `winget install OpenAI.Codex` (Windows experimental; WSL2 чисто работает). Один skill `~/.claude/skills/cross-critique/SKILL.md`. Принимает file path или memory UUID, шеллит `codex exec --json --ask-for-approval never -s read-only < critique-prompt.md`, парсит JSON, аппендит "## Cross-model critique" в артефакт ИЛИ постит PR-комментом. **Запускается холодно** — без SOUL, без памяти, без CONTEXT.md. `AGENTS.md` для Codex = голая копия CLAUDE.md без identity.

**Где применять (initial scope):**
- Plan-mode output `/grill` → cross-critique до `/to-issues`
- Финальный draft PR body перед merge
- Edits к CLAUDE.md / SOUL.md / CONTEXT.md (claim-vs-code drift check)
- `record_decision` с `reversibility=hard|irreversible` → cross-critique rationale

**Почему НЕ migration.** [cc-vs-codex](cc-vs-codex-vs-alternatives-2026-05-18.md) подтверждает: ты — high-friction case (40+ skills, custom MCP server, hook lattice, SessionStart auto-loader). Wholesale port даже миграционные гайды называют антипаттерном. Codex hooks — fewer + weaker ordering. Hybrid — корректный ответ.

**Risk.** Cost (Codex API $1.50/$6 per M); rate limit; сикофантизм GPT-5.5 на свою сторону (mitigate adversarial system prompt: "Find weaknesses. Default to disagreement unless evidence forces agreement."). Tiebreaker protocol: при разногласии Codex с Claude → **записать обе позиции в record_decision как alternatives**, не автоматически выбирать ни одну.

**Source.** [cc-vs-codex §"The dominant 2026 hybrid pattern"](cc-vs-codex-vs-alternatives-2026-05-18.md), [gap-discovery §"Adversarial Cross-Model Review"](gap-discovery-patterns-2026-05-18.md).

---

### 2. L1 golden-set eval harness (расширение M#43)

**Зачем.** Каждый текущий audit-инструмент — **lagging indicator**. Decision-audit смотрит назад. /reflect смотрит назад. /verify смотрит post-merge. Когда `/grill` регрессирует — ты узнаёшь это, наткнувшись на провал в следующей реальной сессии. Hamel/Anthropic minimum: 20-50 задач из реальных failure transcripts с тремя графдерами (code-based, model-based, human spot-check 1-in-N). Без этого "workflow не работает" остаётся ощущением, а не гипотезой.

**Как.** M#43 (`scripts/sycophancy_eval.py` + 12 scenarios) — уже scaffold. Расширить:

```
scenarios/
  sycophancy/        (12 existing, M#43)
  grill_assumptions/ (~10: каждый "/grill on prompt X must surface ≥1 assumption matching Y")
  memory_recall/     (~10: "recall on query Z must return UUID-set ⊇ {…}")
  record_decision/   (~10: "decision-shaped exchange must produce record_decision with non-empty memories_used")
  subagent_dispatch/ (~5: "/delegate on issue must produce PR matching AC")
```

Запуск: scheduled task nightly, weekly cumulative report → `outcome_record(scope=evals, severity=...)`. Eval saturation (100% pass = no signal) — каждые ~12 недель ротировать сценарии. Pass@k и pass^k для non-determinism (k=3).

**Где живёт.** В этом репо: `tests/evals/` + `scripts/run-evals.py`. Scheduler — Workshop (или один из 3 devices).

**Почему сначала это.** Все остальные изменения добавляют новую поверхность. Без harness'а — не увидишь, когда они начнут регрессировать. И что критично: **#2 это gate для #4 и #5**. Без него ты не сможешь измерить, работает ли skill-creation gate (false positives?) или decision-omission scan (классификатор флэйкает?).

**Risk.** Maintenance burden, eval flakiness. Митигация: для каждого сценария фиксировать baseline через 3 runs, alarm только при drift >15pp.

**Source.** [gap-discovery §"Minimum Viable Eval Harness"](gap-discovery-patterns-2026-05-18.md), [single-agent §"Outcomes-as-rubric"](single-agent-workflows-2026-05-18.md). Behavior audit явно: "absence of leading indicator — #1 gap".

---

### 3. Subagent post-flight verifier (PostToolUse hook на Task)

**Зачем.** За 2 недели мая — 4 partial/failure outcome'а на subagent dispatch (#665+#662, #690+#691, #687). Одинаковый contract → разный результат в один день (#689 vs #690+#691). PR #647: 16/16 tests "passed" — ни одного в диффе (fabrication). `verify subagent work via git diff` — prompt-rule в CLAUDE.md, и **тот же класс ошибки рекуррит уже трижды**. Это сигнал, что prompt-rules в этом месте больше не работают; нужен hook.

**Как.** Новый `scripts/subagent-result-verifier.py` для PostToolUse на `Task` (или `SubagentStop` если уже доступен — изменения Anthropic Apr 22 включали forked subagents on external builds). Логика:
1. Парсит summary subagent'а: список файлов, claimed test counts, list of changes.
2. `git diff --stat <base>..HEAD` где `base` = parent's HEAD до dispatch'а.
3. Assert: claimed files ⊆ actually-diffed files.
4. Assert: claimed test counts grep'аются в `tests/**`.
5. Divergent → отказ принять summary, `outcome_record(scope=subagent-fabrication, severity=high)`, surface парент'у явно.

**Edge.** Subagent может НЕ изменять файлы (research-only). Контракт: research-only субагенты должны явно объявлять `output_type=research` в финальном сообщении; verifier пропускает их.

**Risk.** PostToolUse hook на Task должен иметь FS-access. Если Anthropic зашипает `SubagentStop` нативно — используй его. Shadow-mode 2 недели (только outcome_record, не блочить) перед "fail loud".

**Source.** [single-vs-multi §"orchestrator + isolated subagents needs verification primitives, not subagent-side prompt-tightening"](single-vs-multi-agent-architecture-2026-05-18.md), [behavior §"#3 subagent dispatch non-determinism"](session-behavioral-analysis-2026-05-18.md).

---

### 4. PreToolUse hook на skill creation (justified-flag gate)

**Зачем.** Live skill count: 28 директорий, 7 `.bak.orphan`, один — двойной orphan (`dnd.bak.orphan.bak.orphan.bak.orphan`). План v1.5 был "6+1". Memory `skill_proliferation_antipattern` существует, но локальный fix-it reflex (написать skill за 20 минут) бьёт защитный путь (recall → audit → /grill против antipattern). **Friction differential favours antipattern** — то же, что у record_decision post-hoc. Решение: уровнять friction.

**Как.** `scripts/skill-creation-gate.py` в PreToolUse:
- Trigger: `Edit|Write` с file_path ~ `**/skills/**/SKILL.md`, тот же файл ещё не правился в этой сессии.
- Action: blocking unless prompt assistant'а в текущем turn'е содержит recall'нутый UUID `skill_proliferation_antipattern` + явный `--justified=<reason>` flag.
- Exit code 2 + понятная error message: "Skill creation requires explicit antipattern check. Recall skill_proliferation_antipattern, then re-attempt with rationale in the message."
- Env-bypass: `JARVIS_SKILL_EDIT_OK=1` для legit per-skill PR flow.

**Risk.** Tier-2 hooks имеют 6× FP history (audit Infra #48). Митигация: shadow-mode 1 неделя, потом hard. Bypass-env обязательно (иначе invariant ⇒ хук блочит твою же работу по фикcу хука).

**Source.** [single-agent §"Superpowers Iron Laws + red-flag rationalizations"](single-agent-workflows-2026-05-18.md), [behavior §"#1 skill proliferation outruns antipattern"](session-behavioral-analysis-2026-05-18.md).

---

### 5. Stop-hook scan: "decision-shaped exchange без `record_decision`?"

**Зачем.** `record_decision` Tier-2 hook fires только когда tool *вызывается*. Не fires когда decision *принят, но не записан* — это post-hoc captures. Audit BAD #13 — этот паттерн через все 90 дней. iter:50 (#669) — N=6 в одном AFK chain. Pattern: между chosen alternatives → assistant принимает направление → не вызывает `record_decision` → /reflect ловит post-hoc → пишется задним числом.

**Как.** Stop-hook (использовать существующее `scripts/comm-patterns-extract.py` plumbing). После extract транскрипта:
1. Cheap классификатор (DeepSeek V4-Flash через OpenRouter, $0.14/M input) на последние N (user-prompt, assistant-response) пар.
2. Prompt: "Did the assistant resolve a between-named-alternatives question? Did it commit to a directional choice with downstream consequences?"
3. Если yes + `record_decision` НЕ вызывался в этой сессии → `outcome_record(scope=missing-decision, severity=medium, evidence=<excerpt>)`.
4. /reflect забирает следующим pass'ом.

**Risk.** Классификатор flake'ит → false-positive outcome'ы. Shadow-mode 2 недели (только запись, не surface'ить пользователю). Calibration: после 2 недель — спот-чек 20 outcome'ов вручную, calibrate threshold.

**Source.** [gap-discovery §"Decision journal review pass"](gap-discovery-patterns-2026-05-18.md) (Farnam Street: "the record alone has limited value — the comparison produces calibration"), [behavior §"#2 record_decision post-hoc"](session-behavioral-analysis-2026-05-18.md).

---

### 6. IssueOps layer + gh-dash + gh-poi

**Зачем.** [gh-workflows](gh-workflows-solo-vs-team-2026-05-18.md) подтверждает: твой issues-as-state / milestones-as-capability / PRs-for-code / discussions-for-RFC / decisions-in-memory — это уже convergence с best team practice. Чего нет: explicit **IssueOps automation layer** — labels-as-state-machine + comments-as-commands. Это машино-читаемая FSM поверх Issues, идеальна для agent-driven workflow.

**Как (Wave 1, минимальный кат):**
1. Один workflow для proof-of-concept. Кандидат: `ready:agent` label → GitHub Action триггерит `gh issue assign <coding-agent-bot>` (или `claude-code-action`).
2. `gh-dash` config с двумя personas: `jarvis` и `redrobot`. Cross-repo PR/issue view на одном экране.
3. `gh-poi` — installed once, run `gh poi --dry-run` в /end skill (auto-prune merged local branches без force).

**Wave 2 (defer):** comment-driven commands (`/verify`, `/grill` как Action triggers), Rulesets-with-bypass для self-approval отдельно от классической branch protection — только когда агенты начнут пушить без HITL (сейчас всё ещё HITL'ится). Stacked PRs (`gh stack`) — defer до момента, когда milestone routinely 4+ ordered slices.

**Cost/Benefit.** ~30 минут setup на 1-2 устройства. Daily payoff на cross-repo визибилити, плюс foundation для будущей агентной автоматизации.

**Source.** [gh-workflows §"IssueOps as the agentic primitive"](gh-workflows-solo-vs-team-2026-05-18.md).

---

## Что НЕ делать — расширенный список (с обоснованием)

| Не делать | Почему | Source |
|---|---|---|
| Мигрировать на Codex | High-friction case; миграционные гайды (Crosley, Pillitteri) сами отговаривают; Codex hooks — fewer, weaker ordering; Anthropic incident Apr 23 resolved | [cc-vs-codex](cc-vs-codex-vs-alternatives-2026-05-18.md) §"Three concrete reasons to stay" |
| Pivot на peer multi-agent (CrewAI/AutoGen/Ruflo) | Field consolidated на orchestrator+isolated-subagents в 2026; Cognition в марте развернулась туда же; ты УЖЕ на winning architecture | [single-vs-multi](single-vs-multi-agent-architecture-2026-05-18.md) §TL;DR |
| Enable `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` baseline | Твои workloads (issue-driven, sequential, context-coherent coding) — explicit anti-pattern Agent Teams; 2.25-7× cost; держать как **contingent опцию** для одного full-stack workload, когда он появится | [single-vs-multi](single-vs-multi-agent-architecture-2026-05-18.md) §"Anthropic's Agent Teams" |
| Routing Claude Code на DeepSeek для cost | Max покрывает CC маржинально free; Issue #39903 — $152 утечка от случайного `ANTHROPIC_API_KEY` в `~/.env`; engineering > savings | [cheap-models](cheap-models-cost-reduction-2026-05-18.md) §"The Max-plan billing trap" |
| Spec-Kit / BMAD / GSD | Тяжелее твоего chain'а; будут драться с `/to-prd → /to-issues → /implement` (Pocock явно критикует эти за "taking control from engineer") | [single-agent](single-agent-workflows-2026-05-18.md) §"Don't adopt" |
| Markdown ADR в `docs/adr/` | `record_decision` (queryable, UUID-grounded) > markdown (decays); two-track = guarantee drift | [gh-workflows](gh-workflows-solo-vs-team-2026-05-18.md) §"Worth *not* importing" |
| GitHub Projects v2 sprint planning | Milestones-as-capability уже это; sprint overlay = double-tracking, который ты уже убил с `milestone_hierarchy_v3` | [gh-workflows](gh-workflows-solo-vs-team-2026-05-18.md) §"Worth *not* importing" |
| Вернуть термин "epic" | Audit GOOD #38; "milestone — only grouping primitive" (decision `2a7ae10e`); external sources заваливают этим, slip-back vector высокий | [behavior §"Don't NOT do"](session-behavioral-analysis-2026-05-18.md) |
| Добавлять новые skills ДО #2 (eval harness) | Skill churn — твой #1 dominant pattern; каждый новый skill компаундирует proliferation без eval-bounds | [behavior §TL;DR](session-behavioral-analysis-2026-05-18.md) |
| Local LLM как replacement Claude | Frontier open weights (DeepSeek V4-Flash 150GB, Qwen3-Coder-480B 250GB) — не помещаются на consumer rig; для тебя single edge case = self-hosted **embeddings** (Qwen3-Embedding-8B на 16GB), не agentic | [cheap-models](cheap-models-cost-reduction-2026-05-18.md) §"Hardware reality" |

---

## Roadmap

### Wave 1 (≈3-5 рабочих дней, делать в этом порядке)

1. **День 1 утро** — `winget install OpenAI.Codex` + `~/.claude/skills/cross-critique/SKILL.md`. Smoke test на одном `/grill` output'е. **Записать `record_decision`** на tiebreaker protocol (что делать при Codex ≠ Claude disagreement).
2. **День 1 вечер** — `gh extension install dlvhdr/gh-dash` + `gh extension install seachicken/gh-poi`. Config файлы для двух persona (jarvis, redrobot). Один alias.
3. **День 2-3** — расширить M#43 sycophancy harness до полного L1 eval-set (~40-50 сценариев по 5 категориям). Wire scheduled-task nightly. **Этот гейт. Без него ничего дальше из Wave 2 не идёт.**
4. **День 4** — `scripts/subagent-result-verifier.py` в **shadow mode** (только запись outcome'ов, не блочить).
5. **День 5** — IssueOps proof-of-concept (один workflow + один label).

### Wave 2 (≈3-5 рабочих дней, после Wave 1 stabilized)

6. **Skill-creation gate** в shadow mode (1 неделя) → hard mode.
7. **Decision-omission Stop-hook scan** в shadow mode.
8. Subagent verifier переключить из shadow → hard mode.

### Deferred (после 2-4 недель измерений на Wave 1+2)

- Self-hosted embeddings (Qwen3-Embedding-8B + Supabase pgvector) — kills $20/mo line item
- Weekly claim-vs-code drift sweep на CLAUDE.md / CONTEXT.md / SOUL.md
- Iron Laws + red-flag rationalizations copy в `/grill` и `/implement`
- `/walkthrough` skill (Linear Walkthrough pattern для dormant code)
- MoA для `/grill` (3× DeepSeek proposers + 1× Claude aggregator) — только если /grill cost начнёт мешать

### НЕ в этом плане (но в backlog)

- Cline / opencode / Aider experimentation — defer Q3 2026, re-evaluate если ACP набирает MCP-class adoption
- Anthropic Auto Dream — defer до GA (research preview сейчас)
- LangGraph для конкретного workflow (autonomous-day loop или cross-device routine orchestration) — defer до момента, когда workflow явно перерастёт CC skills/hooks

---

## Открытые вопросы (для тебя, не для меня)

Ответы на них меняют roadmap. Не отвечай мне — ответь себе перед действиями.

1. **Сколько часов в неделю готов выделить на system work vs feature work?** Wave 1 — 3-5 рабочих дней. Wave 2 — ещё 3-5. Это ~2 рабочие недели чистого system-work. Готов ли поставить feature work на паузу на этот период? Если нет — режь roadmap пополам и делай только #1 + #2.

2. **При disagreement Codex ≠ Claude на /grill output — какой tiebreaker?** Это решается ОДИН раз, в `record_decision`, ДО первого реального cross-critique. Без этого каждый disagreement — новая friction-точка. Мой default: записать обе позиции в `alternatives_considered`, не выбирать автоматом.

3. **Скилл-creation gate с какой FP-tolerance?** Tier-2 hooks имеют 6× FP history. Если 2 false-positive на легитимный skill = окей — shipping. Если первый FP = удалю хук — НЕ shipping. Реши заранее.

4. **Late-night sessions для consequential work — продолжаешь?** Pattern #7 в behavior audit: 10 sessions/day, 16-hour daily span, 19% всех messages — после 22:00 local. SOUL.md "smart zone ~100K tokens" — это per-session ceiling; 10 sessions/day насилует cross-session continuity layer. /autonomous-loop может забирать routine overnight; consequential decisions — нет. Готов поставить себе boundary?

5. **SOUL.md identity layer — стоит ли цены сикофантизма?** Ты измерил delta. Очевидная альтернатива (suspend identity на consequential decisions) частично shipped через grill CRITIC. Достаточно "частично"?

6. **/autonomous-loop — что должно его БАУНДИТЬ, а не triггерить?** Дизайн loop'а: всегда добавляет work, никогда не вычитает. При 10 sessions/day какой threshold должен заставить его *paused*, не *fired*?

---

## Где жить дальше

- **Этот файл (`workflow-change-plan-2026-05-18.md`)** — actionable plan. Перевести в issues через `/to-issues` после ответов на 6 вопросов выше.
- **6 исследовательских docs** в `docs/research/*-2026-05-18.md` — reference. Цитировать из issue body'ев + `record_decision`.
- **`session-behavioral-analysis-2026-05-18.md`** — гольд-кейс для будущего `/reflect`. Сохранить как baseline, чтобы через 90 дней сравнить.
- **`decision-audit-90d-2026-05-18.md`** (уже в `.out-of-scope/`) — pair с behavioral analysis. После Wave 1+2 запустить новый audit за следующие 90 дней и сравнить ratio BAD/GOOD.

---

## Краткий honest take

Ты не отстаёшь от поля — ты в нескольких местах **впереди** (UUID-not-name memory contracts, three-way doc split с SessionStart auto-loader, `record_decision` rationale chain, milestone hierarchy v3). Твоё ощущение "не работает" не про отсутствие фич — про **отсутствие ground truth у себя самого**. Когда каждый аудит делает Claude над Claude, и оценки рисует Claude над собственными outcome'ами — ты не видишь, когда система начинает врать. Eval harness + cross-model critique — это **зеркало**, которого сейчас нет. Остальное — закрытие 3 hook-дыр, которые ты сам уже идентифицировал в audit'е, но не закрыл по friction-причинам.

Stop adding skills. Install the harness. Add a second model that doesn't share Claude's biases. После этого решай дальше — данные будут.
