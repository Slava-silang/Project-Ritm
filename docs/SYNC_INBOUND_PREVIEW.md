# Входящее сравнение — первый шаг T41c5e

sync_inbound.plan_inbound — чистый трёхсторонний preview, не применение данных.
Принимает local DTO, incoming full Snapshot, последний **применённый** accepted
Snapshot и protected keys. server_id задаёт ожидаемую привязку. accepted=None
обозначает первоначальное объединение. Кеш, обновлённый при исходящей квитанции,
НЕ является доказательством применения снимка к локальным данным: подставлять
его вместо отдельного applied baseline нельзя.

## Контракт

Local содержит канонические wire UUID и одну роль каждого корня: ordinary,
regular current или completed occurrence. История вне облачных лимитов остаётся
в local. Applied baseline и подтверждения собственных local edits должны учитываться
раздельно по provenance: отсутствие pending само по себе не доказывает, что
расхождение с полным applied snapshot можно стереть. До получения такого
доказательства preview консервативно оставляет local_changed conflict.
Pending set будущий owning adapter обязан построить из всех ordinary/
category intents (включая frozen/rejected), regular/history facts, block journal
и несохранённого ввода UI, если он затрагивает применение. Node key защищает
всё дерево; root key защищает связанные calendar changes. Равенство cloud body
локальному не разрешает очищать ещё не подтверждённую команду.

Оба remote snapshot проходят обычные bounded page codec/collector и full graph
validation. Local DTO проверяются тем же graph/codec, без усечения при превышении
лимита (10 000 сущностей/32 МиБ). Отвергаются другой server generation, регресс
set/entity revision/epoch, противоречащие данные одной revision и возврат marker
в live без нового epoch. Это проверка формата/согласованности, не авторизация.

| Решение | Значение для будущего transactional apply |
| --- | --- |
| keep | Равные данные; никакой очистки pending по одному равенству |
| insert/update | Новый/изменённый cloud DTO, местное дерево не расходится с applied baseline |
| transition | Явная смена task/current/history роли; сохранить физическое дерево/историю, не дублировать UUID |
| archive | User deletion при неизменённом локальном baseline; будущий apply сохраняет локальную корзину/файлы |
| retire_rule | Закрыть metadata расписания; НЕ удалять дерево. Отдельный task/occurrence marker определяет его судьбу |
| retain | Retention/type_changed/remote absence не являются удалением локальной истории |
| marker | Метаданные уже отсутствующей сущности; node deletion применяется через owning root, не отдельно |
| defer/conflict | Pending/local divergence/initial collision/dependency/UUID conflict сохраняются для решения |

Initial merge не заменяет local общей копией: одинаковые DTO keep, новые remote
insert, local-only retain, разные UUID-owned тела и удаление local при bootstrap
требуют выбора. Незаявленная локальная правка тоже не затирается молча. Если
applied baseline уже не содержит старого тела (например, retention), а local
расходится с ним, preview консервативно сохраняет конфликт; future apply должен
использовать durable baselines/решение, а не угадывать отсутствие local changes.

Global ownership включает root/child/item UUID. Новый current может сохранить
UUID правила, но не отнять узлы у сохраняемого старого корня. Смена владельца
calendar block не применяется молча. Category conflict задерживает dependent
root, затем его block; удаляемая category сохраняется при local history reference
и откладывается при pending root. Type_changed paired marker не архивирует дерево,
которое представлено другой живой incoming ролью.

## Граница готовности и продолжение

Storage adapter и ограниченный atomic apply уже добавлены: SYNC_INBOUND_STORE.md.
acknowledged_local proof теперь даёт только owning adapter из verified own receipt
и совпадающего local canonical payload; чистый planner сам receipt не проверяет.
Ниже — граница исходного pure checkpoint, до storage adapter.

Готов read-only алгоритм решений с immutable DTO и скрытым из repr содержимым.
Нет SQLite apply, applied cursor/table, UI, сети, ack/cleanup или garden rewards.
Этот шаг не завершает третий блок. Следующее: coherent TaskStore adapter (canonical
mapping + pending guard), durable applied baseline и атомарное применение под
origin=incoming с сохранением local-only данных/корзины/файлов. Baseline/cursor,
данные и защита от повторных garden awards должны коммититься согласованно;
bootstrap/старые completion не дают наград. Затем restart/fault/backup acceptance.

## Проверки

Focused 30 passed (0,28 с), /tmp/ritm-inbound-preview-final.log: bootstrap choice,
new/keep/update/archive, pending equal/different/absent/deleted/retained, local
history/category protection, root/node/block dependencies, role transfers,
regular close vs tree retention, UUID collisions, invalid snapshot/counters/
server/epoch, explicit remote restore и отсутствие payload в repr. Expanded: 1025 passed/9 GUI deselected (94,84 с),
/tmp/ritm-inbound-preview-verified.log. Схемы/deps/UI не менялись; Windows/manual/native/GUI не запускались.
