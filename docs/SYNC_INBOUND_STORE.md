# Согласованный входящий storage — T41c5e

Schema tasks v23 добавляет sync_applied_snapshot: отдельный bounded полный
APPLIED baseline под текущей binding, в отличие от полученного outgoing cache.
Миграция v22 и прежних поддерживаемых схем создаёт пустую таблицу, не объявляет
старый cache применённым и не меняет задачи/очередь. Garden v7/deps прежние.
Backup validator принимает v22/v23; staged sanitizer убирает applied metadata
до binding только в копии. Рабочая БД не очищается. Старым клиентом v22 профиль
v23 не открывать и user_version вручную не понижать.

## API и согласованное чтение

SyncInboundMixin даёт preview_sync_incoming, apply_sync_incoming и
read_sync_applied_snapshot. Binding и явный capture required, нет HTTP/auth/UI.
Preview читает под BEGIN, apply заново собирает план под BEGIN IMMEDIATE:
canonical local DTO, inverse mappings, applied baseline, queue/facts и reserved
trash/file owners принадлежат одному SQLite snapshot. Preview нельзя выдавать
за разрешение применить устаревший план. WAL test меняет дерево из другого
соединения между чтениями: preview согласован со старым snapshot, apply видит
новую очередь и блокируется.

Защищены unfrozen/unacked/rejected intents, regular/history и block journal,
skipped roots, локальная корзина и UUID владельцев файлов. protected_inputs
добавляет несохранённый UI-ввод (local root/node IDs переводятся в canonical).
Настоящий UI должен передавать этот набор либо не применять данные при dirty
editor. Пока UI/runtime не подключены. Applied собственные intents не считаются
вечными pending: последняя verified applied receipt и совпадающий canonical
payload дают acknowledged_local proof. Удаление не считается разрешением вернуть
live без нового epoch. Этот proof не строится из одного равенства cloud body.

## Атомарно поддерживаемый объём

- Чистый bootstrap и ordinary insert/update без удаления узлов/нового выполнения,
  category create/update, standalone и linked block create/update с доступным
  локальным корнем. Canonical wire UUID не перемещает локальные файлы/владельцев.
- Старые completed DTO при bootstrap сохраняют точную completed_at. Все writes
  идут через existing owning methods под origin=incoming, без outbox echo.
  Task write restore_event=True синхронизирует garden mirror без старых наград
  и first-task бонуса; replay не создаёт повторных garden events.
- Retention/remote absence оставляет local history. Retained root получает
  presence=retained, чтобы rename не вернул старую историю обычным put.
  Matching bootstrap root становится live: следующая локальная правка — put,
  не duplicate create. Remote task markers резервируют UUID, но не стирают
  местную корзину; явный remote restore требует нового epoch.
- Data, presence/identity metadata, garden source events, received cache и APPLIED
  blob коммитятся вместе. Ошибка SQL на tree/block/baseline откатывает весь набор.
  Новый watermark сохраняется даже без body changes; exact replay — unchanged.

Первое применение требует bootstrap=True. Это внутреннее намерение caller,
а НЕ доказательство пользовательского согласия или наличия backup. Реальный
runtime сначала обязан создать копию/предпросмотр/получить выбор. Флаг не
разрешает перезапись initial UUID conflict: такие планы остаются blocked.

## Граница этапа

Batch целиком остаётся blocked при conflict/pending или пока не поддерживаемом
archive/prune/regular/history transition. Incremental появление completed flags
на любом уровне ждёт completion ledger; cursor не продвигается и награда не
теряется. Cloud block с archived caption без локального корня ждёт отдельного
представления orphan calendar, а не падает по FK или исчезает. Поддержка отдельных
неконфликтующих групп при конфликте другой группы ещё не включена.

Это ограниченный реальный путь записи, НЕ завершённый третий блок. Дальше:
atomic incoming deletion/prune/role lifecycle с local trash/files preservation,
архивные подписи календаря без local root, completion dedupe ledger и новые
remote garden awards, initial conflict choices и сквозная приёмка. Сервер,
настоящий transport и подключение UI не запускать.

## Проверки

Focused inbound store+pure+intent: 71 passed (5,93 с),
/tmp/ritm-inbound-store-final-focused.log. Expanded: 1049 passed/9 GUI deselected
(98,34 с), /tmp/ritm-inbound-store-expanded.log. Migration v21/v22, old v21/v22
archive fixtures, backup copy stripping/paired rollback входят в набор.
GUI Linux offscreen backup+regular capture off/on: 3 passed (24,87 с),
/tmp/ritm-inbound-store-gui.log. Ruff check/format 9 Python и diff passed.
Windows/native/manual/full application suite не выполнялись; тесты используют
временные профили и fake snapshots/receipts, без личных данных/сети.
