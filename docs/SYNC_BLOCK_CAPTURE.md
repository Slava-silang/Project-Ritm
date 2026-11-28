# Постоянный журнал календаря — T41c5d

Схема задач v22 добавляет sync_block_changes; миграция v21 и более ранних
поддерживаемых версий не создаёт вымышленные изменения существующего календаря.
Garden v7 и зависимости не менялись. Включение capture прежнее, явное/default off.

SyncBlockCaptureMixin фиксирует immutable before/after BlockRecord, вид save,
delete или restore, UUID и последовательность. Payload скрыт из repr. Это локальные
факты, ещё не сетевые операции: UUID task_id остаётся локальным до resolver.
after_intent_id и after_regular_id фиксируют последние предшествующие intent/fact.
Вторая ссылка намеренно без FK: будущий resolver обязан проверять покрытие;
сейчас retirement regular facts блокируется calendar_pending, если на них есть
ссылка календаря. Контексты не удаляются и не очищаются молча.

_write_work_block записывает факт внутри owning transaction для ручной правки,
ИИ-расписания и mail-event. No-op не дублируется. Архивирование главной задачи
записывает delete каждого связанного блока до удаления корня, восстановление —
restore после восстановления корня. Физические work_blocks сохраняются в корзине;
обычный list_work_blocks скрывает их. Удаление подзадач видимость не меняет.
Очистка корзины не заменяет первоначальный logical delete. Incoming/bootstrap
suppression сохраняет прежние правила и не создаёт echo.

Ошибка journal insert откатывает календарь, задачу, очередь, корзину и receipt
в общей транзакции. Повторное применение AI draft с квитанцией не дублирует факты.
Backup validator принимает v21 и v22; старые схемы проверяются без новой таблицы.
Создание архива очищает journal/sequence только в staged copy; исходный профиль
сохраняется. Рабочий календарь, включая локальную историю, не обрезается.

## Граница готовности

Журнал и atomic capture готовы. Подготовка block facts в wire-команды ещё не
реализована: нельзя обходить подтверждение task/regular parent или подставлять
archived caption для ещё не опубликованной задачи. Следующий шаг — resolver,
local↔wire mapping, корректный create/put/delete/restore, 30-day cloud retention
и caption для действительно исключённого по retention корня. Затем общий аудит
AI paths и приёмка второго блока. Global transport barrier остаётся; inbound,
реальные аккаунты, сервер и Mini App этим шагом не включаются.

## Проверки

Expanded 911 passed/6 GUI deselected (76,33 с),
/tmp/ritm-block-journal-expanded.log. Затем четыре дополнительных сценария
AI/mail/retirement/archive вошли в итоговый focused 13 passed (1,91 с),
/tmp/ritm-block-journal-final-focused.log. AI+planning subset 34 passed/2 deselected
(4,21 с). GUI backup+recurrence offscreen 3 passed (23,77 с).
Ruff check/format и diff passed. Windows/native/manual не проверены.
