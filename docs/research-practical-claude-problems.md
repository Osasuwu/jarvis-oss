# Практическое исследование: реальные проблемы и решения с Claude Code

> Дополнение к общей презентации Claude Max (April 8, 2026).
> Цель: конкретные примеры из реального опыта — что работает, что нет, где ещё можно расти.

---

## 1. Что построено поверх чистого Claude Code

Чистый Claude Code — это терминал с ИИ. Вот что мы добавили за ~2 недели:

| Компонент | Что делает | Сложность внедрения |
|-----------|-----------|---------------------|
| **CLAUDE.md** | Правила проекта, архитектура, конвенции — Claude читает при старте | 30 мин, одноразово |
| **SOUL.md** | Идентичность агента, стиль работы, калибровка мнений | 1 час, итеративно |
| **12 Skills** | Переиспользуемые workflow-команды (/delegate, /triage, /research...) | 2-4 часа каждый |
| **MCP Memory** | Кросс-девайсная память через Supabase + Voyage AI эмбеддинги | 8 часов, Python |
| **Session hooks** | Git status при старте сессии — сразу видно контекст | 5 мин |
| **GitHub Actions** | 5 workflow: PR body check, issue sync, review response, project board | 4-6 часов |
| **Coding subagent** | Haiku-модель с ограниченными правами для механических задач | 1 час |
| **Nightly research** | Автономный агент ночью: ищет пробелы в знаниях, исследует, создаёт issues | 4 часа |
| **Risk Radar** | Детерминистический сканер рисков (CI, security, stale issues) — без LLM | 3 часа |

### Что НЕ строили (и почему)
- **Кастомный scheduler** — используем нативный `/loop` и Desktop tasks
- **Telegram бот** — используем Claude Code Channels plugin
- **Бюджет-трекер** — Anthropic dashboard достаточно
- **Multi-user** — не нужно, solo developer
- **Обёртки над Claude API** — Claude Code сам справляется

**Принцип**: не строй то, что Anthropic отправит нативно через месяц.

---

## 2. Каталог реальных проблем

### 2.1 РЕШЁННЫЕ проблемы

---

#### P1: MCP конфиг ломается на другом компьютере
**Симптом**: `.mcp.json` работает на одном ПК, не работает на другом.
**Причина**: Захардкоженные пути (`C:/Users/you/...`) и API ключи прямо в конфиге.
**Решение**: Переменные окружения + `.env` файл.

```json
// БЫЛО (сломано):
{ "command": "python", "args": ["C:/Users/you/GitHub/mcp-memory/server.py"] }

// СТАЛО (портабельно):
{ "command": "python", "args": ["mcp-memory/server.py"],
  "env": { "SUPABASE_URL": "${SUPABASE_URL}", "SUPABASE_KEY": "${SUPABASE_KEY}" } }
```

**Урок для команды**: MCP конфиги — как docker-compose. Никаких абсолютных путей, все секреты через `${ENV_VAR}`.

---

#### P2: Агент говорит "сделано", но ничего не изменил
**Симптом**: Subagent возвращает "Edited 5 files, all tests pass", а `git diff` показывает 0 изменений.
**Причина**: Агент "отредактировал" несуществующие файлы и не получил ошибки, потому что использовал Edit tool на путях, которые не существовали в его рабочей директории (worktree клонировал родительский репо вместо подпроекта).
**Решение**: Протокол верификации.

```
Правило: После ЛЮБОГО subagent — git diff в его рабочей директории.
Никогда не доверяй self-report агента.
```

Дополнительно: `isolation: worktree` клонирует CWD, не target project. Если CWD = `GitHub/` и target = `redrobot/`, агент получит пустой контекст.

**Корректный паттерн**:
```
# НЕ делай: isolation: worktree + prompt "go to redrobot/"
# Делай: агент сам cd redrobot/ && git checkout -b feature-branch
```

**Урок для команды**: Delegation — мощный инструмент, но без верификации это рулетка. Всегда проверяй результат.

---

#### P3: Claude забывает решения между сессиями
**Симптом**: В понедельник обсуждаем архитектуру, во вторник Claude предлагает то, что мы отвергли.
**Причина**: Без персистентной памяти каждая сессия — чистый лист.
**Решение**: MCP Memory Server (Supabase) + привычка сохранять решения сразу.

```
// Сохранение:
memory_store(name="v3_sequential_deposit", type="decision", project="redrobot",
  content="Sand sim must process cut+deposit SEQUENTIALLY along the row.
  Batch creates concentrated mounds; sequential fills deficit progressively.")

// Recall в следующей сессии:
memory_recall(query="sand deposit strategy", project="redrobot")
→ Возвращает решение с контекстом, почему именно так.
```

**Цифры**: 129 записей за 2 недели — 54 feedback, 35 decisions, 19 references, 18 project, 3 user.

**Урок для команды**: Даже без кастомного сервера, файловая память (`~/.claude/projects/.../memory/`) работает на одном устройстве. Для команды — shared CLAUDE.md в репозитории покрывает 80% случаев.

---

#### P4: 14 дублированных skills файлов
**Симптом**: Один skill — 3 копии в разных директориях. Правишь одну, остальные устаревают.
**Причина**: Skills копировались вручную между `personal-AI-agent/.claude/skills/`, `GitHub/.claude/skills/`, и `~/.claude/skills/`.
**Решение**: Единый source of truth (`GitHub/.claude/skills/`) + post-commit hook для синхронизации.

```bash
# scripts/post-commit — автоматически синхронизирует при коммите
if git diff-tree --name-only -r HEAD | grep -q '.claude/skills/'; then
  cp -r personal-AI-agent/.claude/skills/* ../GitHub/.claude/skills/
  cd ../GitHub && git add .claude/skills/ && git commit -m "sync skills"
fi
```

**Урок для команды**: Один источник истины для конфигурации. Git hooks для автосинхронизации. Это базовый DevOps.

---

#### P5: PR создаются пустые — ревьюеру непонятно что изменилось
**Симптом**: Delegated PR приходит с пустым body. Ревьюер открывает diff без контекста.
**Решение**: delegate skill v2.1 — rich PR template.

```markdown
## Summary
- <1-3 bullet points>

## Why
Closes #NNN. <мотивация из issue>

## Key Decisions
- Chose X over Y because Z

## Risk Assessment
- [LOW/MEDIUM/HIGH] — <что может сломаться>

## Testing
- [ ] Unit tests pass
- [ ] Manual verification: <конкретные шаги>

## Files Changed
| File | Change |
|------|--------|
| src/foo.py | Added bar() method |
```

**Урок для команды**: PR template — не бюрократия, а коммуникация. Claude генерирует его автоматически. Настройте `.github/pull_request_template.md` + научите Claude его заполнять через CLAUDE.md.

---

#### P6: Copilot ревью-комментарии игнорируются
**Симптом**: Copilot пишет ревью, но автор забывает применить fix suggestions.
**Решение**: GitHub Action `copilot-review-response.yml` — Claude автоматически:
1. Классифицирует находки по риску (LOW/MEDIUM/HIGH)
2. AUTO-APPLY для LOW/MEDIUM
3. Блокирует на safety-critical файлах (config, MCP, auth)
4. Коммитит fix + постит summary-комментарий

```yaml
# .github/workflows/copilot-review-response.yml
on:
  pull_request_review:
    types: [submitted]
jobs:
  respond:
    if: github.event.review.user.login == 'copilot-pull-request-review[bot]'
    runs-on: ubuntu-latest
    steps:
      - uses: anthropic/claude-code-action@beta
        with:
          prompt: "Classify findings by risk. Auto-apply LOW/MEDIUM..."
```

**Урок для команды**: CI/CD + AI = event-driven quality. Copilot находит, Claude чинит, человек одобряет. Тройная проверка без ручной работы.

---

#### P7: Ночью простаивает — утром нет контекста
**Симптом**: Каждое утро 15-20 мин на "войти в контекст" — что делал вчера, какие проблемы, что исследовать.
**Решение**: Nightly research agent + `/status` dashboard.

Ночной агент (03:00):
1. Загружает контекст из памяти (что решали, что застряло)
2. Находит 3 реальных пробела в знаниях
3. Исследует через web search
4. Сохраняет результаты в Supabase + создаёт GitHub issues

Утром:
```
/status
→ Git: main, 3 uncommitted files
→ PRs: #529-#538 open, 2 need review
→ Issues: #510 HIGH stale 5 days
→ Research: V3 trajectory sampling has 2-point bug
→ Suggested: Fix trajectory Z sampling (nightly finding)
```

**Реальный пример**: Ночное исследование нашло ICRA 2025 paper (Hanut et al.) о sand grading convergence → это привело к внедрению RMS height error метрики → PR #538.

**Урок для команды**: Scheduled tasks + память = контекст не теряется. Даже без ночного агента, `/status` при старте сессии экономит 15 мин.

---

### 2.2 ЧАСТИЧНО РЕШЁННЫЕ

---

#### P8: Контекст теряется при сжатии (context compression)
**Статус**: Checkpoint skill работает, но recall после compression — ручной.
**Что есть**: `memory_store(name="working_state_redrobot", ...)` сохраняет: задачу, ветку, изменённые файлы, что сделано, что осталось.
**Чего не хватает**: Автоматический recall при компрессии. Сейчас приходится вручную просить "вспомни рабочее состояние".
**Путь решения**: Hook на событие `ContextCompression` (если Claude Code добавит такой trigger) → автоматический `memory_recall`.

---

#### P9: Self-contained репо (портабельность)
**Статус**: PR #109 создан, не вмержен.
**Что сделано**: Объединили global + project CLAUDE.md, перенесли все 10 skills, создали setup-device.sh.
**Что осталось**: Убрать дубликаты из parent, обновить .env на всех устройствах, протестировать remote trigger.
**Почему не закрыто**: Низкий приоритет — текущая схема работает.

---

#### P10: Memory staleness (устаревание записей)
**Статус**: Тиры определены (Working 3d, Episodic 30d, Semantic permanent), но автоматическая очистка не enforced.
**Что есть**: `/memory stale` показывает устаревшие записи. `/memory cleanup` — интерактивная очистка.
**Чего не хватает**: Автоматическое напоминание о stale записях при session start. Сейчас 129 записей — через полгода будет 500+, и recall будет зашумлён.

---

### 2.3 НЕРЕШЁННЫЕ / ОТКРЫТЫЕ

---

#### P11: MCP результаты обрезаются
**Проблема**: `memory_recall` может вернуть обрезанный результат если контент длинный.
**Идея**: `anthropic/maxResultSizeChars: 100000` в tool metadata — но неизвестно, поддерживает ли Python MCP SDK поле `_meta`.
**Статус**: Не исследовано.

---

#### P12: Автоматическая верификация агентов
**Проблема**: Агенты всё ещё могут hallucinate результаты. Верификация через `git diff` — ручная.
**Идея**: Post-agent hook, который проверяет `git diff --stat` и отвергает "пустые" результаты.
**Статус**: Claude Code hooks пока не поддерживают event `AgentComplete`.

---

#### P13: Cost tracking программатически
**Проблема**: Anthropic dashboard показывает расход, но нет API для мониторинга.
**Текущее решение**: Ручная проверка.
**Идея**: Parse Anthropic usage page через scraping или ждать API.

---

## 3. Паттерны использования с конкретными примерами

### 3.1 CLAUDE.md — общий контекст проекта

**Проблема**: Каждый разработчик объясняет Claude одно и то же: "мы используем FastAPI", "тесты в pytest", "стиль именования snake_case".

**Решение**: Один файл в корне репо. Claude читает его автоматически.

```markdown
# CLAUDE.md

## Stack
- Backend: FastAPI + SQLAlchemy + Alembic
- Frontend: React 18 + TypeScript + Tailwind
- Tests: pytest (backend), Vitest (frontend)
- CI: GitHub Actions

## Conventions
- API endpoints: /api/v1/<resource>
- Database models: singular (User, not Users)
- Frontend components: PascalCase in src/components/
- Tests mirror src/ structure in tests/

## Architecture decisions
- Auth: JWT tokens, refresh via httpOnly cookie (decided 2026-03-15, PR #42)
- State management: Zustand, not Redux (decided 2026-03-20)
- DB migrations: Alembic autogenerate, manual review required

## Common pitfalls
- Don't import from src/internal/ outside of src/core/
- Always run `alembic check` before creating migration
- Frontend env vars must start with VITE_
```

**ROI**: 30 минут на написание → экономит 5-10 мин в каждой сессии × каждый разработчик.

---

### 3.2 Skills — переиспользуемые workflow

**Проблема**: Повторяющиеся задачи: "создай PR", "проверь CI", "исследуй эту ошибку" — каждый раз объясняешь заново.

**Решение**: Skill = markdown файл с инструкциями. Вызывается через `/command-name`.

```
.claude/skills/
  deploy/SKILL.md       → /deploy — билд + тест + деплой
  review-pr/SKILL.md    → /review-pr 123 — структурированный ревью
  gen-migration/SKILL.md → /gen-migration — Alembic autogenerate + проверка
```

**Пример skill файла** (`.claude/skills/review-pr/SKILL.md`):
```markdown
---
description: Structured PR review with checklist
---

Review PR #$ARGUMENTS following this checklist:
1. Read all changed files (gh pr diff)
2. Check: tests added? Types correct? Error handling? Security?
3. Look for: hardcoded values, missing validation, broken imports
4. Output: Summary + specific file:line comments + verdict (APPROVE/REQUEST_CHANGES)
```

**Реальный пример**: `/delegate #510` → Claude забрал issue, создал ветку, реализовал, создал PR с rich body, запушил. 12 issues за одну сессию — 9 PR.

---

### 3.3 Hooks — автоматические реакции

**Проблема**: Забываешь запустить линтер. Забываешь проверить типы. Claude не знает текущий контекст.

**Решение**: Hooks в `settings.json` — триггерятся на события.

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "startup",
      "hooks": [{
        "type": "command",
        "command": "git status --short && echo '---' && git log --oneline -3"
      }]
    }],
    "PreCommit": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "npm run lint && npm run typecheck"
      }]
    }]
  }
}
```

**Урок**: Hooks — это CI на локальной машине. Бесплатно, мгновенно.

---

### 3.4 Self-improvement цикл (пример для команды)

Это уникальная возможность: Claude может улучшать собственную конфигурацию.

**Цикл**:
```
/self-review → находит проблемы в конфигурации/skills
  → /ideate → оценивает решения (Impact/Effort/Risk)
    → /research → исследует подходы
      → /delegate → реализует
        → /reflect → проверяет результат, сохраняет урок
```

**Реальный пример** (за 2 недели):

| Шаг | Действие | Результат |
|-----|----------|-----------|
| self-review | Нашёл 14 дублированных skills | Issue создан |
| ideate | Оценил 3 подхода: symlinks, post-commit hook, shared dir | Post-commit hook выиграл (Impact 5, Effort 2) |
| research | Изучил Claude Code config resolution (что наследуется) | Нашёл: CLAUDE.md наследуется от parent, skills — нет |
| delegate | Реализовал: единый source + sync hook | PR #106 |
| reflect | Проверил через 3 дня — синхронизация работает | Память обновлена |

**Другой пример — от исследования к коду**:
1. Nightly research нашёл ICRA 2025 paper про sand grading convergence
2. Ключевой инсайт: RMS height error — правильная метрика, не max deviation
3. Это привело к фиксу: `mean_dev` вместо `max_dev` для stagnation detection
4. PR #538 (RMS metric) — прямой результат автономного ночного исследования

**Урок для команды**: Не обязательно такой сложный цикл. Даже простой `/self-review` раз в неделю находит мёртвый код, устаревшие зависимости, пропущенные тесты.

---

## 4. Gap Analysis: где ещё можно расти

### 4.1 Что используем хорошо
- **Memory** — 129 записей, активно используется для решений и контекста
- **Delegation** — 12 issues за сессию, 9 PR
- **Research** — ночной агент, targeted web search, Context7 для документации
- **CI integration** — 5 workflow, event-driven review response
- **Skills** — 12 переиспользуемых workflow

### 4.2 Что используем недостаточно
| Gap | Текущее | Возможное | Effort |
|-----|---------|-----------|--------|
| **Eval suite** | PR #107 создан, не вмержен | Автоматический прогон evals для каждого skill — before/after pass rate | MEDIUM |
| **Context7** | Используем ad-hoc | Автоматический lookup при работе с библиотеками через CLAUDE.md rule | LOW |
| **Desktop agents** | Не используем | Мониторинг CI, автоматический triage при failure | LOW |
| **Claude Code Channels** | Настроен Telegram, редко используем | Уведомления о завершении задач, morning brief | LOW |
| **Structured output** | Не используем | JSON schema для reports, risk assessments — машинно-читаемый output | MEDIUM |
| **Agent SDK** | Исследовали, не внедрили | Headless Jarvis на VPS для 24/7 мониторинга | HIGH |

### 4.3 Что не работает и почему

**Параллельные агенты** (collision problem):
- Два агента редактируют один файл → merge conflict → потерянная работа
- Протокол создан (file-level locking), но не протестирован в реальности
- **Вывод**: параллелизм работает только для НЕЗАВИСИМЫХ задач (разные файлы/модули)

**Worktree isolation** (wrong repo problem):
- `isolation: worktree` клонирует CWD, не target — агенты работают в пустоте
- Это architectural limitation Claude Code, не баг в нашем коде
- **Обходной путь**: агент сам делает `cd` + `git checkout -b`

**Автономная работа без владельца**:
- Работает хорошо для чётко описанных задач (issues с acceptance criteria)
- Ломается на ambiguous задачах — агент уходит в неправильном направлении
- **Вывод**: качество issue description определяет качество автономной работы

---

## 5. Рекомендации для команды: что внедрять и в каком порядке

### Неделя 1: Basics (Zero cost)
1. **CLAUDE.md в каждый репо** — архитектура, конвенции, pitfalls (30 мин)
2. **SessionStart hook** — git status + last 3 commits при старте (5 мин)
3. **`.mcp.json` с GitHub MCP** — issues и PR из терминала (10 мин)

### Неделя 2: Productivity (Low cost)
4. **3-5 skills** для команды: `/review-pr`, `/deploy`, `/gen-migration`, `/investigate-bug`, `/onboard`
5. **PR template** — `.github/pull_request_template.md`, Claude заполняет автоматически
6. **Context7 rule** в CLAUDE.md: "Always check Context7 before answering library questions"

### Неделя 3: Automation (Medium cost)
7. **Copilot review response** — GitHub Action для auto-apply review suggestions
8. **Issue board automation** — label sync, hierarchy validation, staleness detection
9. **Weekly `/self-review`** — находит мёртвый код, устаревшие deps, пропущенные тесты

### Неделя 4: Advanced (High cost, optional)
10. **Память** — file-based для одного девайса, Supabase MCP для cross-device
11. **Nightly research** — автономный агент для continuous learning
12. **Eval suite** — измеряй качество skills, оптимизируй

---

## 6. Метрики: как измерять улучшение

| Метрика | До | После (наш опыт) | Как измерить |
|---------|-----|-------------------|--------------|
| Время "входа в контекст" | 15-20 мин | 2-3 мин | Субъективно, но ощутимо |
| PR cycle time | — | 12 issues → 9 PR за сессию | `gh pr list --json createdAt,mergedAt` |
| Bug investigation | 30-60 мин | 10-15 мин | Время от issue до root cause |
| Boilerplate code | 40-60% рабочего дня | 10-20% | Субъективно |
| Повторные вопросы | Часто | Почти нет (память) | Количество "мы это уже обсуждали" |
| Ночной простой | 100% | Полезная работа | Количество nightly research findings |

---

## Appendix: Полный список skills

| Skill | Команда | Описание | Применимость для команды |
|-------|---------|----------|--------------------------|
| delegate | `/delegate #N` | Автономная реализация issue | HIGH |
| research | `/research topic` | Структурированное исследование | HIGH |
| triage | `/triage` | Health check GitHub board | HIGH |
| status | `/status` | Dashboard при старте сессии | HIGH |
| risk-radar | `/risk-radar` | Сканер рисков (CI, security, stale) | HIGH |
| self-review | `/self-review` | Аудит кодовой базы | MEDIUM |
| checkpoint | `/checkpoint` | Сохранение рабочего состояния | MEDIUM |
| ideate | `/ideate` | Генерация и оценка идей | MEDIUM |
| reflect | `/reflect` | Ретроспектива решений | LOW (solo) |
| self-improve | `/self-improve` | Автономное самоулучшение | LOW (meta) |
| intel | `/intel` | Tech intelligence digest | LOW |
| memory-manager | `/memory` | Управление памятью | LOW (с кастомным MCP) |
| nightly-research | auto 03:00 | Автономное ночное исследование | MEDIUM |

---

## 7. Чужой опыт: как другие компании внедряют Claude Code в команде

### Источники

| Источник | Тип | Ключевой инсайт |
|----------|-----|-----------------|
| [Rasmus Widing](https://rasmuswiding.com/blog/claude-code-for-teams/) | Guide (Dec 2025) | Начинай с 2-3 "believers", не всю команду. CLAUDE.md + 2-3 shared commands = 80% value |
| [davila7/claude-code-templates](https://github.com/davila7/claude-code-templates) | GitHub repo (1000+ stars) | CLI для установки шаблонов по ролям (frontend-dev, reviewer, security). Категории: team/role/stack |
| [MindStudio: Shared Business Brain](https://www.mindstudio.ai/blog/shared-business-brain-claude-code-skills/) | Blog post | Модульный контекст: voice, audience, terminology, edge cases. Каждый skill загружает ТОЛЬКО нужные секции |
| [Weiyuan Liu (Ascenda)](https://weiyuan-liu.medium.com/figuring-it-out-team-customisability-for-claude-code-and-cursor-18b999de7e9d) | Medium (Mar 2026) | Engineering manager. Точно наша проблема: как синхронизировать настройки между репо для команды |

### Общие паттерны (что повторяется у всех)

1. **CLAUDE.md в каждый репо** — это стандарт. Cloudflare, Vercel, и десятки open-source проектов уже делают это.

2. **Трёхслойная архитектура**:
   - Company layer (общий контекст, терминология, ценности)
   - Project/Role layer (стек, конвенции, специфика)
   - Personal layer (индивидуальные preferences)

3. **Начинать с малого**: CLAUDE.md + 2-3 skills → дать команде 2 недели → расширять по feedback.

4. **Не-разработчикам тоже нужен контекст**: MindStudio подчёркивает, что business brain работает для ЛЮБОЙ роли — маркетинг, sales, support. Формат один: кто мы, как говорим, для кого, что избегаем.

5. **Maintenance — главная проблема**: Все авторы предупреждают — CLAUDE.md который не обновляется хуже, чем его отсутствие. Нужен ответственный + регулярный review.

### Что мы берём из чужого опыта

| Паттерн | Применение у нас |
|---------|-----------------|
| Shared repo с шаблонами | `company-claude-setup/` — создан, готов к обсуждению |
| Роли (developer/business/minimal) | 3 роли покрывают все наши кейсы |
| Модульный контекст | CLAUDE.md разбит на секции — каждый берёт что нужно |
| Начать с believers | Петя (уже в теме) → потом остальные программисты → потом бизнес |
| Один ответственный | Пётр на старте, ротация позже |

---

## 8. Командный план: shared repo

Создан прототип: `company-claude-setup/`

### Структура
```
company-claude-setup/
├── CLAUDE.md              ← Общий контекст компании (для ВСЕХ)
├── README.md              ← Инструкция по установке
├── .mcp.json              ← Общие MCP серверы (GitHub)
├── .env.example           ← Шаблон переменных окружения
├── .gitignore
├── .claude/skills/        ← 4 общих skills
│   ├── review-pr/         ← Ревью PR
│   ├── summarize/         ← Саммари документов
│   ├── investigate/       ← Расследование бага
│   └── write-doc/         ← Написание документов
├── roles/
│   ├── developer/         ← Git workflow, код, тесты, MCP
│   ├── business/          ← Документы, клиенты, шаблоны
│   └── minimal/           ← Базовый сетап для любой роли
├── docs/
└── examples/
```

### Как это работает для разных людей

| Человек | Инструмент | Что делает |
|---------|-----------|-----------|
| Программист (Пётр) | Claude Code + VS Code | Клонирует repo, копирует roles/developer + skills в проект |
| Программист (новый) | Claude Code | Клонирует, следует README, настраивает за 15 мин |
| Менеджер / Sales | claude.ai Projects | Загружает CLAUDE.md + roles/business как Knowledge |
| Бухгалтер | claude.ai | Загружает CLAUDE.md + roles/minimal как Knowledge |

### Что нужно решить на собрании

Подготовлена повестка: `docs/team-meeting-agenda.md` — 5 решений с вариантами:
1. Где хранить repo (отдельный / submodule / template)
2. Что включить в общий слой (обязательное + на обсуждение)
3. Какие роли (developer / business / minimal — или другие?)
4. Кто обновляет (один человек / PR / ротация)
5. Как подключить не-разработчиков (Claude Code vs claude.ai Projects)
