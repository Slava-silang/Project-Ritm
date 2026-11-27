# Постоянная подготовка regular/history команд

Tasks schema v20; garden v7 и зависимости прежние. Это локальная часть T41c,
не отправка, inbound или сервер. Новый `TaskStore.enqueue_sync_regular(context,
target_id)` пересчитывает предложение по текущему журналу внутри BEGIN IMMEDIATE.
Тот же commit сохраняет intent, метаданные фаз и неизменяемый BLOB запроса.
Внешняя транзакция отвергается до записи. Основания сервера пока передаются
явно; они не выдумываются из локального дерева. Контекст проверяется до
объединения с сохранёнными UUID и фазами, чтобы не скрывать дубли/неверный ввод.

`sync_prepared` связывает intent с source UUID, предлагаемыми соответствиями,
barrier UUID и transition UUID; текста задач в дополнительных метаданных нет.
Повтор той же подготовки возвращает прежний запрос/op_id. Пока пересекающиеся
исходные факты имеют pending/rejected команду, следующая не создаётся. Зависимость
от последнего intent очереди и известное основание проходят обычные проверки
outbox; ошибка freeze откатывает также intent и metadata.

`save_sync_receipt` сохраняет verified receipt и соответствия `sync_identities`,
фазы `sync_phase_acks` одной транзакцией. До applied receipt предлагаемые UUID
не считаются подтверждёнными. Соответствие взаимно однозначное и неизменяемое;
конфликт на любой записи откатывает все соответствия и сам receipt. Rejected
receipt не выдаёт фаз и соответствий. Повтор receipt не повторяет эффектов.
`read_sync_prepared_state` проверяет наличие applied receipt для фаз и возвращает
подтверждённые identities/barriers/reschedules/promotions/detaches. При следующей подготовке
они подмешиваются в контекст автоматически, поэтому забытый после restart barrier
не создаёт повторный prune.

Миграция v19→20 только создаёт три таблицы; прежние факты, frozen bytes и receipts
сохраняются. Создание/импорт публичной резервной копии очищает таблицы ledger
**в staged-копии**, до удаления очереди и binding. Рабочий профиль не очищается.
Архивы v9–19 продолжают проходить проверку своей схемы и миграцию. Внутреннее
аварийное восстановление пары сохраняет служебные данные, как прежде.

## Ещё не готово

Persistent full snapshot/receipt реализованы в SYNC_SNAPSHOT.md (v21). Cached preparation loop и settled retirement добавлены по SYNC_PREPARE_LOOP.md.
Остаются непокрытые ordinary lineage/compound/type/retention случаи и send coverage. Все исходные
facts остаются в журнале, `next_sync_sendable` сохраняет общий regular barrier.
Метод не разрешает обходить его ручной отправкой frozen request. Следующее:
cached context/preparation loop/phase coverage для обеих проекций, затем снятие
барьера только для действительно покрытых фактов, calendar/category/AI capture,
inbound и runtime с подменным транспортом. UI/реальная сеть не включены.


## История повторов

`enqueue_sync_history(context, target_id)` использует тот же atomic persist/freeze
и receipt ledger. Recompute выполняется под BEGIN IMMEDIATE по свежему журналу.
Каждый completed offline slot получает отдельный occurrence target; подтверждение
одного слота не поглощает ни соседние слоты, ни regular projection того же факта.
Все исходные факты пока остаются. FIFO зависимости требуют подтверждения предыдущей
команды. Identity ledger содержит лишь использованные payload/before node IDs,
а не все произвольные соответствия из переданного контекста.

HistoryContext.acknowledged_barriers и HistoryProposal.barrier_changes связывают
prune/restore с конкретными исходными фактами. После applied receipt повтор
prune/restore не создаётся; более позднее обычное редактирование готовит put.
Старый подтверждённый restore не разрешает воскресить новую user_deleted отметку
или отменить более позднее удаление узла сервером. Подтверждённый detach восстанавливается
через PreparedState.detaches и подмешивается в context автоматически. Доказательства
live before/type_changed mark/ordinary target остаются обязательными для planner.
SQL schema v20, wire version и зависимости не менялись. Проверки выполняются на
временных профилях без реальной отправки, UI/transport этим API не включаются.
