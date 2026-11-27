# Подготовка предложений истории — T41c5c1

Tasks v19/garden v7, зависимости прежние. Новый чистый
`integrations/sync_history_plan.py` и read-only `TaskStore.prepare_sync_history`.
Это часть resolver: готовит предложения для прошлых выполненных экземпляров,
но не записывает SyncIntent/Operation, не замораживает запросы, не отправляет и
не потребляет журнал. UI/аккаунты/HTTP/inbound остаются впереди.

## Вход и доверие

`plan_history(changes, context, server_id=...)` принимает неизменяемый tuple
RegularChange в возрастающем seq без повторов ID. DTO/проверка RegularChange
вынесены из storage в `integrations/sync_regular_changes.py`; storage сохраняет
совместимые импорты. Чистые planner/DTO не зависят от SQLite/Qt/storage.

HistoryContext:

- server_id — ожидаемое поколение сервера, проверяется против отдельного аргумента;
- bases — HistoryBase(history ID, SnapshotEntry либо None). None — явно известное
  отсутствие, не результат пропуска записи в списке. Отсутствующий HistoryBase
  означает unknown_base, не разрешение на create;
- identities — явное взаимно однозначное соответствие local→server UUID корней
  и всех остающихся узлов. Неполная карта блокирует цель; fallback к local UUID нет;
- regulars — известные серверные активные расписания, ordinary_roots — занятые
  ordinary корни. История не публикуется с текущим корнем/днём или вне сетки;
- frozen_changes — source fact UUID, уже участвующие в замороженных запросах.
  Повторная подготовка по ним блокируется вместо изменения прежних bytes.

Будущий caller обязан получить context из аутентифицированного состояния того
же пользователя/поколения, полного снимка и сохранённых отображений. Эти DTO сами
не доказывают owner/auth или подлинность отсутствия. Нельзя создавать контекст
из произвольного частичного списка и считать недостающие записи отсутствующими.
Полная проверка графа всех серверных сущностей/узлов и подтверждение зависимостей
остаются следующими частями resolver; предложения пока не отправляемые команды.

SnapshotEntry проверяет revision/epoch, entity kind/history ID/root и
согласованность tombstone counters. Дубли bases/маппинга/remote root отвергаются.
Границы — 10 000 фактов/элементов каждого набора контекста, 32 MiB кодированных
снимков отдельно для контекста и журнала. Codec сохраняет свои лимиты одной
сущности. Ошибка входа не возвращает частичного плана.

## Сведение истории

Каждый завершённый before у rollover превращается в отдельную OccurrenceRecord
по history_id(rule_id, day). Несколько offline-дней не схлопываются к последнему
current. Незавершённые пропуски не становятся выполненной историей.
Последующие history edit/prune/delete/restore меняют предложение только того
же слота и корня. Запланированный день/completed_at/null сохраняются.
ID дерева преобразуются только по явному identities, локальная БД не переписывается.

| Известное состояние и действие | Результат |
| --- | --- |
| Явно отсутствует, desired history есть | create с base=0/epoch=0 |
| Live history изменена | put либо prune, исходные известные revision/epoch |
| Удалены узлы в локальной цепочке | prune даже после последующих правок |
| Явный restore | restore, включая совпадение итогового тела с live baseline |
| retention без явного restore | unchanged; тело не возвращается |
| user_deleted без явного restore | blocked/deleted |
| Delete известной live/retained истории | delete, payload null |
| Delete уже user_deleted истории | unchanged |
| Delete при явно отсутствующем base | blocked/delete_requires_base |

Идентичный live DTO без барьерного действия — unchanged. Смена epoch не
выдумывается: план содержит исходное основание, а новую ревизию/epoch даст
будущая проверенная квитанция. Восстановление старого узла после локального prune
требует явного restore. Если сервер уже убрал узел, присутствовавший в исходном
локальном снимке, его нельзя вернуть rebase/обычным put — node_restore_required.
Слот/корень, занятый другой историей, также не перезаписывается.

Detach или history→regular promotion требуют атомарной смены типов. Эти цели
возвращаются blocked/atomic_type_transition: planner не публикует отдельно
удаление истории или новое ordinary/regular тело. Ordinary после stop/restore
также оставлено дальнейшему resolver. Остальные независимые слоты могут иметь
предложения, но это не разрешение отправки всего набора.

## Результат и хранение

HistoryPlan содержит proposals, blocked, unchanged и remaining_changes.
HistoryProposal.id — UUID цели occurrence, **не op_id**; action/base/epoch/payload
и source_changes — данные для последующей подготовки. Случайные op UUID не
назначаются. Один и тот же вход даёт одинаковый результат.
Тела proposals/context/плана исключены из repr; причины blocked фиксированные.
Все исходные fact UUID остаются в remaining_changes, включая unchanged:
ни один read-only план не даёт разрешения удалить факт или снять send barrier.

TaskStore.prepare_sync_history читает binding и журнал одним BEGIN snapshot,
завершает чтение и только затем считает план. Непривязанный профиль, другое
поколение и внешняя активная транзакция отклоняются; внешняя транзакция не
коммитится/не откатывается. Подготовка не меняет дерево, журнал, очередь,
квитанции, completion dates, garden event или флаг capture.
Сохраняется общий барьер next_sync_sendable при regular facts.

T41c5c2a готовит next-step current/canonical rollover (SYNC_REGULAR_PLAN.md);
compound cases ещё T41c5c2b. Далее отображение UUID и
атомарные переходы типов; затем durable prepare/связь proposal→intent→receipt и
потребление только подтверждённых фактов. Календарь/категории/AI paths после
regular. Общий account timezone/transport/inbound/награды сада ещё впереди.
