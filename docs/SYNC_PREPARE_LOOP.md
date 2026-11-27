# Подготовка очереди из сохранённого снимка

`TaskStore.prepare_next_sync(moment)` выполняет один атомарный шаг на BEGIN
IMMEDIATE. Аккаунт/capture должны быть предварительно включены через будущий
проверенный bootstrap; метод не подключает пользователя самостоятельно.
Схема tasks v21/garden v7 и зависимости не меняются.

## Порядок

1. Прочитать binding, журнал и полный cached snapshot. Найти первый неготовый
   intent. Frozen pending возвращается с теми же bytes/op_id, rejected — conflict.
   Обычный несохранённый intent готовится по known cached base; create требует
   явного отсутствия, запись поверх marker требует явного restore/delete.
2. Для новой команды snapshot должен покрывать maximum revision всех applied
   receipts. Без снимка/после отдельного receipt без актуального снимка вернуть
   needs_snapshot, не изобретая отсутствие или успешное применение.
3. Собрать RegularContext/HistoryContext по одному full snapshot, журналу и
   receipt-backed identity/phase ledger. Наличие/отсутствие target проверяется
   в полном снимке; timezone берётся из CalendarRecord. Global occupied IDs,
   ordinary roots, terminal/promotion/task/history bases передаются явно.
4. Сначала готовить regular projection, потом history: это позволяет подтвердить
   canonical current/root mapping до независимых offline history create. UUID
   будущих canonical слотов не связываются заранее с локальными UUID. При иной
   known birth identity остаётся блокировка, а не догадка о новом соответствии.
5. Готовую команду пересчитать/сохранить через тот же internal atomic enqueue/
   freeze helper. Следующий вызов вернёт pending, новый op_id не создаётся.

При отсутствии proposals, но наличии blocks вернуть blocked с причинами. Без
блокировок оба planner дополнительно возвращают projected_changes: **область
ответственности**, не доказательство выполнения сама по себе. Их объединение
должно покрывать каждый journal fact. Только совместно с отсутствием proposals/
blocks и покрывающим full snapshot это позволяет удалить settled журнал.
`retired_facts` сообщает число удалённых служебных facts, `remaining_facts` —
оставшихся. Следующий вызов возвращает idle. Один pending/blocked/непредставленный
fact запрещает retirement всего прочитанного набора. Новые записи не могут
появиться между проверкой и удалением из-за BEGIN IMMEDIATE.

Фазы retired UUID удаляются в той же транзакции. Intents, immutable request bytes,
receipts, UUID mapping, sync_regular_roots lineage и пользовательские данные не
удаляются. Повтор receipt после retirement не воссоздаёт старые фазы. Сбой SQL
откатывает facts и фазы вместе. Тест с тремя offline completion после каждого
receipt/reopen доходит до settled/idle без ручных contexts или fake accept.

## Закрытые расписания и отделённые задачи

RegularContext.acknowledged_stops/PreparedState.stops восстанавливают stop_regular/
stop_offline provenance. Для settled closed rule нужны user_deleted regular mark
и ordinary task/mark с тем же root и counters не старше закрытия. Сама отметка
удаления правила без receipt по-прежнему не подтверждает наш stop.

History detach ack покрывает предыдущую history и сам detach, но **не более
поздние ordinary root_save/delete/restore**. Такие поздние факты не входят в его
projected_changes; отсутствие отдельного resolver проявляется projection_missing
или соответствующим blocked. Они не поглощаются успешным предыдущим detach.

## Границы

Это автоматическая preparation, не HTTP и не разрешение обходить
next_sync_sendable coverage barrier. UI не включает синхронизацию. Внешняя
транзакция отвергается до записи. Local refresh по account day пока выполняется
существующим recurrence worker: stale local day даёт local_refresh_required,
фантомные выполнения не создаются.

Первый крупный блок ещё не завершён: ordinary lineage после stop/detach, оставшиеся
compound/type/retention случаи должны проходить автоматический цикл до settled,
после чего нужен общий аудит покрытия перед transport. Далее исходный порядок:
calendar/category/AI capture, inbound/merge/rewards, runtime/UI на fake transport,
сквозные проверки. Реальный сервер не реализуется этим шагом.
