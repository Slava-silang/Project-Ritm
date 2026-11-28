# Приёмка второго блока: категории, календарь и записи ИИ

## Объём

T41c5d завершает локальную запись и подготовку исходящих category/calendar/AI
изменений поверх первого блока. Схема tasks v22/garden v7, зависимости прежние.
Исходящая очередь работает через disposable TaskStore и fake verified receipts/
full snapshots. Это не запуск сети, вход в облако или применение входящих данных.
Default capture off остаётся; явное подключение/предпросмотр — будущий runtime.

## Критерии и доказательства

| Критерий | Реализация и проверка |
| --- | --- |
| Категория атомарна с задачей и предшествует зависимому дереву | sync_catalog_capture; test_sync_category_capture: known/unknown base, no-op, regular dependency, rename, failures, category→task exchange |
| Все календарные записи проходят общий capture | sqlite._write_work_block: manual create/update, apply_schedule, apply_mail_proposal event; test_sync_block_capture |
| Archive/restore корня сохраняет логические delete/restore blocks | before/after journal в owning transaction, локальные blocks остаются; task/history/current/canonical acceptance |
| Задача подтверждается до зависимого блока | prepare_next_sync после regular/history/lineage coverage; canonical mapping меняет только wire task_id |
| Повторы запросов и новые правки во время pending не теряются | immutable fact/op UUID, restart каждого обмена, prefix ack retirement; test_sync_block_prepare |
| Очистка не опережает доказательства | applied receipt + covering target entry, payload/epoch exact-revision check, SQL rollback prefix+next freeze |
| Удаление current/history и поздний stop/delete корректны | test_sync_block2_acceptance: regular/history × own/foreign, canonical current, absent/history/retained late destination |
| Пропущенный незавершённый повтор не загружается как история | skipped local journal исключён; published blocks получают retention side effect той же source revision |
| История выполненного повтора остаётся доступна календарю | completed source → OccurrenceRecord, block остаётся привязан к его root |
| Лимиты облака не режут локальную историю | 30-day UTC/DST inclusive boundary, retained occurrence caption; старое local не возвращается обычным refresh |
| Own cascade не разрешает foreign deletion epoch | stable lineage + canonical mappings + exact source receipt/block-marker revision; foreign branch сохраняет conflict/facts |
| ИИ create/update не обходят очередь | proposal create/update, mail task/event create/update, schedule batches — полный exchange/restart и receipt replay |
| Ошибка ИИ-квитанции не оставляет частичный результат | fault в applied_proposals/mail_proposals откатывает data, intents, block journal, source link; другие material/second-block failures — прежние tests |
| Служебные данные не попадают в DTO/архив подключения | raw fake requests без email/fingerprint, DTO allowlist, backup strips binding/queue/journal только в копии |

## Аудит владельцев записи

Поиск INSERT/UPDATE/DELETE nodes/categories/work_blocks не нашёл обходных AI write
paths вне sqlite owning methods и миграций/физической очистки корзины. TaskService
и task_updates используют apply_proposal → _write_task. Mail task использует тот
же _write_task; mail event и schedule — общий _write_work_block. Receipt checks
остаются у вызывающей транзакции. UI-предпросмотр сам не вызывает эти методы.

## Каскады и подтверждение

sync_calendar_effects.calendar_transition_effects — чистый DTO-контракт
side effects уже проверенного typed source transition, не сервер и не механизм
авторизации. Его caller сначала планирует source через существующие validators.
Task/occurrence user-delete даёт linked block user_deleted с epoch+1; неполный
старый current, заменённый/закрытый без live representation, даёт retention без
роста epoch. Другие roots, standalone events и completed history сохраняются.
Оба вида marker принадлежат той же receipt revision, что и source transition.

Fake exchange применяет этот контракт. save_sync_receipt_and_snapshot сверяет
обязательные calendar effects объявленного source transition с полным снимком.
Missing/contradicting effect откатывает receipt, phase mappings и cache вместе;
новое подтверждение может быть применено повторно. Более новая целевая revision
может законно перекрыть результат. Старое содержимое в cloud не выдаётся за
подтверждение удаления лишь по общему set_revision.

## Проверки

Acceptance 26 passed (10,79 с), /tmp/ritm-block2-acceptance-complete.log;
затем acceptance + block prepare + prepare loop с дополнительным receipt-proof
59 passed (19,04 с), /tmp/ritm-block2-proof.log. Финальный expanded: **995 passed/9 GUI deselected (97,67 с)**,
/tmp/ritm-block2-verified.log. Ruff check/format шести Python и diff passed.
GUI Linux offscreen: assistant, planning/restart/scroll, recurrence capture off/on,
backup — 6 passed (104,16 с), /tmp/ritm-block2-gui.log. Native X11 assistant+
recurrence — 3 passed (28,24 с), /tmp/ritm-block2-native.log. Эти GUI-прогоны до
добавления последнего receipt-proof guard; его рабочий путь покрыт focused/full
storage tests, пользовательский интерфейс не менялся. Windows/manual/full app
suite не проверялись. Сеть, реальные аккаунты/модели и личные профили не использованы.

## Следующий блок

Inbound/initial merge/retention/local preservation и exactly-once garden rewards
за новые remote completions. Runtime/UI/auth/vault/fake transport и сквозные два
клиента идут после него. До этих этапов реальная отправка не включается; сервер
и Mini App не начинаются. Никакого сокращения согласованного выпуска этим нет.
