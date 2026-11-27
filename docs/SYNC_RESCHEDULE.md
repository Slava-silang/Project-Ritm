# Атомарная смена расписания — T41c5c2b1

Согласованный порядок T41 сохраняется. Это ограниченная часть compound resolver,
без сервера/HTTP/inbound/изменения интерфейса. Tasks v19/garden v7/deps прежние.

## Команда неопубликованного wire v1

`reschedule_regular`: target_kind=regular, target_id=старый rule UUID,
payload=новый RegularRecord с **другим** rule UUID. Это единственное действие,
где ID payload отличается от target_id. Для остальных create/put/restore/prune/
rollover прежнее соответствие обязательно. Тип payload/kind/action проверяется
в Operation и SyncIntent одним payload_matches_target helper. Пустое тело,
тот же rule UUID, другой target kind и обычный put с чужим payload ID запрещены.

Base revision/epoch относятся к старому правилу. Applied Receipt связан с
исходным op/device/server/hash, требует revision>base и epoch+1. Замороженный
request не переписывается при retry; смена основания требует отдельного
разрешения, как раньше. Это расширение ещё не опубликованного v1, не downgrade:
старый строгий клиент должен отказать, а не пропустить новый action.

## Два эффекта одной операции

`plan_regular_effects(..., vacant_rule_id=new_id)` требует known live old rule,
точные CAS/counters/applied receipt и конкретный новый UUID, для которого будущий
server caller проверил отсутствие **внутри той же SQL-транзакции**. Параметр UUID
сам по себе не доказывает отсутствие/owner/auth; это чистый planner, не сервер.

Условия: другой rule ID, действительно изменён first_day/interval, current_day
нового правила равен scheduled_day по общему account timezone/server moment;
root и структура node IDs/позиций совпадают с известным старым деревом. Поля,
названия, веса и выполнение могут измениться вместе со сменой расписания.
Добавление/удаление/перестановка node IDs этим ограниченным переходом не допускается.

Результат — ровно две SnapshotEntry с общей receipt revision/epoch:

1. user_deleted mark старого regular, без текста и root tombstone;
2. новый live RegularRecord с тем же root/child/item UUID.

Старое completed history не удаляется. Root не копируется и не превращается
в отдельную ordinary task между правилами. Новый UUID не может быть существующей
записью или tombstone. Ошибка не возвращает половину результата. Реальная запись
обоих эффектов/owner isolation/replay ledger — T41d, пока не реализована.

## Подготовка из local journal

Current planner распознаёт один save OldRegular→NewRegular и проверяет известные
bases **обеих** сторон. Для нового rule нужен явный absent base, не пропуск в
snapshot. Новый tombstone/существующая запись, frozen source, неизвестные UUID/
base, смена структуры, неактуальный день или конфликт свободных IDs блокируют
обе стороны; отдельный delete/create не предлагается.

Если старый current ещё не согласован, сначала выдаётся его обычный следующий
шаг: put/prune/restore либо canonical rollover с известным base. После нового
проверенного context готовится reschedule_regular. ID самого root сохраняется;
предложение содержит transition_change UUID именно исходного reschedule fact,
а не всех последующих правок.

`RegularContext.acknowledged_reschedules` допускает этот fact только после
проверенной receipt/обоих эффектов из будущего ledger. Один похожий snapshot
без такого подтверждения не принимается за нашу выполненную смену расписания.
Противоречие acknowledgement и bases блокируется. Frozen/ack sets не могут
пересекаться. Новый target после перехода использует собственный известный
SnapshotEntry: old-target receipt не становится автоматически его SyncBase.

Поздние правки new rule не помечаются применёнными одним reschedule ack:
они готовятся следующим put на новом rule с его подтверждёнными counters.
Все journal facts остаются remaining, mappings/ack marks ещё не сохраняются
автоматически. Наличие regular facts продолжает блокировать отправку.

## Оставшаяся часть compound resolver

Несколько связанных reschedule за offline-период (A→B→C) пока блокируются
reschedule_chain_required, без публикации A→B как будто вся цепь готова.
Старое правило, ещё никогда не опубликованное, при known absence также не
получает выдуманное reschedule основание; нужен отдельный план сведения.
Исторический reschedule day, changed intermediate template, promotion/detach,
сложные terminal day и structural transitions остаются следующими частями
T41c5c2b. Это явные границы, не завершённые функции.

Storage умеет сохранить/freeze/прочитать новую **явно добавленную** SyncIntent и
Receipt, но автоматическое превращение фактов в durable очередь/retire facts/
server transport этим шагом не подключается. Схема БД/публичные копии прежние.
