# Нарезка §AC4 — заведена на трекере

**Источник:** [`hardware-experiment-methodology-2026-07-28.md`](hardware-experiment-methodology-2026-07-28.md) §AC4 ([#1248](https://github.com/Osasuwu/jarvis/issues/1248), PR [#1290](https://github.com/Osasuwu/jarvis/pull/1290)).
**Ревизия:** [`scientific-method-2026-07-28.md`](scientific-method-2026-07-28.md) §«Ревизия #1248» ([#1298](https://github.com/Osasuwu/jarvis/issues/1298), PR [#1299](https://github.com/Osasuwu/jarvis/pull/1299)). Решение `ed1f5dc9-8e21-4fed-abe2-ceac735182ba`.

Файл — след рассуждения: почему нарезка выглядит именно так. Текущее состояние работ живёт на трекере, не здесь.

## Что изменила ревизия

- **Гейта различимости не существует.** `discrimination_summary()` печатает `[OK]`/`[!!]`, но `main()` возвращает 1 только на `VERDICT_ERROR`, а расписка строится как `build_receipt(scripts, green=not errored)`. Репетиция с нулевой различимостью даёт зелёную sha256-расписку, которую `verify_receipt` принимает. ⇒ прежний слайс «гейт покрытия» стоял на снятой посылке и получил блокера.
- **Строка «ни одна из ветвей» нигде не валидируется**, хотя рунбук утверждает обратное.
- **`Step.repeats` не даёт оценку неопределённости** (GUM: повторы дают только u_A).
- **Записанное предсказание без названного механизма даёт ложную строгость** (Szollosi): 100 % формального соблюдения при нулевой диагностичности.
- **§AC3 chase budget нужен с правилом выхода**, не только входа — в лётных испытаниях build-up предзадан целиком.

## Проверки, прогнанные до публикации

- **Q1 AFK-fit (статическая)** — `intersects_protected()` из `scripts/to_tickets_afk_fit.py` против `config/protected-paths.json`: **clean** по всем заявленным файлам всех слайсов, включая добавленные ответами на вопросы 6 и 7. redrobot защищает `driver/**`, `planning/**`, `mujoco/**`; ни один слайс их не трогает.
- **Вокабуляр меток redrobot** — `sandcastle`, `unsafe-for-afk`, `status:ready`, `status:blocked` есть.
- **redrobot private + Free plan ⇒ автомёржа нет.** Четыре гейта работают, финальный мёрж руками по зелёному CI.

## Что решили по открытым вопросам

1. **Гранулярность 3/4** — слить: типизация шага одним слайсом (повторы с разбросом + предсказание с названным механизмом), обе правки трогают `Step` и `format_day_report`.
2. **Слайс 5** — резать: механизм реестра осей (AFK) отдельно от заполнения осей по истории err221 (HITL).
3. **Слайсы 6/7** — секция остаточного риска влита в слайс про красную репетицию: обе правки трогают вывод и код возврата `main()`. Гейт покрытия остался отдельным слайсом.
4. **Слайс 9** — резать: форма claim ledger и роль отказа отдельно от засева первой записи по err221.
5. **Милстоун redrobot** — создан «Robot-day: дисциплина эксперимента» ([milestone 33](https://github.com/SergazyNarynov/redrobot/milestone/33)).
6. **§AC3** — заведён слайсом сейчас, не отложен.
7. **Охват «переносимого ядра»** — дозировать: A/A-разброс как пол чувствительности и инъекция известного дефекта заведены в основной милстоун; бюджет типа B и холодный разбор сырых данных — в бэклог (милстоун 31). Двусторонняя проверка перед `root-cause:confirmed` покрыта формой claim ledger.

## Что заведено

**redrobot, милстоун 33 «Robot-day: дисциплина эксперимента»**

| Issue | Слайс | AFK |
|---|---|---|
| [#1649](https://github.com/SergazyNarynov/redrobot/issues/1649) | Репетиция краснеет при нулевой различимости + секция остаточного риска | AFK |
| [#1650](https://github.com/SergazyNarynov/redrobot/issues/1650) | Валидация строки-двойника «ни одна из ветвей» | AFK |
| [#1651](https://github.com/SergazyNarynov/redrobot/issues/1651) | Типизация шага: повторы с разбросом + предсказание с названным механизмом | AFK |
| [#1652](https://github.com/SergazyNarynov/redrobot/issues/1652) | Механизм реестра осей неопределённости как данных | AFK |
| [#1653](https://github.com/SergazyNarynov/redrobot/issues/1653) | Claim ledger: форма с модальностью GRADE + явная роль отказа | HITL |
| [#1654](https://github.com/SergazyNarynov/redrobot/issues/1654) | Рунбук: бюджет погони за сюрпризом с правилом выхода + «чего день не докажет» (§AC3) | AFK |
| [#1655](https://github.com/SergazyNarynov/redrobot/issues/1655) | Позитивный контроль: инъекция известного дефекта в первые 20 минут дня | HITL |
| [#1656](https://github.com/SergazyNarynov/redrobot/issues/1656) | Заполнить реестр осей по истории err221 — blocked by #1652 | HITL |
| [#1657](https://github.com/SergazyNarynov/redrobot/issues/1657) | Гейт покрытия — blocked by #1649, #1652 | AFK |
| [#1658](https://github.com/SergazyNarynov/redrobot/issues/1658) | Засев claim ledger по err221 — blocked by #1653 | HITL |
| [#1659](https://github.com/SergazyNarynov/redrobot/issues/1659) | A/A-пара как пол чувствительности — blocked by #1651 | AFK |

**redrobot, милстоун 31 «Аудит 2026-07 — Cleanup» (бэклог, ответ 7)**

| Issue | Слайс |
|---|---|
| [#1660](https://github.com/SergazyNarynov/redrobot/issues/1660) | Бюджет неопределённости типа B — blocked by #1651 |
| [#1661](https://github.com/SergazyNarynov/redrobot/issues/1661) | Холодный разбор сырых измерений в отдельном контексте — blocked by #1653 |

**jarvis, милстоун 62 «Methodology & competence foundations»**

| Issue | Слайс |
|---|---|
| [#1308](https://github.com/Osasuwu/jarvis/issues/1308) | Перенести дисциплину эксперимента в `/grill` и `/diagnose` |

Все рёбра «Blocked by» заведены нативными зависимостями GitHub, а не только прозой в теле.

## Что осталось за рамками

Девять из четырнадцати принципов «переносимого ядра» ревизии не заведены ни слайсом, ни бэклогом — сознательно, по ответу 7 (дозировать). Список — в [`scientific-method-2026-07-28.md`](scientific-method-2026-07-28.md) §«переносимое ядро»; статус «не делаем» там же.
