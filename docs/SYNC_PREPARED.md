# Постоянная подготовка команд задач

Tasks schema v21 (ledger введён v20), garden v7/deps прежние. Storage
`enqueue_sync_regular(context,target_id)` и `enqueue_sync_history(context,target_id)`
пересчитывают proposal по locked journal и атомарно сохраняют intent, metadata
и immutable frozen BLOB. Публичные методы отвергают внешнюю транзакцию. Общий
production preparation step — SYNC_PREPARE_LOOP.md, приёмка — SYNC_BLOCK1_ACCEPTANCE.md.

sync_prepared хранит source UUID, предлагаемые identity pairs, barrier UUID и
transition UUID без дополнительного текста задач. Точное повторение возвращает
прежние bytes/op_id. Pending/rejected пересечение facts не создаёт вторую команду.
Ошибка freeze откатывает intent и metadata. Глобальная UUID ownership проверяется
по полному cache: typed source может передать свой root/узлы, но не забрать другую
live сущность. Type_changed marker не владеет уже переданным live root.

Verified applied receipt и sync_identities/sync_phase_acks сохраняются атомарно.
Mappings взаимно однозначны и не переписываются; ошибка откатывает весь receipt.
Rejected ничего не подтверждает. PreparedState восстанавливает identities,
barriers, reschedules (regular/offline), promotions (regular/offline), detaches,
stops и closures. Первоначальный context проверяется до объединения с ledger.

Internal history preparation допускает только точное ordered подмножество
реального immutable journal из той же транзакции: это нужно для source create/
restore перед promotion и отдельной ordinary lineage projection. Изменение
содержимого, дубли или подстановка чужого fact отвергаются.

Pure prepare_sync_regular/prepare_sync_history остаются read-only; их remaining
facts не означают готовность к retirement. Общее покрытие проверяет queue step.
History prune/restore связаны с конкретными barrier facts и не повторяются после
restart; старый restore не отменяет новую foreign deletion. Typed transition
receipt, cached base и owner checks нужны для дальнейших команд нового типа.

Миграции сохраняют старые intents/frozen bytes/receipts/facts. Публичный backup/
import очищает ledger/cache в staged-копии перед binding, исходный профиль не
очищается. Внутренний rollback сохраняет служебные данные. Сети/учётных данных
в этом API нет. Следующие capture/inbound/runtime блоки отдельно от очереди.
