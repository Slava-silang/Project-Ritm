# Атомарная смена расписания

Неопубликованный wire v1 поддерживает reschedule_regular и reschedule_offline.
Оба адресуют known old regular, payload — RegularRecord другого fresh rule ID.
Positive base_revision, server/device/op/hash, applied receipt и epoch+1 обязательны.
Caller в той же authoritative транзакции проверяет owner и отсутствие new rule;
флаг vacant_rule_id сам по себе не доказывает право или отсутствие.

reschedule_regular сохраняет root/структуру UUID, first/interval меняются,
current_day соответствует account due. Pure effects одной revision/epoch:
old regular user_deleted mark + new regular. Source current сначала согласуется
put/prune/restore по known counters; более поздние правки — последующие команды.

reschedule_offline переносит фактический экземпляр при structural edits или
пропущенных днях. New current не может лежать позже account due, исторический
фактический first instance разрешён. При том же root old mark + new regular;
при ином root деревья не пересекаются, old completed current сохраняется history,
unfinished — retention, затем публикуется new regular. Global owner checks
запрещают забрать UUID другого live объекта. Даты выполнения не меняются.

Queue переносит именно edge.after, а не последний local snapshot нового rule:
иначе его первый completed instance можно ошибочно записать как skipped remnant
старого rule. После transfer обрабатываются local changes/rollovers нового rule.
Так chains A→B→C с завершёнными intermediate slots не теряют и не дублируют историю.
Old never-uploaded rule при known absence материализуется фактическим saved
экземпляром, после чего выполняется тот же atomic transfer. Future clock conflict
блокируется, не превращается в новый repeat.

Acknowledged reschedule освобождает dependent link только по verified receipt
и обоим effects. Intermediate new rule впоследствии может быть закрыт следующим
link, если его counters не старше предыдущего effect. IDs/counters cross-target
не выводятся из произвольного receipt без explicit known cached base.

Frozen requests immutable. Новая несохранённая команда может использовать более
свежий revision того же epoch из полного снимка. Другой epoch требует собственной
typed-transition provenance в ancestor chain и cached target либо безопасного
explicit действия над пустым retention mark. Чужой восстановленный live root
не перезаписывается прежним деревом.

Pure validation/effects не являются сервером. Actual queue/FIFO/restart/coverage/
retirement — SYNC_PREPARE_LOOP.md; приёмка — SYNC_BLOCK1_ACCEPTANCE.md. Старый строгий
клиент новый action отвергает, не выполняет половину перехода. SQLite v21/garden v7
и зависимости прежние. HTTP/inbound/UI подключения остаются следующими блоками.
