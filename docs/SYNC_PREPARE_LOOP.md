# Постоянная очередь: первый блок завершён

Область первого блока: локально записанные изменения задач, повторов и истории
превращаются в неизменяемые команды; подтверждения и полный облачный снимок
сохраняются; только обработанный журнал очищается. Приёмка —
[SYNC_BLOCK1_ACCEPTANCE.md](SYNC_BLOCK1_ACCEPTANCE.md).

## Публичный шаг очереди

`TaskStore.prepare_next_sync(moment)` работает под BEGIN IMMEDIATE. Требуются
binding и предварительно включённый capture. Сам метод не подключает аккаунт.
Он читает immutable intents, журнал, receipt-backed UUID/phase ledger и bounded
полный snapshot текущего server generation. Без snapshot или если его watermark
не покрывает maximum revision applied receipts возвращается needs_snapshot.

FIFO head имеет приоритет. Frozen pending возвращается с теми же bytes/op_id;
rejected возвращается как conflict. Несохранённый ordinary intent получает
известное основание из snapshot; create требует отсутствия, запись поверх mark
не превращается в обычный put. Частичные/чужие/старые снимки не используются.

Для journal facts собираются три проекции:

- regular: current, canonical rollover, stop/delete и переходы расписаний;
- history: самостоятельные completed offline slots, edit/prune/delete/restore,
  detach, подготовка source перед promotion;
- ordinary lineage: последующие правки/удаление/восстановление после stop/detach.

Переходы выполняются по capture-порядку. Переносится фактический экземпляр в
момент смены правила, затем обрабатываются последующие выполнения нового правила.
Это сохраняет completion один раз и не создаёт пропущенных во время offline
экземпляров. Будущие canonical UUID не связываются заранее с локальными UUID.
History source перед promotion может потребовать отдельного create/restore;
используется точное подмножество immutable journal, проверенное против SQLite.

Следующая команда сохраняется атомарно через internal enqueue/freeze. Пока её
receipt не получен, вызов возвращает pending. Новая локальная правка во время
pending добавляет факт и будет подготовлена после подтверждения прежних bytes.

## Состояния и границы

prepared/pending содержат FrozenRequest; needs_snapshot требует нового полного
снимка; blocked содержит конкретную причину (например, local_refresh_required,
неизвестное основание/узел или смена типа, требующая разбора). conflict сохраняет
данные при rejected receipt, чужом deletion epoch или UUID другого объекта.
Ни один такой исход не считается выполнением и не очищает факты.

Более свежий revision той же generation/epoch разрешён только с точным known
cached base. Другой epoch разрешён при доказанном собственном typed transition
в цепочке applied receipts и явном base из snapshot. Ранее замороженный запрос
не меняется. Пустые retention marks можно явно удалить/восстановить; user_deleted
с чужим новым epoch не позволяет автоматически вернуть прежнее дерево.

Данные retention остаются локальными: простое переименование не возвращает их
в облако. Удаление передаёт только mark. Явное восстановление former-history как
ordinary может атомарно заменить occurrence mark на type_changed и создать Task
без промежуточного фиктивного completed history. Глобальные владельцы UUID
проверяются до freeze, receipt/snapshot проходят собственные counter/graph gates.

## Retirement

Pure planners возвращают projected_changes как область ответственности, а не
самостоятельное доказательство выполнения. После отсутствия proposals/blocks
объединение regular/history/lineage/confirmed promotion coverage должно покрывать
каждый fact. Все intents должны иметь applied receipts, snapshot — покрывать их.
Тогда в той же транзакции удаляется journal и phase acks retired UUID, возвращается
settled с retired_facts. Следующий шаг — idle.

Один pending/blocked/непредставленный fact запрещает retirement прочитанного
набора. Сбой SQL откатывает facts и phase cleanup вместе. Tasks/trash/files,
completion dates, sync_regular_roots, UUID maps и immutable requests/receipts не
удаляются. Retry старого receipt не воссоздаёт очищенные фазы. Detach ack сам по
себе не покрывает последующие ordinary правки — за них отвечает lineage resolver.

## Следующие блоки

Следующий второй блок — category/calendar mutations и все AI paths capture.
Затем inbound/merge/однократные garden rewards, runtime/UI на fake transport,
сквозные два клиента. `next_sync_sendable` сохраняет общий coverage barrier до
полноты capture; returning PreparedStep не разрешает ручную сетевую отправку.
HTTP/аккаунты/сервер/Mini App здесь не включены. Tasks schema v21, garden v7,
зависимости прежние. Windows/полная desktop синхронизация не объявляются готовыми.
