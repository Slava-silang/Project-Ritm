# Планировщик текущих расписаний

Чистый plan_regular(changes, RegularContext, server_id) готовит следующий шаг
каждого правила. TaskStore.prepare_sync_regular читает binding/journal одним
snapshot и считает после чтения; он сам ничего не записывает/не отправляет.
Постоянный preparation/retirement — SYNC_PREPARE_LOOP.md.

RegularContext содержит общий CalendarRecord, aware moment, explicit RegularBase
(entry=None означает доказанное отсутствие), bijective UUID pairs, occupied IDs,
known promotion/terminal bases и receipt-backed barriers/reschedules/promotions/
stops/closures. Источник этих данных — проверенный full snapshot и durable ledger.
Чужой generation, противоречивые counters/marks/UUID и размеры отвергаются.

## Current и rollover

Сначала согласуется фактическое before/current тело: put/prune/restore. Удалённые
на сервере узлы не возвращаются обычным put без explicit restore. Затем готовится
canonical latest rollover с детерминированными UUID; completed source сохраняется
как history, unfinished source — retention. Новые незамеченные повторы не создаются.
Изменённый шаблон birth передаётся rollover_updated, прежний completed source не
переписывается. Birth mappings требуют точного доказательства, не совпадения названий.

Account day берётся из настроек, не часов ОС. Active local current раньше due
требует local_refresh_required, позже — future_local_day. Waiting future first
допустим. Никогда не отправленный и уже закрытый actual rule может сначала
материализовать свой фактический saved экземпляр, затем закрыться; не создаётся
фантомный экземпляр сегодняшнего дня. Неизвестное происхождение не считается create.

## Закрытие и смена роли

Обычный stop_regular закрывает rule и переносит current в ordinary. Late stop
использует stop_offline при свободном выбранном root. Если выбранный root уже
история, сначала detach; если уже ordinary — согласовать тело; затем close_regular
закрывает rule, сохраняя actual source history/retention без нового ordinary.
Retention/deleted выбранного slot не возрождается от простого stop. Собственный
closure receipt связывает эти последовательные шаги; сама чужая rule mark не ack.

Delete передаёт null body. При позднем удалении отдельно удаляется выбранный
known task/occurrence (retention превращается в user_deleted) либо forget_task
создаёт tiny mark для доказанно absent root. Затем закрывается rule. Root из
metadata-only delete может отличаться от уже продвинувшегося server current;
проверяется выбранная representation, а не предполагается удалённой по rule mark.

Promotion task/history→regular готовит source create/restore при необходимости,
затем atomic promote_regular/promote_offline. History source нельзя публиковать
поверх ещё текущего экземпляра старого rule. Старые history frames после подтверждённой
promotion не перезаписывают type_changed mark. Ordinary lineage после stop/detach
обрабатывается отдельной проекцией и не поглощается acknowledgement перехода.

Reschedule описан в SYNC_RESCHEDULE.md: fast exact-root/tree и offline structural/
historical перенос, последовательные chains с фактическим первым экземпляром
нового rule. Старый source completion не теряется при смене current root.

## Результат

RegularProposal.id — target entity ID, не op_id; target_kind может быть regular,
task или occurrence для prerequisite. Proposal содержит action/counters/payload,
identity pairs, source_changes, barriers и отдельный transition_change. Payload/
identity данные скрыты в repr. RegularPlan возвращает proposals/blocked/unchanged,
все remaining_changes и projected_changes (его область ответственности).
Один pure result не разрешает retirement. Snapshot graph/owner/CAS/receipt
проверяются при preparation/application. Никакого HTTP/SQL внутри planner нет.
