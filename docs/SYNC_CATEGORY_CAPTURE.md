# Категории и зависимости задач — T41c5d

Второй блок desktop sync принят: SYNC_BLOCK2_ACCEPTANCE.md.
Ниже сохранён исходный category checkpoint; calendar capture/resolver уже подключены.
Категории записываются через SyncCatalogCaptureMixin в транзакции владельца
TaskStore._write_category. Capture использует существующий opt-in флаг, default
off. Schema tasks v21/garden v7/dependencies прежние; новой таблицы не требуется.

## Каталог

Новая категория → create; изменение → put; неизменное название не дублирует
намерение. Если уже есть category intent, последующие правки зависят от него через
общий FIFO predecessor. Known full snapshot определяет known live/mark/absence.
Отсутствие snapshot не считается отсутствием сущности: rename ранее существовавшей
категории остаётся put до получения действительного base. Deleted mark не
возрождается обычным rename автоматически. Флаг capture или binding сами по
себе не являются первоначальным merge/auth: bootstrap/preview остаётся отдельным.

Публичный save_category берёт BEGIN IMMEDIATE до чтения старого значения.
Private _write_category используется внутри create/update task и regular save.
Ошибка записи intent откатывает category и весь parent task/regular/proposal
transaction. Incoming/bootstrap suppression не меняет глобальный capture flag
и не создаёт echo. Backup/import исключает category intents через прежний
queue sanitization, рабочий профиль не очищается.

## Ссылка задачи на категорию

Task write и explicit regular journal frame проверяют _ensure_category_dependency.
Если категория уже live в cache или её intent существует, повторного create нет.
Known missing category читается из локального каталога и ставится в очередь до
Task intent/regular fact. Unknown bootstrap catalog не заменяется выдуманным
absence. Отсутствующая локально категория — ошибка, parent transaction откатывается.

Новый captured task intent зависит от последнего global intent, поэтому не
обходит preceding category. Regular journal after_intent_id ссылается на этот
predecessor. Это не меняет bytes уже frozen запросов. Public queue API/restart
проверены: category ack/snapshot прежде dependent task, полный graph валиден.

## Аудит путей записи и следующий шаг

| Мутация | Owning path / текущая готовность |
| --- | --- |
| Категория standalone / вместе с задачей | _write_category, capture готов |
| TaskService create/edit/AI proposal | _write_task → task/regular capture + category dependency |
| apply_proposal + material links/receipt | Тот же transaction; ошибка материалов откатывает intents |
| apply_mail_proposal для Task | _write_task, существующий task capture; реального Gmail/model запроса в проверках нет |
| create_work_block/update_work_block | Теперь единый _write_work_block внутри транзакции |
| apply_schedule от помощника | Тот же _write_work_block для каждого блока, общий receipt transaction |
| apply_mail_proposal для события | Тот же _write_work_block вместо обходного upsert |
| Soft-delete/restore Task и связанные blocks | Следующий calendar capture должен учитывать логическую видимость, не только SQL удаления |
| purge_trash cleanup | Удаляет локальные связанные blocks; не заменяет sync delete при первоначальном archive |

_write_work_block централизует insert/update без изменения поведения: update
сохраняет UUID и task link, source/draft проверки и BEGIN принадлежат вызывающему
API. Category delete и standalone event delete в текущем desktop API отсутствуют;
новые пользовательские команды этим шагом не добавляются.

Следующее: bounded durable block journal/capture с parent root dependency,
local↔wire mapping повторов, independent events, 30-day cloud retention и archived
caption. Hook нужен и в _write_work_block, и при archive/restore задачи. Calendar
нельзя freeze раньше подтверждённого task/regular source. Затем сквозной аудит
всех AI mutation paths и fault/restart/backup acceptance всего второго блока.
HTTP/inbound/реальный сервер и транспорт по-прежнему не включены.

## Проверки

13 category scenarios passed (1,20 с): ordering, known/unknown catalog, no-op,
restart, suppress origin, regular dependency, SQL failures before/after enqueue,
AI proposal material rollback и category→task exchange через full snapshot.
Expanded DEVELOPMENT 902 passed/6 GUI deselected (75,81 с),
/tmp/ritm-catalog-capture-final-suite.log. Calendar/mail/AI writer subset 71 passed/
1 GUI deselected (3,85 с), /tmp/ritm-catalog-calendar-writers.log.
GUI backup+regular offscreen 3 passed (23,94 с), native X11 3 passed (23,32 с).
Ruff check/format 5 Python и diff passed. Windows/manual/full app suite не
выполнялись; проверки с временными профилями, без аккаунтов/сети/платных моделей.
