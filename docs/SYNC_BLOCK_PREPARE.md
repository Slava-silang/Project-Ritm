# Подготовка календаря из постоянного журнала — T41c5d

SyncBlockPrepareMixin подключён к prepare_next_sync после обычной FIFO и полного
покрытия regular/history/lineage projections. Один шаг готовит один immutable
запрос. Network/inbound/runtime всё ещё выключены общим coverage barrier.
Schema tasks v22/garden v7 и зависимости не изменились.

## Порядок и сохранность

До подтверждения всех preceding task/category/regular operations и covering
snapshot календарь не готовится. Для каждого block UUID несохранённые в outbox
правки сворачиваются к последнему состоянию; несвязанные blocks обрабатываются
в порядке первых фактов. Net create→delete без публикации не отправляет блок.
Последний fact UUID становится immutable intent/op UUID; global predecessor
сохраняет ancestry. Дальше используются прежние FIFO/known-base/receipt validators.

Frozen request не меняется от нового local edit. Новые facts получают новые UUID.
После applied receipt и snapshot не старше его revision удаляется только prefix
того же блока до подтверждённого fact.sequence. Более новые правки сохраняются. Watermark снимка сам по себе недостаточен:
нужен target entry с revision не ниже receipt; при точной revision проверяются
epoch и live payload. Missing effect ждёт snapshot, mismatch остаётся conflict.
Rejected receipt/foreign epoch не очищают facts. Удаление prefix, подготовка
следующего запроса и eventual regular journal retirement входят в одну SQLite
transaction. Ошибка SQL откатывает и очистку, и новый intent/outbox. Очередь,
bytes/receipts, local work_blocks и локальная история сохраняются.

## Ссылки и основания

Standalone event не требует task source. Для связанного блока confirmed mapping
из sync_identities заменяет только wire task_id; локальная связь не меняется.
Live root ищется в TaskRecord/RegularRecord/OccurrenceRecord полного snapshot.
Unknown/ещё не опубликованный root блокирует подготовку без выдуманного caption.
Подтверждённый user_deleted root не возрождается. Retention root marker позволяет
передать archived_task_title из локальной задачи (или известного cloud block).
Global UUID ownership включает вложенные узлы задачи, а не только entity keys.
При конфликте journal остаётся. Изменение cloud parent link не затирается молча.

Known отсутствующий новый block → create; live → put; явный local restore →
restore tombstone. Обычная правка не возвращает user_deleted block. Intentional
edit retained local event в облачное окно использует restore retention marker,
а не обычный put. Неизвестный existing block не выдаётся за новый create.
Delete с уже отсутствующим/removed block считается покрытым; живой получает
отдельную delete-команду. После own ordinary task-delete cascade восстановление
блока использует marker только при точном совпадении его revision с applied
receipt удаления того же корня. Более новая foreign deletion остаётся conflict.

## 30 суток

Cutoff считается по UTC, включая переходы DST, и совпадает с sync_projection: ends_at < moment−30 суток исключён, точная
граница включена. Локальные интервалы не обрезаются. Ещё не опубликованный/уже
removed старый block не загружается. Живой cloud block, который пользователь
перенёс в старую историю, получает put с новыми датами: последующий server
retention выдаёт policy marker, а не ложное user_deleted. Applied receipt с
таким covering marker позволяет завершить fact без повторного возвращения.

## Остаток второго блока

Нужны отдельная приёмка каскадов regular/history/skip/retention при позднем
восстановлении, проверка всех AI writer путей в полном цикле и общая приёмка
category/calendar/AI. В частности, own cascade proof сейчас покрывает ordinary
Task delete; epoch от regular close не получает автоматического разрешения.
Unknown parent остаётся явным blocked, а не основанием для утраты локального fact.
Сервер/сеть/авторизация/входящие изменения не реализуются этим шагом.

## Проверки

Focused 45 passed (6,93 с), /tmp/ritm-block-prepare-proof-focused.log:
restart/pending edits/coalescing, parent-before-block, canonical mapping,
retention caption vs unknown/user_deleted root, 30-day boundary/DST, expired
live put и policy return, UUID ownership, create→delete no upload, ordinary
cascade restore/foreign epoch, SQL failure при freeze/retire/combined step,
receipt watermark без target и несовпадающий payload. Expanded 938 passed/6 GUI deselected (81,93 с), /tmp/ritm-block-prepare-verified.log. Ruff check/format и diff passed.
Нативный UI/Windows/ручной осмотр не проверялись; UI не менялся.
