# Pocock vs Jarvis: полная карта рабочих процессов по сценариям

> Research-документ (skill:research, 2026-07-31). Сравнение системы Matt Pocock
> (mattpocock/skills, dictionary-of-ai-coding, aihero.dev) и системы jarvis —
> от идеи до продукта, по сценариям. Это исследование, не решение: любое
> заимствование идёт через /grill.

## Резюме

Две системы решают один и тот же набор проблем (statelessness модели, деградация
внимания, потеря информации, ложные предположения о знании) — и **обе
prevention-first**: артефакты как дефолтный носитель важного у обоих. У Pocock —
spec/ticket/ADR/CONTEXT.md-глоссарий/AGENTS.md; у jarvis — issues/milestone-PRD +
«save immediately, don't batch» + record_decision в момент резолюции + «no state
in static storage». Реальная асимметрия уже и сидит в двух местах:

- **Судьба контекста сессии.** Pocock сессию выбрасывает (clear; компрессия
  санкционирована только на границах фаз — см. ось 1); ставка — всё важное уже
  записано, сессия одноразовая. Jarvis дополнительно несёт контекст сессии
  через компрессию и держит recovery-машинерию второго эшелона (pre-compact
  snapshot, SessionStart re-injection, working_state, /end-реконсиляция) для
  того, что не успело из сессии в артефакты.
- **Путь доставки знания.** У Pocock — always-load файлы и тикеты на трекере:
  отказ громкий (не записано → свежая сессия не знает, видно с первого ответа).
  У jarvis — вероятностный recall поверх Supabase: богаче по типам (outcomes,
  goals, calibration), но отказ тихий — промах recall неотличим от «учёл и
  решил иначе».

Ключевая поправка к исходной гипотезе: **«у него нет памяти» — неверно.**
В словаре Pocock есть отдельная статья Memory system («A system that attempts
to make an agent stateful across sessions») и AGENTS.md. Различие не
«память vs нет памяти», а **носитель и режим отказа**: у Pocock — файлы,
загружаемые целиком или по context-pointer, курируемые человеком, с громким
отказом; у jarvis — запросная БД (Supabase) с автоматическим recall, богаче по
типам (decisions, outcomes, goals, calibration), но с тихим отказом.

Вторая поправка: **jarvis уже реализует главную защиту, которую рекомендуют
статьи про компрессию.** Paper «Governance Decay» (arXiv:2606.22528) показывает:
компрессия молча стирает in-context-ограничения; лекарство — переинъекция
ограничений после компрессии. SessionStart:compact-хук jarvis (always_load +
SOUL + snapshot) — ровно этот механизм. То есть «я полагаюсь на компрессию —
возможно, главная ошибка» точнее формулируется как «я полагаюсь на компрессию,
и половина моей инфраструктуры существует, чтобы это пережить; Pocock той же
ценой просто не платит».

## Терминологический каркас Pocock (dictionary-of-ai-coding, verbatim-выжимки)

- **Stateless**: «if you want something remembered across sessions, you have to
  write it down somewhere the agent will read it back».
- **Session**: «A new session starts from nothing… What survives is the
  filesystem». «One task per session keeps the context relevant».
- **Smart zone / dumb zone**: деградация начинается ~125–150K токенов; «Plan
  around the smart zone, not the window».
- **Attention budget/degradation**: «You recover by removing context, not adding
  more. Re-pasting the ignored instruction… helps only briefly».
- **Handoff**: два механизма переноса. Handoff artifact — «You can read and
  correct it before anything depends on it; reusable across many sessions».
  Compaction — «Automatic and cheap; harder to inspect; feeds one successor».
  «The visible failure of a bad handoff is relitigation».
- **Compaction**: «Lossy by design… Contrast with clearing… compaction tries to
  carry the essentials across; clearing bets they're already written down
  somewhere better». Autocompact — «lossy at a moment you didn't choose».
- **Spec / Ticket**: spec — дом работы, переживающий сессии; ticket — «scoping
  one session of work… completable before the session drifts out of the smart
  zone — and that constraint is testable».
- **Primary/Secondary source**: «An agent that read a doc inherits the doc's
  staleness; an agent that read the code is reading the current truth».
  Secondary sources «are lossy… and they drift». Хороший secondary несёт
  context pointer на свой primary.
- **Memory system**: «memories are secondary sources, so they drift… A memory
  system needs pruning, the same way AGENTS.md does».

## Карта: где что хранится

| Что | Pocock | Jarvis |
|---|---|---|
| Сырая идея / туман | Wayfinder-map (parent issue: Investigate/Grilling/Research/Implementation tickets + «Not yet specified», «Out of scope») | Нет персистентного артефакта; /reason → /grill в одной сессии; итог сразу в milestone PRD |
| Спека работы | Spec (PRD/design doc/plan.md) — мутирует по ходу | Milestone description (PRD) + issues |
| Единица работы | Ticket = одна сессия, размер тестируем («сессии деградируют до конца работы → тикеты слишком большие») | Slice = один PR (размер привязан к PR, не к сессии) |
| Решения + rationale | ADR (только: hard-to-reverse + surprising + real trade-off) | record_decision в Supabase (триггер-лист шире: любая имплементация, confidence<0.7, policy-изменения) |
| Язык домена | CONTEXT.md = «a glossary and nothing else» | CONTEXT.md = полная доменная модель (глоссарий + потоки + инварианты) |
| Коррекции/предпочтения | AGENTS.md («corrected twice → candidate line»), короткий, остальное за context pointers | Память type=feedback + CLAUDE.md/SOUL; авто-recall хуками |
| Рабочее состояние | Tickets + frontier (что разблокировано) | working_state_jarvis + issues + labels |
| Перенос между сессиями | Handoff artifact (файл в temp OS, «suggested skills», ссылки вместо дублей) | Компрессия + pre-compact snapshot + SessionStart re-injection |
| История отказов | — (нет аналога) | outcome_list / outcome_record, calibration |

## Сценарии

### 1. Спонтанная идея

- **Pocock**: размер определяет вход. Помещается в обозримую работу →
  /grill-with-docs → /to-spec → /to-tickets → /implement. Слишком большая/туманная →
  /wayfinder: chart-the-map, карта живёт в трекере, ветки явные («Not yet
  specified» — известные неизвестные, «Out of scope» — отвергнутое с причиной).
  UI-идея → /prototype до спеки.
- **Jarvis**: /reason (интуиция без плана) → /grill → /to-spec → /to-tickets.
  Итог: milestone + PRD + record_decision. **Разрыв**: между «смутной идеей» и
  «milestone со слайсами» нет персистентного промежуточного артефакта — /grill
  однопроходный, ветки и отвергнутые варианты уходят в decision-эпизоды, но
  карта неисследованного не сохраняется.

### 2. Найдена проблема (баг)

- **Pocock**: diagnosing-bugs — 6 фаз, и Phase 1 (построить feedback loop) —
  это и есть скилл: 10 способов построить петлю; критерий завершения — одна
  уже запущенная команда, red-capable, детерминированная, быстрая, запускаемая
  агентом; «No red-capable command, no Phase 2». Дальше: reproduce + minimise →
  3–5 ранжированных фальсифицируемых гипотез (показать человеку) → одна
  переменная за раз с [DEBUG-xxxx]-тегами → regression-тест ДО фикса и только
  на правильном шве («If no correct seam exists, that itself is the finding») →
  post-mortem «что бы это предотвратило» с handoff в
  /improve-codebase-architecture ПОСЛЕ фикса.
- **Jarvis**: /diagnose (Phase 1 — построить feedback loop до дебага; у него же
  и позаимствован), маршрутизация /file-issue vs fix-inline (#428, <30 мин →
  чинить сразу с `[no-issue]`), outcome_record после. Паритет по ядру; у Pocock
  жёстче гейт входа в дебаг и явный пост-мортем-хвост, у jarvis —
  outcome-трекинг, которого у Pocock нет.

### 3. Тупик (агент застрял)

- **Pocock**: чистка контекста — первый рефлекс. Словарь, статья Clearing,
  дословно: «It's stuck looping on the failing test» → «Just clear it — start a
  fresh session with the plan doc and the test file. No point fighting the
  existing context».
- **Jarvis**: рефлекс — продолжать в той же сессии (компрессия как предохранитель);
  /diagnose для багов; outcome_list ловит паттерн (2+ отказа → root cause). SOUL
  уже содержит правило «Stay in the smart zone… Plan / Execute / Clear», но
  практика сессий ему противоречит — правило есть, привычки нет. **Это самый
  чистый кандидат на заимствование**: «застрял → clear + reload plan doc»
  дешевле, чем «застрял → дожимать в раздутом контексте».

### 4. Не хватает информации

- **Pocock**: grilling разделяет «facts-from-environment» (агент добывает сам) и
  «decisions-from-user» (спрашивает). /research: фоновый агент, primary sources
  only, результат — cited markdown в репо. В wayfinder — Research-tickets как
  параллельные AFK-агенты (PR #538: общие блокеры должны оставаться видимыми на
  frontier).
- **Jarvis**: /research (4 канала, граундинг-правило, бюджеты), label
  needs-research + гейт в /delegate, context7-правило, GROUNDING-pass в /grill.
  Функционально эквивалентно, у jarvis жёстче протокол, у Pocock — дешевле вход.

### 5. Нет доступа к ресурсам

- **Pocock**: в добытых источниках прямого механизма нет (не найдено — честный
  пробел, не «отсутствует у него»).
- **Jarvis**: явная доктрина деградации: fallback-цепочки (firecrawl down →
  WebSearch + infrastructure-blocked waiver), review-blind carve-out (недоступный
  CI-раннер), device-gating MCP (`x-jarvis-requires-env`). Здесь jarvis
  систематичнее — следствие мульти-устройства и AFK-запусков.

### 6. Непонятно, что делать дальше

- **Pocock**: frontier wayfinder-карты = «что разблокировано сейчас»; decision
  tickets блокируют implementation tickets. Отказ из поля: #625 — Codex
  проигнорировал label wayfinder:grilling и «решил» decision-ticket кодом; #518 —
  decision tickets ошибочно помечены ready-for-agent. Т.е. машинная читаемость
  «это решение, не задача» — известная слабость даже у автора.
- **Jarvis**: goal_list (приоритеты в SessionStart), /goals, /status
  (анкерный), milestone-иерархия. Ответ «что делать» синтезируется из целей и
  открытых milestone, а не читается с карты. Работает для sized-работы; для
  тумана — см. сценарий 1.

### 7. Что-то забылось

- **Pocock**: забывание — норма, не отказ. «The question isn't why it didn't
  learn — it can't — but where that correction should be written down». Правило
  двух коррекций → строка в AGENTS.md. Забытое всплывает громко (свежая сессия
  не знает — видно сразу).
- **Jarvis**: три recall-хука (SessionStart, UserPromptSubmit, PreToolUse) +
  топик-recall в скиллах + /learn (очередь кандидатов) + /curate (гигиена).
  Богаче и автоматичнее, но отказ тихий: если recall не поднял запись, никто не
  узнаёт. Метрика empty-memories_used и show-and-continue («leaning on: …») —
  существующие, но частичные ответы на это.

### 8. Конфликт мнений (человек vs агент)

- **Pocock**: grilling — «one question at a time… don't act until shared
  understanding confirmed»; факты — от среды, решения — от человека. Финальное
  слово за человеком после выравнивания.
- **Jarvis**: SOUL «Fallibility is the fixed point» — обе стороны систематически
  неправы, вес у верификации, не у уверенности; CRITIC-подагенты в /grill,
  cross-context review против сикофантии, record_decision фиксирует
  альтернативы с причинами отклонения. Jarvis-механика глубже (антисикофантия
  как явная поверхность атаки); у Pocock проще и дешевле.

### 9. Конфликт с кодом (убеждение противоречит коду)

- **Pocock**: доктрина primary source: «Point it at the actual retry module —
  work from the primary source when the behaviour matters»; domain-modeling
  требует cross-reference глоссария с кодом.
- **Jarvis**: «Verify before assuming implemented» (grep символа + чтение пути
  end-to-end + тест, урок tool-width Z), sibling-grep. Совпадение по сути;
  jarvis формализовал в процедуру.

### 10. Потеряна информация

- **Pocock**: профилактика. Важное никогда не живёт только в сессии; handoff
  artifact «lives on disk where you can read and correct it before anything
  depends on it»; компрессия допустима только на границе фазы и с промптом «что
  сохранить»; secondary source обязан нести context pointer на primary
  (транскрипт на диске).
- **Jarvis**: та же профилактика как дефолт («save immediately, don't batch»,
  record_decision в момент резолюции, состояние в GH, не в markdown) — плюс
  второй эшелон восстановления для просочившегося: pre-compact snapshot (в этой
  самой сессии блок «Pre-Compact Recovery» — живое доказательство),
  working_state, полный transcript .jsonl, /end-реконсиляция (post-hoc decisions
  с маркером). Асимметрия не «предотвращение vs восстановление», а в том, что
  живёт в сессии: у Pocock по конструкции — ничего важного (сессия одноразовая),
  у jarvis mid-task-контекст живёт в сессии до ближайшей записи — recovery
  закрывает это окно. Стоимость ошибки: у Pocock — дисциплина записи, у
  jarvis — интервал между потерей и обнаружением, в котором агент действует на
  неполном знании.

### 11. Человек думает, что агент знает, — а агент не знает

- **Pocock**: структурно громкий отказ. Свежая сессия пуста по определению —
  ложное предположение разбивается о первый же ответ. Словарь прямо
  инсценирует: «Why does it forget the convention every time I clear?» → «write
  it to AGENTS.md». Человек быстро выучивает: не записано → не существует.
- **Jarvis**: структурно тихий отказ — и это подтверждает исходное опасение
  («слишком полагаюсь на память, а она может недостаточно отрабатывать»).
  Память создаёт у человека ожидание знания; recall вероятностный; промах
  неотличим от «агент учёл и решил иначе». Существующие смягчения:
  show-and-continue, empty-memories_used-метрика, обязательный skill-name в
  recall-запросе. Это главное место, где дизайн Pocock честнее дизайна jarvis.

### 12. Агент думает, что человек знает, — а человек не знает

- **Pocock**: grilling существует ровно для этого: «Interview the user
  relentlessly» до подтверждённого общего понимания; в wayfinder невыясненное
  становится Grilling-ticket и блокирует работу на карте.
- **Jarvis**: /grill (наследник grill-me) + обязательный Grill trigger checkbox
  перед /implement и /delegate + «Stated plans beat assumed plans» + вопрос
  «milestone для этих N слайсов?». Паритет; jarvis добавил принудительный
  триггер, Pocock — персистентность невыясненного (тикеты на карте).

## Дополнительные сценарии (производные — «и т.д.»)

Заданные 12 сценариев не исчерпывают пространство. Ниже — 14 сценариев,
выведенные из полного чтения обеих систем (в т.ч. шести скиллов, дочитанных
после первой версии: code-review, resolving-merge-conflicts, triage,
diagnosing-bugs, codebase-design, ask-matt).

### 13. Вопрос не решается разговором — нужен запускаемый ответ

- **Pocock**: /prototype — узаконенный detour: runnable UI-эксперимент вместо
  спора о вкусах; мостится handoff'ами в обе стороны (из wayfinder/grilling в
  прототип и обратно). Wayfinder «produces decisions, not deliverables» —
  прототип и есть способ добыть решение, когда слова не работают.
- **Jarvis**: выделенного механизма нет — spike происходит внутри /reason или
  /implement без артефакта и без явного «это одноразовый код». Известный разрыв
  (prototype-скилл ранее сознательно не адаптировался).

### 14. Задача оказалась больше, чем казалась (scope explosion)

- **Pocock**: размер тикета тестируем — «сессии деградируют до конца работы →
  тикеты слишком большие»; ask-matt ветвит на входе («multi-session build?» →
  wayfinder); mid-flight — /handoff и разрез на тикеты.
- **Jarvis**: грил-чекбокс и вопрос «milestone для этих N слайсов?» ловят
  разрастание ДО старта; mid-flight встроенного сигнала нет — слайс привязан к
  PR, не к сессии (см. Trade-offs). Разрыв именно в mid-flight-детекции.

### 15. Прерывание / переключение на другую работу

- **Pocock**: «/handoff forks» — ветвление в новый тред с читаемым артефактом;
  старый тред остаётся живым для возврата.
- **Jarvis**: working_state-чекпоинт (+ memory_delete по завершении), draft-PR
  как парковка. Паритет; чекпоинт jarvis структурированнее, файл Pocock —
  читаемее и правится руками до того, как на него обопрутся.

### 16. Работа «готова» — проверка

- **Pocock**: code-review — две оси ПАРАЛЛЕЛЬНЫМИ сабагентами: Standards
  (фиксированный baseline из 12 Fowler-smells: Mysterious Name, Duplicated
  Code, Feature Envy, Data Clumps, Primitive Obsession, Repeated Switches,
  Shotgun Surgery, Divergent Change, Speculative Generality, Message Chains,
  Middle Man, Refused Bequest; репо-стандарты перекрывают baseline) и Spec
  (соответствие намерению). Результаты сознательно НЕ сливаются и не
  ранжируются вместе — чтобы одна ось не маскировала другую. Ревью — всегда
  свежая сессия, не та, что писала код.
- **Jarvis**: /verify (git diff поверх self-report — ловит fabricated «done»),
  CI-гейт review с fail-closed verdict-парсингом, четыре merge-гейта. У jarvis
  проверка встроена в merge-путь (машина), у Pocock — в структуру самого ревью
  (процедура). Взаимодополняющие, не конкурирующие.

### 17. Входящий поток внешних запросов

- **Pocock**: /triage — конечный автомат: категория (bug/enhancement) ×
  состояние (needs-triage / needs-info / ready-for-agent / ready-for-human /
  wontfix); «PR — это issue с приложенным кодом»; проверка клейма
  воспроизведением ДО грила; triage применяется только к issue, которые ты не
  создавал.
- **Jarvis**: /triage тоже есть (state machine, ready-for-agent); поток мал
  (solo-принципал). Различие в деталях: у Pocock отклонённое уходит в
  .out-of-scope/*.md, и triage ОБЯЗАН свериться с этой базой перед повторным
  рассмотрением (см. сценарий 19).

### 18. Запрос на то, что уже реализовано

- **Pocock**: redundancy check в triage — искать существующую реализацию по
  доменному концепту, отчитаться, где искал → wontfix + указатель на
  существующее (и это НЕ .out-of-scope: не отклонение, а дубль возможности).
- **Jarvis**: зеркальное правило «Verify before assuming implemented» — тот же
  «grep прежде чем верить», но в обратную сторону (не заявляй сделанным без
  проверки). Пара правил закрывает обе ошибки; явного шага «а не существует ли
  это уже в коде?» при заведении issue у jarvis нет (дубль-чек /file-issue
  ищет по issues, не по коду).

### 19. Отвергнутая идея всплывает снова (relitigation)

- **Pocock**: именованный режим отказа — «The visible failure of a bad handoff
  is relitigation». Тройная защита: .out-of-scope/*.md как курируемая база
  отклонений (triage обязан свериться), секция «Out of scope» на
  wayfinder-карте, ADR.
- **Jarvis**: alternatives_considered в record_decision + вероятностный recall.
  Тот же loud/silent-паттерн, что в оси 2: у Pocock — детерминированное чтение
  файла отклонений, у jarvis — надежда, что recall поднимет старое решение в
  нужный момент.

### 20. Столкновение параллельных потоков (merge-конфликт)

- **Pocock**: resolving-merge-conflicts — на каждый конфликт найти primary
  sources обеих сторон (commit messages, PRs, issues), сохранить оба намерения;
  несовместимы → цель мержа + зафиксированный trade-off; никогда не выдумывать
  поведение, никогда не `--abort`. Плюс граф зависимостей тикетов делает
  параллелизм безопасным по построению.
- **Jarvis**: предотвращение столкновений — sandcastle worktrees, правило
  re-check main mid-run (`implement_concurrent_fix_reaudit`). Процедуры
  РАЗРЕШЕНИЯ конфликта (как у Pocock) нет — при реальном конфликте jarvis
  действует ad-hoc.

### 21. Незнакомая область кода

- **Pocock**: CONTEXT.md-глоссарий + читать ADR области (diagnosing-bugs прямо
  предписывает); codebase-design даёт словарь навигации: deep module, seam,
  adapter, deletion test, «One adapter means a hypothetical seam. Two adapters
  means a real one».
- **Jarvis**: /zoom-out + CONTEXT.md (полная доменная модель). Паритет.

### 22. Ошибка повторяется

- **Pocock**: один механизм — «corrected twice → candidate line» в AGENTS.md.
- **Jarvis**: outcome_list (2+ отказа в области → root cause, не ретрай),
  /reflect, и главное — эскалация по протокольным слоям: Tier 1 строка →
  Tier 2 hook → Tier 3 skill-gate, когда строка доказала недостаточность.
  Здесь jarvis существенно глубже: систематическая эскалация носителя правила
  против одной строки в файле.

### 23. Возвращение после длинной паузы

- **Pocock**: spec + tickets + frontier — любой свежий агент читает трекер и
  видит, что разблокировано; «вспоминать» нечего по конструкции.
- **Jarvis**: SessionStart-хук (baseline + оффер working_state), /status
  (анкерный), /last-work-report. Паритет; оба решают через среду, не через
  память сессии.

### 24. После фикса: что бы это предотвратило

- **Pocock**: Phase 6 diagnosing-bugs — post-mortem сразу после фикса
  («you have more information now than when you started») с handoff в
  /improve-codebase-architecture; подтверждённая гипотеза — в commit message.
- **Jarvis**: outcome_record + architecture sweep на закрытии milestone (в
  свежей сессии) + /self-improve. У jarvis шире (успехи тоже записываются —
  у Pocock success-capture нет вовсе), у Pocock жёстче привязка к моменту:
  сразу после фикса, а не по семантической каденции.

### 25. Кому отдать работу: агент vs человек

- **Pocock**: явные состояния ready-for-agent vs ready-for-human (человеку —
  judgment calls, external access, design decisions, manual testing);
  decision-tickets — никогда агенту (и ровно это ломается в поле: #625, #518).
- **Jarvis**: pre-dispatch gate в /delegate, AFK-fit/sandcastle-семантика,
  «Jarvis решает, что сабагенту, что inline». Паритет по идее; критерии jarvis
  богаче, маркировка Pocock — читаемее (один label на трекере).

### 26. Накопился процессный мусор

- **Pocock**: pruning — именованная обязанность: AGENTS.md «needs pruning»,
  memory «needs pruning, the same way AGENTS.md does», .out-of-scope как
  курируемая KB, triage oldest-first.
- **Jarvis**: /curate, /learn (cap 20, идемпотентный), ceiling:-маркеры как
  grep-able debt list, /triage. Паритет по покрытию; jarvis автоматизированнее,
  Pocock — дешевле в поддержке.

## Две фундаментальные оси

### Ось 1: компрессия vs fresh sessions

Позиция Pocock точнее, чем secondary-рекап «избегает компрессии» (biggo).
Primary source — его роутер ask-matt — прямо санкционирует /compact: «Use it at
intentional breaks between phases… Don't compact mid-phase. /handoff forks;
/compact continues». Там же правила контекст-гигиены: grill → spec → tickets —
в ОДНОМ непрерывном окне («don't compact or clear until after /to-tickets»);
smart zone ~120k; при приближении к границе до /to-tickets — не дожимать на
деградировавшем контексте, а /handoff в свежий тред; каждый /implement — всегда
свежая сессия. Словарь дополняет: компрессия — легитимный, но худший из двух
механизмов переноса («harder to inspect; feeds one successor»), autocompact
особенно опасен («lossy at a moment you didn't choose»). Итого его запрет — не
на компрессию вообще, а на компрессию mid-phase и на автоматическую. Вместо неё
на разрывах — /handoff-skill (документ в temp OS, ссылки вместо дублей, redact
secrets).

Данные (Channel 3): arXiv:2606.22528 (Governance Decay/ConstraintRot) —
компрессия молча удаляет in-context-ограничения в long-horizon агентах;
arXiv:2601.07190, 2510.06727, 2512.16970 (PAACE), 2510.00615 (ACON) — деградация
рассуждений от раздутого контекста И lossy-компрессия как основная поверхность
отказа. Эмпирика поддерживает обе половины позиции Pocock: длинные сессии
плохи, и компрессия как лекарство плоха по-своему.

Положение jarvis: компрессия — рабочий режим, но обвешенный ровно теми
контрмерами, которые рекомендует литература: re-injection ограничений после
компрессии (SessionStart:compact — always_load + SOUL), snapshot как context
pointer на primary source (транскрипт), working_state как handoff-artifact-лайт.
Т.е. «полагаюсь на компрессию» ≠ «не защищён от компрессии». Незакрытым
остаётся то, что не покрыто always_load и snapshot: mid-task-решения, не успевшие
в память до autocompact, — окно Governance Decay.

### Ось 2: память vs stateless-процессы

Ложная дихотомия. Оба stateful через среду; различие в носителе:

| | Pocock | Jarvis |
|---|---|---|
| Носитель | Файлы (AGENTS.md, CONTEXT.md, ADR, spec/ticket) | Supabase (queryable) + файлы |
| Доставка | Always-load + context pointers (агент сам решает следовать) | Авто-recall хуками + топик-запросы |
| Кто курирует | Человек, руками | /learn + /curate + classifier |
| Типы | Правила, глоссарий, решения (ADR) | + outcomes, goals, calibration, working_state, credentials-metadata |
| Режим отказа | Громкий (не записано → сессия не знает, видно сразу) | Тихий (recall промахнулся → уверенное действие без знания) |
| Дрейф | «Memories are secondary sources, so they drift… needs pruning» — признан обеими системами | staleness-правила, memory_mark_stale, show-and-continue |

Вывод: возврат «к канону» с отказом от памяти уничтожил бы возможности, которых
у канона нет (outcome-трекинг, decision-лог с UUID-связностью, калибровка).
Реальный разрыв — не наличие памяти, а **режим отказа**: у jarvis нет механизма,
делающего промах recall видимым в момент промаха.

## Trade-offs и риски

- **Дисциплина vs машинерия.** Система Pocock требует постоянной ручной
  дисциплины (писать handoff, чистить AGENTS.md, держать тикеты в один смарт-зон);
  система jarvis требует поддержки инфраструктуры (хуки, схема, /curate).
  Отказ дисциплины — потерянная сессия; отказ машинерии — тихая деградация.
- **Тикет-размер как тест.** У Pocock размер тикета проверяем («сессии
  деградируют → тикеты велики»). У jarvis слайс привязан к PR, не к сессии —
  сигнал деградации не встроен в единицу работы.
- **Wayfinder в поле ломается на маршрутизации** (#556: chart-the-map пропускает
  /domain-modeling; #625/#518: агенты путают decision-tickets с задачами) —
  заимствовать стоит идею персистентной карты, не ожидая, что labels сами
  удержат семантику.

## Рекомендации (кандидаты для /grill, не решения)

1. **Рефлекс «застрял → clear+reload», а не «застрял → дожимать»** (сценарий 3).
   Носитель: правило уже существует в SOUL («Plan / Execute / Clear») — Tier 1;
   разрыв в исполнении, не в тексте. Эскалация по DOCTRINE-порядку: hook-inject —
   напоминание при пороге контекста (~120K) «smart zone кончается: clear/handoff
   на границе фазы дешевле autocompact» — потому что нарушение стоит целой
   деградировавшей сессии, а Tier-1-строка уже доказала недостаточность.
2. **Персистентная карта для туманных задач** (сценарии 1 и 6): промежуточный
   артефакт между /reason и milestone — «Not yet specified» и «Out of scope» как
   живые секции. Форма (wayfinder-парент-issue vs секция PRD vs map-файл) —
   вопрос для /grill.
3. **Сделать промах памяти громким** (сценарии 7 и 11): не отказ от памяти, а
   дисклоужер — расширить show-and-continue до обязательной строки при recall с
   0 релевантных хитов в скилл-контрактных точках («memory: ничего по <topic>»).
   Носитель: skill-гейт (Tier 3) в /implement и /grill, не новая память —
   нарушение стоит уверенного действия на невидимо-пустом знании.
4. **Компрессия по границам фаз, не по порогу** (ось 1): предпочитать ручной
   compact/clear на стыке фаз (после плана, перед тестами) с явным
   «что сохранить»; autocompact — аварийный, не штатный. Носитель: строка в
   CLAUDE.md §Autonomous work + существующий pre-compact hook уже даёт recovery.
   Это не догадка «по мотивам» — это его собственное правило verbatim (ask-matt:
   «Don't compact mid-phase. /handoff forks; /compact continues»), плюс правило
   непрерывного окна grill→spec→tickets как кандидат на прямое заимствование.
5. **Не возвращаться к канону целиком.** Сравнение показывает: надстройки jarvis
   (outcome-трекинг, decision-лог, антисикофантийный CRITIC, деградация-доктрина)
   покрывают сценарии, где у канона пусто (5, частично 2 и 8). Пересборка «канон +
   заимствования» оправдана точечно (пункты 1–4), не сносом.

## Источники

Прочитано в этом прогоне (primary): mattpocock/skills — структура репо +
SKILL.md: engineering/README, grilling, handoff, implement, domain-modeling,
research, code-review, resolving-merge-conflicts, triage, diagnosing-bugs,
codebase-design, ask-matt (+ wayfinder, prototype+LOGIC, grill-me в предыдущей
сессии этого же исследования); mattpocock/dictionary-of-ai-coding README
(полный текст, 62 статьи, verbatim-извлечение ключевых);
aihero.dev/skills-to-tickets; biggo podcast recap (позиция по компрессии —
скорректирован primary-источником ask-matt); GitHub issues/PRs
mattpocock/skills #556, #625, #518, #535, #538; arXiv: 2606.22528, 2601.07190,
2510.06727, 2512.16970, 2510.00615. Jarvis-сторона: CLAUDE.md, SOUL.md,
CONTEXT.md, skills/, hooks (включая живое срабатывание pre-compact recovery в
сессии исследования).

Не читалось (остаточные пробелы): tdd, to-spec, to-tickets SKILL.md (известны
по рекапам и по jarvis-наследникам); воркшопы Pocock целиком (только рекапы);
сценарий 5 у Pocock не покрыт источниками.

## Confidence: 80/100

Pocock-сторона заземлена в primary sources: 15 SKILL.md (включая роутер
ask-matt — его собственные правила контекст-гигиены verbatim) + словарь
verbatim; jarvis-сторона — в собственном репо. Снижают уверенность: воркшопы
только по рекапам (написанные правила могут расходиться с живой практикой),
пустой сценарий 5 с его стороны, непрочитанные tdd/to-spec/to-tickets.
