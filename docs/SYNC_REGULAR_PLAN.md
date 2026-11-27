# Следующий шаг текущего расписания — T41c5c2a

Tasks v19/garden v7/dependencies прежние, миграции нет. Чистый
`integrations/sync_regular_plan.py` и read-only `TaskStore.prepare_sync_regular`.
Это предложения, не сохранённые команды: нет enqueue/freeze/HTTP/inbound,
назначения op_id, подтверждения сервера или удаления исходных фактов.

## Известный контекст

RegularContext содержит server generation, CalendarRecord аккаунта, явный aware
moment, bases по rule ID (SnapshotEntry либо явно известное None), взаимно
однозначные local→server UUID, occupied_ids живых серверных деревьев/других
сущностей, frozen_changes и acknowledged_barriers. UUID/коды DTO/counters/
повторы/лимиты проверяются. Неуказанный base — unknown_base, не create.
Нужны полный проверенный context того же владельца/поколения и согласованный
server day; тип данных сам не доказывает owner/auth/серверное время.

occupied_ids включает все занятые живые node UUID, а не только root. Собственное
известное текущее дерево исключается при put/prune/restore; чужие ID не занимают.
Канонический новый root/children также не могут совпасть с занятыми ID.
Контекст не заменяет полную последующую проверку server graph/node tombstones.
Неподтверждённые frozen facts блокируют переподготовку; acknowledged_barriers
предназначены только для fact UUID, чей prune/restore подтверждён точной
проверенной квитанцией. Противоречие frozen/ack запрещено. Автоматического
доверия произвольным флагам caller нет: сборщик контекста/ledger ещё впереди.

Общая проверка RegularChange/journal/UUID/counter/codec/node IDs вынесена в
sync_regular_changes.py и используется обоими planner. Границы каждого набора —
10 000 элементов, кодированных контекста/журнала — по 32 MiB.

## Один следующий шаг

На каждое правило выдаётся максимум одно предложение с **уже известным** base
revision/epoch. Для следующей команды нужен новый проверенный server context
после receipt; зависимые revision/epoch не вычисляются как будто сервер уже
применил предыдущую команду.

Для live правила выбирается последний локальный снимок того же current_day,
что на сервере. Явная UUID-карта переносит только IDs, сохраняя поля/выполнение/
completion date. Изменение → put, удаление узлов → prune, явное восстановление
→ restore. При удалении старого узла сервером обычный rebase не возвращает его.
Локальный удалённый node нельзя добавить повторно без explicit restore.

Подтверждённые barrier_changes исключаются из повторного предложения, иначе
prune/restore с тем же финальным DTO предлагался бы бесконечно. После ack данные
должны также соответствовать фактическому server snapshot. Remaining facts не
исчезают, в том числе если отдельный барьер уже подтверждён.

Когда этот экземпляр согласован, но локально есть следующий, готовится
rollover_record(remote, последний наступивший день по account timezone).
Промежуточные завершённые слоты не отправляются как устаревшие rollover — их
сохраняет отдельный history planner. Локальная текущая дата должна совпадать с
server due day; иначе local_refresh_required/future_local_day. Пользовательские
часы не выбирают победителя и не подменяют серверную дату.

Пример трёх offline завершений:

1. put выполнения серверного старого экземпляра с известным base;
2. после подтверждения — канонический rollover сразу в последний наступивший день;
3. после подтверждения — put правок нового экземпляра, если они были;
4. отдельный history planner готовит промежуточные выполненные слоты.

## Соответствие UUID

Случайный desktop root никогда не подставляется в сетевой rollover.
Канонический root — occurrence_id(rule, day), child/item UUID — по wire path.
Предложение rollover содержит только доказанное соответствие последнего
локального birth-снимка и канонического дерева. Сравниваются все поля, веса,
структура и отметки после замены ID; совпадения одного названия недостаточно.
Пары не записываются в профиль до будущего durable prepare/receipt этапа.

При уже существующем canonical current такая birth-пара может быть восстановлена
из точного совпадения birth DTO с известным server DTO. Частичная/противоречивая
карта не дополняется случайными значениями. Если шаблон изменился на промежуточных
offline-днях и не совпадает с новым canonical tree, возвращается
**rollover_updated** с canonical деревом нового шаблона. Нельзя переписать
старую выполненную историю ради шаблона нового экземпляра. Без полного birth
снимка остаётся local_birth_required.

Новая локальная серия допускает create последнего current только при явном
отсутствии server rule, исходном creation before=None и отсутствии коллизий:
identity UUID здесь опирается на записанный факт создания, а не fallback для
неизвестного старого объекта. Без creation provenance — блокировка. Future-first
waiting current допускается; он не вызывает выдуманный rollover до первого дня.

## Stop/delete и границы

Stop выдаёт stop_regular с null payload только для соответствующего текущего
root после предшествующих необходимых правок/rollover. Сам wire transition
атомарно удаляет правило и сохраняет current обычной задачей по
SYNC_TRANSITIONS.md. Правки ordinary после stop готовятся следующим resolver.
Delete выдаёт delete с null payload только для точно соответствующего current
root, не удаляя вместо него старое выполненное тело. Даже если после opt-in
в журнале только delete без прежнего DTO, проверенного root mapping и known
regular base достаточно; текст удалённой задачи восстанавливать не требуется. Для canonical root при
удалении достаточно доказанного rule/day/root; child UUID не выдумываются.

Уже остановленная серия не обновляется по текущей дате: stop/delete того же
известного current допустим и при позднем подключении. Но когда сервер отстал
от локального последнего экземпляра, а server due day уже ушёл дальше остановки,
**terminal_day_conflict** блокирует устаревший rollover. Новую незамеченную задачу
после stop/delete planner не создаёт. Требуется отдельное разрешение перехода.
Deleted server rule не возрождается от старого save. Для уже принятого delete
выдаётся unchanged; stop tombstone без отдельного ledger ещё не считается
доказательством принятия именно нашего stop.

History→regular/ordinary→regular promotion блокируется atomic_type_transition,
Один reschedule теперь обрабатывается атомарно по SYNC_RESCHEDULE.md;
цепочки/структурные и исторические случаи всё ещё блокируются для обеих сторон. Detach покрыт history
planner блокировкой атомарной смены типа. Шаблон между пропущенными днями,
эти compound cases и terminal_day_conflict — следующая T41c5c2b, **не выполнены**
этим шагом. Не публиковать отдельно половину такого перехода.

## Хранение и продолжение

RegularProposal.id — rule target ID, не op_id. Результат содержит action,
исходные counters, payload/null, предложенные identities, source_changes и
barrier_changes. Тела/mappings скрыты в repr. RegularPlan сохраняет все
remaining_changes. Повтор одинакового входа детерминирован.
TaskStore.prepare_sync_regular читает binding/facts одним BEGIN snapshot,
считает после release lock; без привязки/при чужом generation/внешней транзакции
отказ, внешняя транзакция не коммитится/не откатывается.

Общий next_sync_sendable barrier остаётся, новые UI/сеть/аккаунты не включаются.
После compound resolver нужны durable prepare→intent→receipt, хранение mappings/
барьерных подтверждений и retirement только подтверждённых facts. Затем
calendar/category/AI capture. До inbound отдельно согласовать награды сада.

## Цепочки и изменённый шаблон

Reschedule A→B→C готовится по одному звену с подтверждением предыдущего.
Зависимая часть получает transition_dependency; подтверждённое старое звено
может ссылаться на уже закрытое новым звеном правило, если его counters не старше
предыдущего эффекта. Все исходные факты по-прежнему remaining.
Promotion→reschedule готовит сначала promotion без противоречивой блокировки
того же target, затем reschedule по известному созданному regular base.

Новый action неопубликованного v1 rollover_updated позволяет явный изменённый
шаблон **нового** экземпляра. Он сохраняет old current/history без изменений,
передаёт canonical latest root/children/items с пустыми completion/date и общей
сеткой расписания. Pure effects проверяют canonical tree от переданного нового
шаблона и latest server day, сохраняя before как отдельную history/retention
запись. Epoch как у обычного rollover не увеличивается. Это не разрешение
переписать старую выполненную задачу ради нового шаблона. Если birth DTO
невосстановим, остаётся local_birth_required. Auto enqueue/сеть ещё не включены.
