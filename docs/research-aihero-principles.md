# AI Hero (Matt Pocock) — принципы для Jarvis

**Дата:** 2026-04-30
**Источник:** Matt Pocock — aihero.dev (~54K подписчиков, бывший Vercel DevRel, Total TypeScript). AI Hero — его образовательный бренд по AI engineering: курсы, посты, skills repo.
**Зачем этот док:** перенять его философию в трёх плоскостях — (1) Jarvis как инструмент в Claude Code, (2) Jarvis как проект, над которым работаю, (3) личный devloop.

---

## 1. Ядро философии

Pocock — не "AI-евангелист", а **инженер, который против vibe coding**. Его тезис: AI не сломал software engineering, наоборот — поднял ставки на фундамент. Хорошая архитектура, тесты, обратная связь нужны больше, чем когда-либо, потому что агент всё это использует как сигнал.

> "Everyone thinks AI is a paradigm shift… I disagree."
> "The world still belongs to the builders."
> "Garbage codebase → garbage AI output."

---

## 2. 12 принципов

### A. Про сам code и engineering

1. **Real engineering > vibe coding.** AI требует *усиления* фундамента, а не его отмены. Модульность, тестируемость, чистые интерфейсы рулят.
2. **Deep modules, не shallow** (Ousterhout). Маленький интерфейс, большая скрытая реализация. AI по умолчанию рожает горы мелких single-purpose файлов — это надо ломать намеренно.
3. **Vertical slices / tracer bullets** (Pragmatic Programmer). Каждая задача проходит весь стек: schema → service → API → frontend. AI любит горизонтально (всё DB, потом всё API…) — обратная связь приходит на третьей фазе, поздно. Режь вертикально.
4. **Domain language как контекст.** Сгенерируй glossary (DDD ubiquitous language) и скармливай агенту. Когерентность и intent-alignment растут заметно.

### B. Про работу с LLM

5. **Evals = unit tests AI-инженера.** "Your app is only as good as its evals." Это способ выжать предсказуемость из вероятностной системы. Без них продакшн-LLM-приложение не существует.
6. **Smart zone ≈ 100K токенов.** Дальше — "dumb zone": качество reasoning рушится. Большие контекстные окна — это "shipping more dumb zone". Отсюда ритм: короткие сессии, свежие контексты, **Plan / Execute / Clear**.
7. **Свежий контекст для review.** Reviewer должен крутиться в чистом окне. Если та же сессия ревьюит свой код — она уже в dumb zone.
8. **Treat agents like humans with no memory.** Им нужны "strict processes to compensate". Отсюда — repo-level skills и playbooks, а не вибы.

### C. Про планирование и процесс

9. **Reach shared understanding *before* writing a plan.** PRD — это вход для следующей фазы, а не human-readable артефакт. Ценность — в выравнивании, не в документе. Отсюда `/grill-me`.
10. **Don't bite off more than you can chew.** Скоупь задачи под smart zone. Декомпозируй на independently grabbable issues с явными зависимостями. Глубина планирования > амбициозность задачи.
11. **TDD — самый надёжный рычаг качества для агентов.** Red-green-refactor — "the most consistent way to improve agent outputs". Тесты ещё и являются runtime feedback'ом — без них агент летит вслепую.
12. **Tight, automated feedback loops.** Типы, браузер, тесты, линтеры — всё что даёт агенту ground truth без человека в цикле. Качество вывода ограничено "the quality of the codebase's architecture and feedback loops".

---

## 3. `/grill-me` — техника

**Что это:** skill в Claude Code (короткий промпт в репо), который заставляет LLM **интервьюировать тебя** по одному вопросу за раз про план, обходя branch'и дерева решений и резолвя зависимости между ними. Ядро промпта:

> "Interview me relentlessly about every aspect of this plan until we reach a shared understanding."

**Как работает на практике:** 40–80+ вопросов, ~25K токенов на сессию (внутри smart zone). Цель — *не* PRD, а **shared wavelength** между тобой и агентом перед следующей фазой.

> "I didn't need an asset, I didn't need a plan — I needed to be on the same wavelength as the AI as my agent."

**Цепочка skills у Pocock:** `/grill-me` → `/to-prd` → `/to-issues` → `/tdd` → `/improve-codebase-architecture`. Каждая фаза — отдельная сессия со свежим контекстом.

**Применение к моим задачам:** перед любым нетривиальным фичером в jarvis/redrobot — прогнать `/grill-me` (или его адаптацию) и только потом писать код или issue. Особенно для архитектурных решений.

---

## 4. Catchphrases

- **Smart zone / dumb zone** — про окно токенов
- **Vibe coding vs real engineering** — основная риторическая ось
- **Plan / Execute / Clear** — ритм длинных сессий
- **Tracer bullets / vertical slices** — про декомпозицию
- **Deep modules / shallow modules** — про архитектуру
- **"Treat agents like humans with no memory"**
- **"Garbage codebase → garbage AI output"**
- **"From Zero to AI Hero"** — бренд

---

## 5. Маппинг на 3 слоя

### 5.1 Jarvis как инструмент (Claude Code контекст)

Что встроить в `SOUL.md` / `CLAUDE.md` / always-load:

| Принцип | Куда | Как сформулировать |
|---|---|---|
| Smart zone (~100K) | always-load rule | "Если сессия > ~100K — делай Plan/Execute/Clear: записать состояние в memory, начать свежее окно" |
| Vertical slices | feedback rule | "Декомпозируй задачи вертикально: каждая issue должна проходить весь стек до проверяемого результата, не 'сначала всю схему'" |
| Deep modules | feedback rule | "Перед созданием третьего файла на одну фичу — спроси, не должно ли это быть одним deep module" |
| TDD как feedback | already in CLAUDE.md (transform tasks into verifiable goals) | усилить: "если фича без теста — она не done" |
| Fresh context для review | new rule | "Code review своих же PR — в свежей сессии, не там где писал" |
| Domain glossary | новая практика | сгенерировать `docs/glossary.md` (ubiquitous language jarvis: pillar/skill/memory/outcome/FOK/digital twin…) и подгружать |

**Конкретные действия:**
- [ ] Добавить feedback memory `aihero_smart_zone_rhythm` (Plan/Execute/Clear, порог ~100K)
- [ ] Добавить feedback memory `aihero_vertical_slices` (issue должна доходить до проверяемого результата)
- [ ] Добавить feedback memory `aihero_deep_modules` (не плодить мелкие файлы)
- [ ] Сгенерировать `docs/glossary.md` через сессию с grill-me
- [ ] Создать skill `/grill-me` адаптированный под jarvis (интервьюирует меня по issue/feature перед /implement)

### 5.2 Jarvis как проект

Куда вписать в redesign / pillars:

- **Pillar "Memory"** уже идёт к FOK calibration — это и есть evals-driven подход. Усилить формулировку: память без калибровки = vibes.
- **Новый sub-pillar или принцип**: **Evals everywhere**. Не только для memory, а для каждого LLM-touching куска (autonomous-loop, decisions, summarization). Без evals — фича в "dumb zone проекта".
- **Workflow**: текущий `/implement` это то, что у Pocock называется TDD-фаза. Не хватает явных стадий до неё — `grill-me` → `to-prd` → `to-issues`. У меня уже есть `/research`, `/delegate`, но нет фазы "интервью перед планом". Это gap.
- **Architecture review**: добавить регулярный `/improve-codebase-architecture` (как у Pocock) — он смотрит на репо целиком, ищет shallow modules, ломает их. Можно сделать раз в спринт.

**Конкретные действия:**
- [ ] Issue: "Add /grill-me skill — interview-driven planning before /implement"
- [ ] Issue: "Add /architecture-review skill — sprint-end pass для глубоких/мелких модулей"
- [ ] Discussion: "Evals beyond memory — apply to autonomous-loop / decisions / summarization"
- [ ] Обновить `docs/design/jarvis-v2-redesign.md` — секция "engineering principles" со ссылкой на этот док

### 5.3 Личный devloop

Что я делаю по-другому в день:

- **Перед нетривиальной задачей** — `/grill-me` сам себя (или попросить Jarvis): на 30 минут вопросов **до** написания кода. Если не выдерживаю — это сигнал что задача не понята.
- **Plan / Execute / Clear как ритм рабочего дня**, не только сессии: утро — плэннинг (свежий контекст), потом блоки execute, потом clear (записать что было, начать чистое окно).
- **Vertical slices в собственной работе**: даже mini-задача должна доходить до checked-in result, не "ещё на одной фазе застрял".
- **Tacit knowledge через grill-me**: техника не только для планирования. Использовать её **на самом себе** для извлечения опыта — садиться раз в неделю и просить агента грилить меня про какое-то решение / провал / интуицию. Складывать в memory как `tacit_*`.
- **Свежие глаза**: review своего же PR — в новой сессии. Перестать читать diff в той же ветке мысли где писал.
- **"Don't bite off more than you can chew"** как маркер усталости: если задача не лезет в smart zone — это не про токены, это про мою рабочую память. Декомпозировать.

**Конкретные действия:**
- [ ] Завести memory `personal_workflow_aihero_adoption` с этим списком практик
- [ ] Поставить scheduled задачу: раз в неделю — grill-me про последнее значимое решение, результат → memory как `tacit_*`
- [ ] Использовать `/grill-me` (после создания) хотя бы 3 раза за следующие 2 недели — потом отрефлектировать через `/reflect`

---

## 6. Honest gaps (что не подтверждено первоисточником)

- Прямые цитаты выше — реконструкция из вторичных источников (BigGo, StartupHub, vibesparking, Hacker News): несколько постов на aihero.dev возвращали 404/500 при автоматическом fetch. Перед использованием конкретных формулировок в SOUL — стоит зайти руками на:
  - https://www.aihero.dev/what-are-evals
  - https://www.aihero.dev/use-the-grill-me-skill-k029d
  - https://www.aihero.dev/my-grill-me-skill-has-gone-viral
- YouTube workshop "AI Coding For Real Engineers" — самый плотный единый источник, transcript не вытащил. Если нужна точная фразировка — посмотреть.
- Позиция по RAG / fine-tuning / MCP — тонкий слой данных. Скорее прагматик: skills + Claude Code native, без религии.
- Слово "taste" не его. Если хочется принципа про вкус — это моя добавка, не его catchphrase.

---

## 7. Источники

- [aihero.dev — homepage](https://www.aihero.dev/)
- [aihero.dev/posts — index](https://www.aihero.dev/posts)
- [Your App Is Only As Good As Its Evals](https://www.aihero.dev/what-are-evals)
- [5 Agent Skills I Use Every Day](https://www.aihero.dev/5-agent-skills-i-use-every-day)
- [My 'Grill Me' Skill Went Viral](https://www.aihero.dev/my-grill-me-skill-has-gone-viral)
- [Use The /grill-me Skill](https://www.aihero.dev/use-the-grill-me-skill-k029d)
- [Claude Code for Real Engineers (cohort 2026-04)](https://www.aihero.dev/cohorts/claude-code-for-real-engineers-2026-04)
- [mattpocock/skills (GitHub)](https://github.com/mattpocock/skills) — реальные skill-файлы из его `.claude`
- [@mattpocockuk on X](https://x.com/mattpocockuk)
- [AI Coding For Real Engineers (YouTube workshop)](https://www.youtube.com/watch?v=-QFHIoCo-Ko)
- [It Ain't Broke: Why Software Fundamentals Matter (YouTube talk)](https://www.youtube.com/watch?v=v4F1gFy-hqg)
- [Smart Zone is 100K tokens — recap](https://finance.biggo.com/news/e7209c094224b09c)
- [Skills deep dive — 17 skills dissected](https://www.vibesparking.com/en/blog/ai/2026-04-07-mattpocock-skills-analysis/)
