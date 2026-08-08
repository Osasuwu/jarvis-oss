---
title: Дизайн тест-кейсов — оракул ценности теста, выбор кейсов, test smells
date: 2026-07-28
status: draft
depth: deep-dive
sources_count: 18
issue: 1247
milestone: Methodology & competence foundations
confidence: 82
---

> Исходная публикация — комментарий в
> [#1247](https://github.com/Osasuwu/jarvis/issues/1247#issuecomment-5103117405).
> Этот файл — его копия в репозитории; расхождений быть не должно.
> Внедрение чек-листа отслеживается в
> [#1287](https://github.com/Osasuwu/jarvis/issues/1287).

## Summary

**Разрыв не в незнании техник, а в отсутствии оракула ценности теста и процедуры выбора кейсов.**

Литература даёт три вещи, которых у нас нет ни в одном файле:

1. **Отрицательный результат про наш текущий гейт.** «Тест на AC существует и зелёный» не значит ничего: покрытие слабо коррелирует с эффективностью набора при контроле его размера (31 000 наборов, 5 систем, ICSE'14). Наш `/implement` §4-TDD и §4b проверяют именно наличие и зелёность — то есть меряют то, что доказанно не меряет ценность.
2. **Единственный валидированный прокси — мутационный.** Мутанты — приемлемая замена реальным дефектам (FSE'14), и Google построил на этом рабочий процесс: **не порог на mutation score, а показ одного выжившего мутанта на покрытую строку в код-ревью** (82 % → 89 % «продуктивных» по фидбеку разработчиков). Ключевое для нас: это **ритуал, а не метрика** — гейт на скоре наступает на те же грабли Goodhart, что и в [#1260](https://github.com/Osasuwu/jarvis/issues/1260).
3. **Выбор кейсов имеет объективный фундамент**, который мы не применяем: interaction rule NIST (66 % отказов вызваны одной переменной, 97 % — одной-двумя; максимальная наблюдённая степень взаимодействия — 6) + category-partition method (CACM 1988) как механика вывода кейсов из спецификации.

Наш собственный профиль измерен и совпадает с описанным в литературе анти-паттерном «change-detector»: **3244 теста, 0 файлов с property-based тестами, 25/139 файлов с `parametrize`, 56/139 с моками, 388 подстрочных ассертов вида `assert … in content`, 38 файлов ассертят прозу markdown-документов.** При этом `_shared/tdd/tests.md` (76 строк, апстрим Pocock, verbatim) покрывает **связанность** теста (behavior vs implementation) и **не покрывает выбор кейсов вообще** — гипотеза issue подтверждается точечно: разрыв в файле, а не в голове.

Внешнее подтверждение агентской части: на SWE-bench Verified (500 задач) тесты, которые пишет агент, работают как **канал наблюдения, а не как верификация** — у Claude Opus 4.5 ~25 print'ов против ~5 ассертов на задачу, ассерты преимущественно на точные локальные значения, а не на отношения; и «заставить агента писать больше тестов» почти не меняет исход задачи. Отсюда главное предписание всей этой research: **не «пиши больше тестов», а «усиливай оракул»**.

---

## Key Findings

### 1. Покрытие не является метрикой ценности — и это не мнение

- Inozemtseva & Holmes, ICSE 2014: 31 000 сгенерированных наборов тестов на 5 больших Java-системах; корреляция покрытия с эффективностью **низкая-умеренная при контроле числа тестов**, и более сильные формы покрытия (branch/path) не дают большего понимания эффективности ([ICSE'14](https://dl.acm.org/doi/10.1145/2568225.2568271), [Semantic Scholar PDF](https://www.semanticscholar.org/paper/Coverage-is-not-strongly-correlated-with-test-suite-Inozemtseva-Holmes/abd840dbcfd986e6de9102ab809c2c46e5ce47aa)).
- Coplien с другой стороны: «даже 100 % line coverage — большая ложь», одна строка вызывается в множестве состояний, тест одного из них даёт мало информации ([Why Most Unit Testing is Waste](https://blog.jakubholy.net/2015/01/26/challenging-myself-with-copliens-why-most-unit-testing-is-waste/)).
- **Для нас:** любой будущий гейт «покрытие ≥ X %» — заведомо ложный сигнал. И наоборот: отсутствие метрики не оправдывает отсутствие процедуры.

### 2. Мутационное тестирование — рабочий прокси, но как ритуал ревью, а не как порог

- Just et al., FSE 2014: обнаружение мутантов статистически связано с обнаружением **реальных** дефектов сильнее, чем покрытие ([PDF](https://homes.cs.washington.edu/~mernst/pubs/mutation-effectiveness-fse2014.pdf)); прямое сопоставление mutation score с реальными дефектами — [ICSE 2018](https://dl.acm.org/doi/pdf/10.1145/3180155.3180183).
- Google (2 млрд строк, 150 млн тестов в день) не считает mutation score: генерируется **один мутант на покрытую строку**, он показывается в код-ревью с кнопками «Please fix» / «Not useful»; доля продуктивных выросла с 80–82 % до 89 % ([State of Mutation Testing at Google](https://research.google.com/pubs/archive/46584.pdf), [Practical Mutation Testing at Scale, TSE 2021](https://homes.cs.washington.edu/~rjust/publ/practical_mutation_testing_tse_2021.pdf), [Does Mutation Testing Improve Testing Practices?, ICSE 2021](https://homes.cs.washington.edu/~rjust/publ/mutation_testing_practices_icse_2021.pdf)).
- **Для нас важна именно дешёвая ручная форма:** испортить одну строку реализации, убедиться, что тест краснеет. Это укладывается в «одна runnable-проверка» из CLAUDE.md и не требует ни инфраструктуры, ни порога, ни CI-времени.

### 3. Выбор кейсов: interaction rule даёт приоритет, category-partition даёт механику

- NIST (Kuhn): **66 %** отказов медицинских устройств вызваны значением **одной** переменной, **97 %** — одной или двумя; по разным доменам все отказы покрываются взаимодействиями степени 4–6, максимум наблюдённой степени — **6** ([NIST: Interactions involved in software failures](https://csrc.nist.gov/projects/automated-combinatorial-testing-for-software/combinatorial-methods-in-testing/interactions-involved-in-software-failures), [SP 800-142](https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-142.pdf)).
  → Порядок работ, а не «побольше кейсов»: сначала **все** одиночные значения-границы каждого параметра, потом пары, глубже — только по риску.
- Category-Partition Method (Ostrand & Balcer, CACM 1988): спецификация → категории → партиции → **ограничения**, отсекающие невозможные/бессмысленные комбинации; акцент на покрытии спецификации и на управляемом объёме тестов ([CACM](https://dl.acm.org/doi/10.1145/62959.62964), [PDF](https://swtv.kaist.ac.kr/courses/cs453-sw-verification-tech-fall-10/category-partition.pdf)).
  → Это **ровно тот отсутствующий навык**: детерминированный переход «AC → список кейсов», а не «что придёт в голову».
- Boundary value analysis — не отдельная техника, а следствие: дефекты кучкуются на границах партиций, поэтому берутся min, min−1, min+1, max, max−1, max+1 ([NUS SE book: Test Case Design](https://nus-cs2103-ay1920s2.github.io/website/se-book-adapted/chapters/testCaseDesign.html)).

### 4. Свойства — выход из «тест = снимок текущего вывода», и у них есть цифры

- Hughes, «How to Specify It!»: **пять** систематических способов придумать свойство — инварианты, постусловия, **метаморфические**, индуктивные, модельные ([PDF](https://research.chalmers.se/publication/517894/file/517894_Fulltext.pdf), [Java-адаптация Link'а](https://johanneslink.net/how-to-specify-it/)).
- Эмпирика по Python (OOPSLA 2025, корпус **426** проектов на Hypothesis): каждый property-тест находит **≈ в 50 раз больше мутаций**, чем средний unit-тест; **76 % найденных мутаций находятся в первых 20 сгенерированных входах**; отдельные простые категории (проверка исключений, вхождения в коллекцию, типов) **>19×** эффективнее прочих property-тестов ([ACM](https://dl.acm.org/doi/10.1145/3764068), [PDF](https://cseweb.ucsd.edu/~mcoblenz/assets/pdf/OOPSLA_2025_PBT.pdf)).
  → Практический вывод: дефолт `max_examples` можно держать низким — стоимость мала, отдача ранняя. И начинать надо с самых тупых свойств, а не с изящных.
- Метаморфические отношения — единственный доступный оракул там, где **правильный ответ неизвестен**: сравнивается не выход с эталоном, а выход на связанных входах между собой ([обзор применения](https://www.ministryoftesting.com/insights/metamorphic-and-adversarial-strategies-for-testing-ai-systems)). Мощность подтверждена на компиляторах: Csmith — 325+ ранее неизвестных багов за 3 года, EMI и производные продолжают находить miscompilation в свежих GCC/LLVM ([Regehr](https://blog.regehr.org/archives/1161), [MT for graphics compilers](https://www.doc.ic.ac.uk/~afd/homepages/papers/pdfs/2016/MET.pdf)).
  → **Наши прямые кандидаты на метаморфику:** ранжирование `memory_recall` (добавление нерелевантной записи не должно менять топ-3), парсер вердикта code-review (декоративные изменения заголовка не меняют вердикт), rework-policy (перестановка находок не меняет решение), инсталлятор (повторное применение = идемпотентность). Это ровно те места, где «ожидаемый вывод» мы сегодня вписываем руками из прогона.

### 5. Поведенческий тест vs вмороженный в реализацию — канон сформулирован и операционализируем

- Google, SWE-book гл. 12: «пиши тест на каждое **поведение**, а не на каждый метод»; структура given/when/then; **тестируй через публичный API**; «не клади логику в тесты»; **DAMP, не DRY**; цель — **unchanging tests**: «после написания теста к нему не нужно возвращаться при рефакторинге, починке багов и добавлении фич»; **проверяй состояние, а не взаимодействия** ([ch. 12](https://abseil.io/resources/swe-book/html/ch12.html), [ch. 13 Test Doubles](https://abseil.io/resources/swe-book/html/ch13.html)).
- Тесты, злоупотребляющие interaction testing, там же названы **change-detector tests**: падают на любое изменение продакшн-кода даже без изменения поведения. Отдельная заметка формулирует жёстче: они **хуже, чем бесполезны** — удваивают стоимость правки, не давая информации ([Testing on the Toilet, 2015](https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html)).
- Kent Beck, 12 desiderata: два из них — рабочая пара противоположностей: **Behavioral** («если поведение меняется, результат теста должен меняться») и **Structure-insensitive** («тест не должен менять результат при изменении структуры кода») ([testdesiderata.com](https://testdesiderata.com/), [оригинал](https://medium.com/@kentbeck_7670/test-desiderata-94150638a4b3)). Остальные, важные для чек-листа: **Specific** («если тест упал, причина падения очевидна»), **Predictive** («если все тесты зелёные, код пригоден для продакшна»).
- Khorikov, 4 столпа: защита от регрессий, устойчивость к рефакторингу, быстрая обратная связь, поддерживаемость; первые два — не предмет размена ([разбор](https://www.sammancoaching.org/learning_hours/test_design/four_pillars_khorikov.html)).
- **Полезная формулировка ценности через эти два свойства:** тест ценен ровно тогда, когда он **краснеет от изменения поведения** и **не краснеет от изменения структуры**. Оба плеча проверяемы механически — второе рефакторингом, первое мутацией. Это и есть искомый оракул.

### 6. Test smells: эмпирика есть, но не все одинаково вредны

- Крупный эффект (≥0.8) на задачи поддержки у четырёх: **Mystery Guest, General Fixture, Eager Test, Assertion Roulette** ([Bavota et al., «Are test smells really harmful?»](https://www.researchgate.net/publication/271658546_Are_test_smells_really_harmful_An_empirical_study)); «грязные» тесты более change- и defect-prone, а тестируемый ими продакшн-код — тоже; метрики test smells добавляют объясняющую силу к post-release дефектам (**+8.25 % AUC** в среднем).
- Контр-сигнал: **у большинства типов smells эффект на post-release дефекты минимален**, а по Assertion Roulette есть работа, ставящая его вредность под вопрос в учебном контексте ([Is Assertion Roulette still a test smell?](https://www.researchgate.net/publication/363485771_Is_Assertion_Roulette_still_a_test_smell_An_experiment_from_the_perspective_of_testing_education)).
- Отдельно: автоматически сгенерированный тест-код **системно грязнее** написанного руками ([On the Diffusion of Test Smells in Automatically Generated Test Code](https://www.researchgate.net/publication/296183385_On_the_Diffusion_of_Test_Smells_in_Automatically_Generated_Test_Code_An_Empirical_Study)) — прямое попадание в наш профиль, где тесты пишет агент.
- **Для нас:** автоматически ловить стоит **три**, не двадцать: тест без ассертов, `assert_called*` на внутреннем коллабораторе, ассерт-литерал, скопированный из вывода прогона.

### 7. Тесты, которые пишут LLM-агенты: измеренный профиль совпадает с нашим

- SWE-bench Verified, 500 задач, 6 моделей (препринт [arXiv 2602.07900](https://arxiv.org/html/2602.07900)): Claude Opus 4.5 пишет тесты в **83 %** задач и решает **74.4 %**; GPT-5.2 пишет тесты в **0.6 %** задач и решает **71.8 %** — разница **2.6 п.п.** Тесты работают как канал наблюдения: **~25 print'ов против ~5 ассертов** на задачу у Claude 4.5; ассерты — преимущественно проверки точных значений и локальных свойств, редко — **отношений**. Вмешательства в промпт («пиши больше тестов») дают малый эффект на исход.
- Качество LLM-тестов по независимой метрике: средний mutation score сгенерированных наборов на реальных функциях ≈ **40.21 %** ([Benchmarking LLMs for Unit Test Generation from Real-World Functions, TOSEM](https://dl.acm.org/doi/10.1145/3805043), [arXiv](https://arxiv.org/pdf/2508.00408)); основной источник некорректности — **неверные ассерты**.
- Пользовательский канал ровно про это: сгенерированные тесты дублируют допущения реализации вместо того, чтобы их оспаривать; ассерты навешиваются на сериализованный вывод вместо структурных инвариантов; моки маскируют тайминг ([dev.to postmortem](https://dev.to/jamesdev4123/when-ai-generated-tests-pass-but-miss-the-bug-a-postmortem-on-tautological-unit-tests-2ajp), [HN: «I really hate useless unit tests»](https://news.ycombinator.com/item?id=20311344), [HN: Unit Testing is Overrated](https://news.ycombinator.com/item?id=30942020)).
- **Это и есть механизм провала робо-дня 2026-07-26:** зелёный репетиционный мок — не случайность и не халатность, а *модальное поведение* агента-писателя тестов, зафиксированное на 500 задачах.

### 8. Наш репозиторий: цифры

| Метрика | Значение | Что означает |
|---|---|---|
| тест-функций | 3244 | объём не проблема |
| ассертов | 6259 | ~1.9 на тест |
| файлов с `hypothesis` | **0 / 139** | property/метаморфических тестов нет вообще |
| файлов с `parametrize` | 25 / 139 | партиции почти не выражены явно |
| файлов с моками | 56 / 139 | 40 % поверхности — риск change-detector |
| `assert_called*` | 54 в 15 файлах | interaction testing локализовано — лечится точечно |
| `assert … in content/text/output` | **388** | ассерт на подстроку прозы — структурно-чувствительный по построению |
| файлов, ассертящих `.md` | 38 | мета-тесты на формулировки документов |

388 подстрочных ассертов и 38 файлов на markdown — это не «плохие тесты», а **тесты, у которых оракул — текущая формулировка**. Они краснеют от переписывания абзаца (нарушение structure-insensitive) и молчат при изменении смысла правила (нарушение behavioral). Ровно двойной провал по паре из findings §5.

При этом `_shared/tdd/tests.md` — 76 строк, verbatim из апстрима Pocock — про **связанность** теста, и ни строки про **выбор кейсов**. Гипотеза issue («агент генерирует тесты, повторяющие наши предположения») подтверждена и локализована: нет файла, который бы этому мешал.

---

## Чек-лист «этот тест ценный?»

Семь пунктов, все бинарные. **Три — блокирующие** (⛔), остальные — advisory: фиксируются в PR-описании, не останавливают.

| # | Проверка | Как проверяется | Источник |
|---|---|---|---|
| 1 | **Трассируемость** — тест указывает на пункт AC или на конкретный дефект | ссылка в docstring/имени | уже в `/implement` §4-TDD |
| 2 | ⛔ **Независимый оракул** — ожидаемое значение выведено **из требования**, а не скопировано из прогона | ответ на вопрос «я знал это значение до первого запуска?»; красный флаг — литерал, вставленный после красного прогона | tautological tests, LLM-профиль §7 |
| 3 | ⛔ **Мутационная проба** — порча одной строки реализации, которую тест якобы покрывает, делает его красным | ручная мутация → прогон → откат | Google mutation §2 |
| 4 | ⛔ **Выбор кейсов проговорён** — перечислены партиции входа и границы; взят хотя бы один **негативный** кейс, где правильное поведение — *не* сработать | список партиций в PR-описании; для 2+ параметров — покрыты все одиночные значения | category-partition + interaction rule §3 |
| 5 | **Устойчивость к структуре** — переименование внутреннего хелпера или перестановка внутренних вызовов не ломает тест; нет `assert_called*` на внутреннем коллабораторе; проверяется состояние, а не взаимодействия | греп по диффу теста | Beck, SWE-book §5 |
| 6 | **Читаемость падения** — имя описывает поведение («should …»), при падении причина видна без чтения тела теста | взгляд на имя + сообщение ассерта | Beck «Specific», SWE-book |
| 7 | **Не дубликат** — тест не повторяет уже покрытую партицию без прироста мутационной силы | греп по соседним тестам | стоимость набора, Coplien |

**Почему блокирующие именно 2, 3, 4.** Они закрывают три подтверждённых механизма провала: вписанный из прогона оракул (§7), тест, который ничего не ловит (§1–2), и односторонний выбор кейсов (§3 + двусторонность из [#1260](https://github.com/Osasuwu/jarvis/issues/1260): «one-sided evals create one-sided optimization»). Пункты 5–7 — про стоимость поддержки; их нарушение болезненно, но не создаёт ложную уверенность.

**Чего в чек-листе намеренно нет:** порога покрытия (§1), требования «тест на каждый метод» (§5, Coplien/DHH про риск-ориентированность), полного каталога test smells (§6 — большинство не влияет на дефекты).

---

## Как встроить

1. **`_shared/tdd/tests.md` → добавить секцию «Choosing cases»** (файл уже размечен как адаптируемый: шапка «Jarvis adaptations: none (verbatim)» → станет «adaptations: added case-selection section»). Содержание: партиции → границы → interaction rule (все одиночные, затем пары) → обязательный негативный кейс → пять типов свойств Hughes с указанием, когда оракула нет.
2. **`/implement` §4-TDD, после GREEN каждого AC-пункта — шаг «mutation probe»**: испортить одну строку только что написанной реализации, убедиться в RED, откатить. Один прогон одного теста; это буквально «одна runnable-проверка» из CLAUDE.md, применённая к самому тесту.
3. **`/diagnose`** — перед фиксом тест воспроизводит дефект (уже так), **плюс** обратный кейс: близкий вход, который чинить не надо, остаётся зелёным. Симметрия из #1260 против переусердствования фикса.
4. **`/rework`** — на каждую CRITICAL-находку тот же mutation probe: тест, добавленный в rework, обязан краснеть на мутации в исправленной строке.
5. **Мета-тесты на документы (38 файлов)** — отдельная работа: перевести ассерты с прозы на структуру (якоря, ID, front-matter, имена джобов), иначе они по построению change-detector. Не блокирует пункты 1–4.

---

## Trade-offs & Risks

- **Мутационная проба как гейт → Goodhart.** Google сознательно не гейтит на mutation score, а показывает мутанта человеку. Наш вариант (одна ручная мутация на AC) безопасен, пока остаётся *ритуалом внутри цикла*; превращение в «mutation score ≥ X %» в CI — та же ошибка, что порог на score судьи в #1260. Прямо помечаем как не-цель.
- **PBT не универсален.** Property-based эффективен на детерминированных функциях с формулируемыми инвариантами; в нашем коде это меньшинство (`rework_policy`, go-gate scorer, парсеры). Тянуть Hypothesis в тесты оркестратора и MCP-обвязки — плохая ставка. Цифра «50×» — на тест, а не на человеко-час; стоимость придумывания свойства она не покрывает (и авторы этого не утверждают).
- **Coplien/DHH — противовес, а не оппонент.** Их возражение (тесты ради тестов, test-induced design damage) поддерживает то же решение: чек-лист повышает планку к отдельному тесту вместо того, чтобы требовать их количество.
- **Конфликт источников по test smells** (крупный эффект на поддержку vs минимальный на post-release дефекты) разрешён в пользу узкого автодетекта: три smell'а, а не каталог.
- **Слабое место — канал «Users»**: два содержательных треда HN и постмортемы уровня dev.to. Формально канал закрыт, но доказательной силы у него мало; вся тяжесть выводов лежит на каналах Data и Specialists. Расширять не предлагаю — по этой теме пользовательский опыт систематически не публикуется в измеримом виде.
- **Двойной ассерт в чек-листе** (пункты 2 и 3 частично пересекаются) оставлен намеренно: скопированный из прогона литерал может пережить мутационную пробу, если мутация попала в другую строку.

---

## Sources

**Data / research**
1. [Inozemtseva & Holmes, ICSE 2014](https://dl.acm.org/doi/10.1145/2568225.2568271) — покрытие слабо коррелирует с эффективностью, 31k наборов.
2. [Just et al., FSE 2014](https://homes.cs.washington.edu/~mernst/pubs/mutation-effectiveness-fse2014.pdf) + [ICSE 2018](https://dl.acm.org/doi/pdf/10.1145/3180155.3180183) — мутанты как замена реальным дефектам.
3. [State of Mutation Testing at Google](https://research.google.com/pubs/archive/46584.pdf), [TSE 2021](https://homes.cs.washington.edu/~rjust/publ/practical_mutation_testing_tse_2021.pdf), [ICSE 2021](https://homes.cs.washington.edu/~rjust/publ/mutation_testing_practices_icse_2021.pdf) — один мутант на строку, продуктивность 82→89 %.
4. [NIST: interactions involved in software failures](https://csrc.nist.gov/projects/automated-combinatorial-testing-for-software/combinatorial-methods-in-testing/interactions-involved-in-software-failures), [SP 800-142](https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-142.pdf) — interaction rule, 66 % / 97 %, максимум 6.
5. [Ostrand & Balcer, CACM 1988](https://dl.acm.org/doi/10.1145/62959.62964) — category-partition method.
6. [An Empirical Evaluation of PBT in Python, OOPSLA 2025](https://dl.acm.org/doi/10.1145/3764068) ([PDF](https://cseweb.ucsd.edu/~mcoblenz/assets/pdf/OOPSLA_2025_PBT.pdf)) — 426 проектов, 50×, 76 % в первых 20 входах.
7. [Benchmarking LLMs for Unit Test Generation, TOSEM](https://dl.acm.org/doi/10.1145/3805043) — mutation score LLM-тестов ≈ 40.21 %.
8. [Rethinking the Value of Agent-Generated Tests, препринт arXiv 2602.07900](https://arxiv.org/html/2602.07900) — 500 задач SWE-bench Verified, 25 print vs 5 assert.
9. [Bavota et al., Are test smells really harmful?](https://www.researchgate.net/publication/271658546_Are_test_smells_really_harmful_An_empirical_study), [Test smells vs fault-proneness](https://www.researchgate.net/publication/336030859_An_Exploratory_Study_of_the_Relationship_Between_Software_Test_Smells_and_Fault-Proneness), [smells в сгенерированном коде](https://www.researchgate.net/publication/296183385_On_the_Diffusion_of_Test_Smells_in_Automatically_Generated_Test_Code_An_Empirical_Study).

**Specialists**
10. [SWE-book гл. 12 «Unit Testing»](https://abseil.io/resources/swe-book/html/ch12.html) и [гл. 13 «Test Doubles»](https://abseil.io/resources/swe-book/html/ch13.html) — поведения вместо методов, given/when/then, unchanging tests, состояние вместо взаимодействий.
11. [Testing on the Toilet: Change-Detector Tests Considered Harmful](https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html).
12. [Kent Beck, Test Desiderata](https://medium.com/@kentbeck_7670/test-desiderata-94150638a4b3) / [testdesiderata.com](https://testdesiderata.com/) — 12 свойств, пара Behavioral × Structure-insensitive.
13. [Khorikov, четыре столпа](https://www.sammancoaching.org/learning_hours/test_design/four_pillars_khorikov.html).
14. [Hughes, How to Specify It!](https://research.chalmers.se/publication/517894/file/517894_Fulltext.pdf) — пять типов свойств.
15. [Regehr: finding compiler bugs by removing dead code](https://blog.regehr.org/archives/1161), [MT for graphics compilers](https://www.doc.ic.ac.uk/~afd/homepages/papers/pdfs/2016/MET.pdf) — сила метаморфики.

**Adversarial**
16. [Coplien, Why Most Unit Testing is Waste (разбор)](https://blog.jakubholy.net/2015/01/26/challenging-myself-with-copliens-why-most-unit-testing-is-waste/), [DHH, Test-induced design damage](https://dhh.dk/2014/test-induced-design-damage.html).
17. [Is Assertion Roulette still a test smell?](https://www.researchgate.net/publication/363485771_Is_Assertion_Roulette_still_a_test_smell_An_experiment_from_the_perspective_of_testing_education) — контр-сигнал к §6.

**Users**
18. [HN: «I really hate useless unit tests»](https://news.ycombinator.com/item?id=20311344), [HN: Unit Testing is Overrated](https://news.ycombinator.com/item?id=30942020), [постмортем тавтологических AI-тестов](https://dev.to/jamesdev4123/when-ai-generated-tests-pass-but-miss-the-bug-a-postmortem-on-tautological-unit-tests-2ajp).

---

## Confidence: 82/100

Высокая по механизму и по предписаниям: interaction rule, отрицательный результат по покрытию, мутационный прокси и профиль агентских тестов — воспроизведённые количественные результаты из рецензируемых работ, и они сходятся на одном выводе (усиливать оракул, а не количество). Снижено на: канал «Users» слабый (§Trade-offs); ключевая работа по агентским тестам — препринт; эффективность самого чек-листа у нас не измерена — это гипотеза, а не результат, и мерить её придётся тем же способом, что и всё остальное (мутационная проба на выборке существующих тестов до/после).
