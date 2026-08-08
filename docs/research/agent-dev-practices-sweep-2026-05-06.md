# Wide sweep: AI-agent-assisted dev practices

Date: 2026-05-06
Mode: breadth (no deep dives)
Trigger: Telegram msg 161 — owner re-evaluates devloop after a week of intensive learning.

Tags: [s] settled · [c] contested · [f] frontier · conf /5
Marker `[в системе]` = уже отражено в Jarvis (skill / memory / CLAUDE).

---

## 1. Принципы инженерии в эпоху агентов

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 1.1 | TDD / red-green-refactor с агентами | [s] | 5 | Консенсус: Beck, Pocock, Willison, alexop. Агент любит писать «happy path» — TDD форсирует поведение через тесты. Skill `/tdd` уже жёстко гейтит фазы. **[в системе]** |
| 1.2 | Vertical slice / tracer bullet | [s] | 5 | Один срез сквозь стек до проверяемого результата. Anti-horizontal. AI Hero principle. **[в системе]** |
| 1.3 | Deep modules (Ousterhout) | [s] | 5 | Маленький интерфейс, толстая реализация. Анти-фрактал «сотен вежливых файлов». Skill `/improve-codebase-architecture`. **[в системе]** |
| 1.4 | Plan / Execute / Clear rhythm | [s] | 5 | Сжимать контекст к артефакту → новая сессия. AI Hero P/E/C. **[в системе]** |
| 1.5 | Smart zone ~100K токенов | [s] | 5 | После ~40% контекста — деградация рассуждения; после 100K — drift даже у 1M-моделей. **[в системе]** |
| 1.6 | Tight feedback loops (типы / тесты / линтеры / браузер) | [s] | 5 | Без авто-feedback агент летит вслепую. **[в системе]** |
| 1.7 | Design It Twice (parallel exploration) | [c] | 4 | Pocock: запускать 2-3 подхода параллельно, выбирать. Не у всех прижилось. |
| 1.8 | Tiny commits (Fowler) | [s] | 4 | Микро-коммиты особенно ценны при ревизии агентских изменений. |
| 1.9 | Behavior tests > implementation tests | [s] | 5 | TDD проверяет публичный интерфейс, не внутренности. |
| 1.10 | Refactor adjacent legacy under test cover | [c] | 4 | Анти-loss-aversion для vibe-coded репо. **[в SOUL]** |

## 2. Workflow-паттерны агентной разработки

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 2.1 | Spec-Driven Development (PRD как validation gate) | [c] | 4 | arXiv 2026-02 + Thoughtworks. SDD-spec ≠ PRD-документ — это исполняемый контракт. GitHub Spec Kit, Augment, Kiro. |
| 2.2 | Grill-me / interrogation-before-plan | [s] | 5 | AI Hero `/grill-with-docs`. Алайнмент до написания плана. **[в системе]** |
| 2.3 | PRD → vertical-slice issues → /implement | [s] | 4 | Канонический pipeline AI Hero. **[в системе] частично — есть `/to-prd`, `/to-issues`, `/implement`.** |
| 2.4 | Subagent delegation / parallel agents | [s] | 5 | Subagent = отдельный контекст-карман. **[в системе]: `/delegate`.** Известная боль — fabrication, scope-shrinkage. **[5 memories о ловушках]** |
| 2.5 | Verification-after-delegate (`git diff`, AC checklist) | [s] | 5 | Subagent self-report ≠ реальность. **[в системе]** |
| 2.6 | Ralph Wiggum loop (Huntley) | [c] | 3 | Автономный цикл агента до критерия завершения. Используется для крупных миграций кода. |
| 2.7 | Back-pressure for long-horizon work | [c] | 4 | Huntley: качество долгих задач = качество автоматического feedback. |
| 2.8 | Fresh-eyes review (другой контекст для ревью своего кода) | [s] | 5 | AI Hero. **[в SOUL]** |
| 2.9 | "Skills as workflow entry points, commands shallow" | [s] | 4 | alexop / Anthropic blog. Команда = тонкий вход, навык = глубина. |
| 2.10 | Phase gates (TDD / diagnose / grill) | [s] | 4 | Pocock skills блокируют переход между фазами до выполнения условий. |

## 3. Knowledge tooling — RAG / context / docs / ADR

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 3.1 | Long-context vs RAG (комплементарны) | [s] | 5 | <200K токенов → full-context + prompt caching. Больше → RAG. Гибрид — норма 2026. |
| 3.2 | llms.txt стандарт | [c] | 4 | Markdown-индекс корня сайта для агентов; HTML-обёртка тратит до 90% токенов. Cursor/Copilot уже парсят. |
| 3.3 | Context7 / live-docs MCP | [s] | 5 | Свежая документация в окно по требованию. **[в системе]** |
| 3.4 | CONTEXT.md / repo-level glossary | [s] | 4 | Доменная модель в репо для агента. **[в системе] — растёт через `/grill-me`.** |
| 3.5 | ADR как контекст для агента | [s] | 4 | Адольфи / OpenSpec — ADR превращается из артефакта для людей в input для LLM. **[в системе] — `docs/adr/`.** |
| 3.6 | Memory как «процедурная» дополнение к семантической | [f] | 4 | mem0 v1.0 — третий тип: procedural (как делать). |
| 3.7 | Context engineering как дисциплина | [s] | 5 | «The right 300 tokens beat 100K noisy» (QCon Mar 2026). Переход от prompt eng к context eng. |
| 3.8 | "RAG → Context Engine" эволюция | [c] | 4 | RAGFlow 2025-year-end: RAG растворяется в более широком context engineering. |

## 4. Ландшафт инструментов

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 4.1 | Claude Code | [s] | 5 | Top SWE-bench Verified 87.6%. **[в системе] — основная среда.** |
| 4.2 | Cursor | [s] | 4 | IDE-first, дешевле в команде, но 5.5× больше токенов на задачу. |
| 4.3 | Aider | [s] | 4 | CLI, BYOK, 52.7% bench, 257с/задача. |
| 4.4 | Cline | [s] | 4 | Open-source, мульти-IDE, хороший MCP. |
| 4.5 | Devin | [c] | 3 | Cloud sandbox, $20/мес core + ACU. SWE-bench Pro 13.86%. Frontier для autonomous-cloud. |
| 4.6 | OpenCode / Codex CLI / Amp | [c] | 3 | Конкуренты Claude Code в terminal-native категории. |
| 4.7 | Pocock skills repo (45K stars) | [s] | 5 | Эталон skill-каталога. **[в системе] — уже свой каталог.** |
| 4.8 | MCP ecosystem (500+ серверов, AAIF / Linux Foundation) | [s] | 5 | GitHub, Filesystem, Postgres, Firecrawl, Notion, Slack — high-frequency. |

## 5. Evals и ground truth

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 5.1 | Hamel Husain eval framework | [s] | 5 | Error analysis first; binary pass/fail; 70% pass rate ≥ 100%; 30 мин просмотра 20-50 outputs до инфраструктуры. |
| 5.2 | LLM-as-Judge | [s] | 4 | Hamel guide; alignment с человеком — обязательное условие. |
| 5.3 | Braintrust vs LangSmith | [c] | 4 | Braintrust: prod traces → regression tests, PR-блокировка. LangSmith: дашборды без блокировки. Latitude/Langfuse — open-source альтернативы. |
| 5.4 | Process metrics (steps / cost / time) vs E2E success | [s] | 4 | Сегментация ошибок по стадиям workflow — early-stage даёт каскад. |
| 5.5 | Production failure → permanent regression test | [c] | 4 | Braintrust pattern. Применимо вообще к любому agent-pipeline. |
| 5.6 | Eval как первый-класс артефакт под каждый skill | [f] | 4 | Hamel «Evals Skills for Coding Agents». Свежее. **[не в системе]** |

## 6. Анти-паттерны

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 6.1 | Vibe coding (без тестов / ревью) | [s] | 5 | 47.5% Claude 4 Sonnet correctness, только 8.25% secure. 24.7% AI-кода с уязвимостью. |
| 6.2 | Shallow modules / fractal split | [s] | 5 | Pocock identified pattern: AI режет на мелкие файлы → код без центра. |
| 6.3 | Horizontal slicing | [s] | 5 | «Сначала все тесты, потом весь код» → тесты на воображаемое поведение. |
| 6.4 | Premature abstraction | [s] | 5 | YAGNI до двух реальных реализаций. **[в SOUL]** |
| 6.5 | Trust agent self-report | [s] | 5 | **[в системе] — 5 memories об этом.** |
| 6.6 | Security theater промптов | [s] | 4 | «Write secure code» в system prompt = плацебо. |
| 6.7 | Disabling validation для прохождения тестов | [s] | 4 | Beck: агент удаляет тесты, чтобы они «прошли». |
| 6.8 | Не покрытые pillars: error handling, idempotency, retries, observability | [s] | 4 | Vibe-output систематически их упускает. |

## 7. Memory-архитектуры

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 7.1 | Episodic + Semantic + Procedural | [s] | 4 | Mem0 v1 ввёл procedural как третий первоклассный. |
| 7.2 | Vector vs Graph (комплементарны) | [s] | 4 | Vector — fuzzy recall; Graph — связи между сущностями. |
| 7.3 | Hybrid stack (vector + episodic buffer + graph) | [s] | 4 | Production-паттерн 2026. **[частично в системе] — Supabase vector + memory tags.** |
| 7.4 | SYNAPSE (spreading activation, lateral inhibition) | [f] | 3 | arXiv 2601.02744 — динамический граф памяти. |
| 7.5 | A-MEM (агентная память для LLM) | [f] | 3 | arXiv 2502.12110. |
| 7.6 | AriGraph (KG + episodic для агентов) | [f] | 3 | IJCAI 2025. |
| 7.7 | ICLR 2026 MemAgents workshop | [f] | 3 | Память — first-class architectural component. |
| 7.8 | Источники: state-of-ai-agent-memory-2026 (mem0), Agent-Memory-Paper-List | [s] | 4 | Стартовые точки для deep dive. |

## 8. Repo-level discipline

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 8.1 | CLAUDE.md / SOUL.md / CONTEXT.md split | [c] | 5 | Three-way: rules / identity / domain. **[в системе]** |
| 8.2 | Skills (auto-discovered, progressive disclosure) | [s] | 5 | **[в системе]** |
| 8.3 | Hooks (deterministic guardrails) | [s] | 5 | PreToolUse/PostToolUse/UserPromptSubmit. **[в системе]** |
| 8.4 | Slash-commands как entry-points | [s] | 5 | **[в системе]** |
| 8.5 | Plugins (markdown bundles навыков) | [c] | 3 | Anthropic свежее. Pocock skills — фактически плагин. |
| 8.6 | Subagent definitions (specific, не general) | [s] | 4 | feature-specific > general-purpose. **[в системе]** |

## 9. Agent ergonomics

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 9.1 | Error messages as ground truth | [s] | 4 | Агент чинит то, что видит. Богатые сообщения → быстрее цикл. |
| 9.2 | Naming: speak the domain language | [s] | 5 | Imена — первая документация для агента. |
| 9.3 | Deep modules для агентов | [s] | 5 | См. 1.3 — это и принцип, и эргономика. |
| 9.4 | LLM-friendly repo structure (Fern post) | [c] | 3 | Документация в markdown-first, llms.txt. |
| 9.5 | "Treat agent like dev with no memory" | [s] | 5 | Repo-level процессы вместо vibes. **[в SOUL]** |

## 10. Frontier

| # | Топик | Тэг | Conf | Аннотация |
|---|---|---|---|---|
| 10.1 | Anthropic Managed Agents (public beta 2026-04-08) | [f] | 4 | Hosted runtime — sessions/sandboxes/state на стороне Anthropic. **[в системе] — `managed_agents_wait_and_see` decision.** |
| 10.2 | Computer use / browser-use агенты | [f] | 4 | Claude Computer Use research preview 2026-03. |
| 10.3 | Multi-agent orchestration | [c] | 4 | Hierarchical (supervisor-worker) и graph выживают в проде. Swarm — research only. **[в системе]: Pillar 7.** |
| 10.4 | Persistent agents (LangGraph) vs Routines | [c] | 4 | **[в системе]: pm_dispatch_v1 superseded by persistent agents.** |
| 10.5 | LocoBench / агентные long-context bench | [f] | 3 | Salesforce «Beyond 100K Tokens» eval. |
| 10.6 | Conductor + Swarm hybrid (mid-market) | [c] | 3 | 10-200 чел. — гибрид-паттерн. |

---

## Top-5 кандидатов на deep dive

Приоритизировано по дельте между «уже в системе» и «значимо изменит devloop».

1. **Hamel-style evals для skill-каталога Jarvis** (5.1, 5.5, 5.6).
   *Почему:* у нас 30+ skills, ноль формальных evals. После переноса на TDD-логику следующий шаг — eval-as-skill. Hamel прямо описал это весной 2026.
   *Действие:* прочитать Hamel evals-faq + «Evals Skills for Coding Agents», проектировать eval-skill для `/implement` и `/delegate`.

2. **Spec-Driven Development (3.x секция)** (2.1, 2.3).
   *Почему:* у нас канон `/grill-me → /to-prd → /to-issues → /tdd`, но spec как **исполняемый контракт** (validation gate, не текст для людей) — это другой уровень. arXiv 2026-02 + GitHub Spec Kit + Augment.
   *Действие:* сравнить с нашим `/to-prd` outputом, понять, что сделать executable.

3. **Memory architectures за пределами vector (7.x)** (7.1, 7.2, 7.4, 7.7).
   *Почему:* Pillar 4 — Supabase vector. Procedural memory + episodic buffer + graph слой назрели. ICLR 2026 MemAgents + mem0 v1 + SYNAPSE — три независимых указателя в одну сторону.
   *Действие:* прочесть mem0 «State of AI Agent Memory 2026» + Agent-Memory-Paper-List, выбрать что воровать.

4. **llms.txt + LLM-friendly docs стандарт (3.2, 9.4)** для собственных репозиториев.
   *Почему:* быстрая победа — Cursor / Copilot / Claude Code уже умеют. CONTEXT.md уже есть, но llms.txt = публичный контракт.
   *Действие:* Fern post + llms.txt spec → решить, нужен ли в jarvis/redrobot.

5. **Long-horizon back-pressure / Ralph Wiggum (2.6, 2.7)**.
   *Почему:* Huntley описывает workflow для миграций / portов, который у нас слабо покрыт. Применимо к refactor-задачам redrobot и к будущему «digital twin».
   *Действие:* читать ghuntley.com/loop/ + ghuntley.com/pressure/ + ralph-wiggum-ai-coding (Leanware).

## Сознательно не углубляли

- Конкретные benchmark-цифры моделей — устаревают за недели.
- Ландшафт коммерческих eval-вендоров кроме Braintrust/LangSmith — мало добавляет за пределами 5.3.
- LangGraph / CrewAI / AutoGen механика — Pillar 7 уже исследовал.
- Computer-use deep — рано для нас.

## Sources (canonical only)

- AI Hero / Pocock: https://www.aihero.dev/, https://github.com/mattpocock/skills
- Pocock TDD skill: https://skills.sh/mattpocock/skills/tdd
- Beck «Augmented Coding»: https://tidyfirst.substack.com/p/augmented-coding-beyond-the-vibes
- Beck Pragmatic Engineer interview: https://newsletter.pragmaticengineer.com/p/tdd-ai-agents-and-coding-with-kent
- Willison «Agentic Engineering Patterns»: https://simonwillison.net/2026/Feb/23/agentic-engineering-patterns/
- Willison Red/Green TDD: https://simonwillison.net/guides/agentic-engineering-patterns/red-green-tdd/
- Hamel evals FAQ: https://hamel.dev/blog/posts/evals-faq/
- Hamel evals skills for coding agents: https://hamelhusain.substack.com/p/evals-skills-for-coding-agents
- Hamel LLM-as-Judge: https://hamel.dev/blog/posts/llm-judge/
- Huntley Ralph loop: https://ghuntley.com/loop/
- Huntley back pressure: https://ghuntley.com/pressure/
- Ousterhout deep modules (in agent context): https://www.mejba.me/blog/improve-codebase-architecture-skill-deep-modules
- alexop Claude Code stack: https://alexop.dev/posts/understanding-claude-code-full-stack/
- alexop TDD with Claude: https://alexop.dev/posts/custom-tdd-workflow-claude-code-vue/
- Anthropic Managed Agents docs: https://platform.claude.com/docs/en/managed-agents/overview
- Anthropic Skills explained: https://claude.com/blog/skills-explained
- llms.txt LLM-friendly docs (Fern): https://buildwithfern.com/post/how-to-write-llm-friendly-documentation
- RAG year-end review 2025: https://ragflow.io/blog/rag-review-2025-from-rag-to-context
- Spec-Driven Development overview: https://www.augmentcode.com/guides/what-is-spec-driven-development
- Mem0 State of AI Agent Memory 2026: https://mem0.ai/blog/state-of-ai-agent-memory-2026
- Agent Memory paper list: https://github.com/Shichun-Liu/Agent-Memory-Paper-List
- ADR for AI agents: https://adolfi.dev/blog/ai-generated-adr/, https://intent-driven.dev/blog/2026/04/29/spec-driven-development-with-adr/
- Coding agents leaderboard: https://artificialanalysis.ai/agents/coding
- Best MCP servers 2026: https://www.mcpbundles.com/blog/best-mcp-servers
- QCon «300 tokens beat 100K»: https://qconlondon.com/presentation/mar2026/right-300-tokens-beat-100k-noisy-ones-architecture-context-engineering
- Beyond 100K tokens (LocoBench): https://www.salesforce.com/blog/locobench-agent/
- Multi-agent patterns: https://gurusup.com/blog/agent-orchestration-patterns
