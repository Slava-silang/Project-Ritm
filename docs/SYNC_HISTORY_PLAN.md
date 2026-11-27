# Планировщик истории повторов

Чистый plan_history(changes, HistoryContext, server_id) сохраняет отдельный
OccurrenceRecord для каждого completed slot (rule_id,day) со стабильным history_id.
Skipped unfinished slots не становятся выполненными. День расписания и actual
completion date различаются; даты не выдумываются. Payload/identity repr скрыты.

Context содержит explicit HistoryBase (None — известное отсутствие), bijective
UUID pairs, известные active regulars/ordinary roots, task bases и подтверждённые
barrier/detach UUID. Full snapshot/gen/counters/лимиты задают известное основание;
похожий текст или случайная дата не доказывают identity. Root/node collisions,
текущий slot, day вне сетки и чужие marks блокируются.

History edit/prune/delete/restore меняют только выбранный slot. Удалённые nodes
не возрождаются обычной правкой. Restore/prune привязаны к source facts; applied
barrier подавляет повтор того же действия после restart. Retention rename остаётся
локальным; explicit delete превращает mark в user_deleted; explicit restore
возвращает данные. Новая foreign deletion не отменяется старым ack restore.

Detach переводит known live occurrence в ordinary одной revision/epoch: old
occurrence type_changed mark + TaskRecord. Root сохраняется; подзадачи могут
измениться вместе с reopen/add item/deadline. Original nodes, удалённые сервером,
требуют explicit restore; новые UUID проверяет общий owner gate. Task target
должен отсутствовать либо быть прежним type_changed marker не новее source epoch.
Существующий ordinary root не считается свободным.

Сам detach ack покрывает предыдущую history и detach, но не поздние root_save/
delete/restore ordinary. Они исключаются из history projection и проходят lineage
resolver. После promotion в regular предшествующие frames покрывает confirmed
transition: old history body не загружается заново поверх type_changed mark.
Queue может подготовить точное подмножество исходного journal для prerequisite
history create/restore перед promotion; ни один исходный fact не переписывается.

TaskStore.prepare_sync_history read-only. Durable enqueue, full cached context,
FIFO/retry/acks и retirement — SYNC_PREPARED.md/SYNC_PREPARE_LOOP.md. Pure result
сохраняет remaining_changes; projected_changes — только область ответственности,
не автоматическое разрешение очистить journal. Сеть/inbound/награды сада вне planner.
