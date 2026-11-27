# Приёмка первого блока: постоянная очередь задач

**Готово для локальной Linux-приёмки, 27 ноября 2026.** Это первый из пяти
согласованных блоков desktop sync. Готовность всей синхронизации, сервера и Mini
App не заявляется. Следующий блок — календарь/категории/все AI mutation paths.

## Критерии и доказательства

| Критерий | Фактическое доказательство |
| --- | --- |
| Ordinary create/edit/complete/delete/restore | test_sync_queue_acceptance.test_raw_ordinary_create_edit_complete_delete_restore_keeps_trash_and_dates: public queue API, reopen перед каждым receipt, сохранены корзина и actual completion date |
| Current regular prune/restore | test_current_regular_subtask_can_be_pruned_and_restored; прежние capture/domain/weights проверки входят в общий набор |
| Несколько completed offline slots не схлопываются | test_sync_prepare_loop.test_resume_offline_completions_to_settled_from_cached_state; три выполнения → отдельная история → settled/idle |
| Skipped slots не объявляются completed | test_skipped_incomplete_slots_do_not_become_completed_history |
| Незавершённая команда переживает restart и новые правки | test_new_local_edit_while_request_is_pending_is_not_lost; pending возвращает прежние bytes/op_id |
| Stop/detach не блокируют дальнейшие обычные правки | test_exit_then_edit_prune_delete_restore_after_each_restart, оба origin: edit, child prune/restore, root delete/restore |
| Reopen/add subtask/add item/deadline в истории | test_history_edit_detaches_with_new_tree_or_deadline, root сохранён, новые узлы проходят global ownership gate |
| История/обычная задача снова становится regular | test_promotion_after_unsent_edits и test_history_to_regular_to_history_can_detach_back_to_ordinary; после цикла допускается новый ordinary edit |
| Source ещё не передан или сначала нужен restore | test_unuploaded_intermediate_history_can_be_promoted_then_roll_forward; test_restore_then_promote_before_restore_has_been_sent |
| Никогда не переданное расписание можно закрыть | test_never_uploaded_regular_can_close_without_losing_old_slots, stop/delete; intermediate completed history сохраняется |
| Восстановление удалённого current не перезапускает rule | test_restore_deleted_current_is_ordinary_and_does_not_restart_schedule, known/never-uploaded |
| Смена расписания и дерева, offline chains | test_change_schedule_and_tree_together; test_offline_reschedule_preserves_intermediate_completions; test_offline_schedule_chain_with_new_rules_rollovers_keeps_each_completion |
| Late stop/delete не создаёт незамеченные повторы | test_late_terminal_preserves_history_without_new_unseen_repeats: absent/live history/retained destination × stop/delete |
| Retention не очищает локальную историю и не возрождается от rename | test_retained_history_rename_is_local_and_not_resurrected; retained-stop lifecycle включает rename/delete/explicit restore без фиктивного completed history |
| UUID и generation/counters не угадываются | test_foreign_global_identity_does_not_leave_half_a_command; test_new_remote_revision_is_used_but_new_deletion_epoch_requires_review; transfer proof cases в test_sync_offline_transfers.py |
| Receipt/snapshot/mappings атомарны | test_sync_snapshot.py: SQL fault, wrong generation/watermark, contradicting entity version, многопагинационный cache, restart, v20 migration и backup stripping |
| Нет ложного retirement | test_settled_retirement_keeps_tasks_lineage_and_idempotent_receipts; test_detach_ack_does_not_cover_later_ordinary_edit; projected coverage всех трёх проекций |
| Retry/rejection/failure не теряют очередь | test_sync_outbox.py, test_sync_prepared.py и public loop rejection/outer transaction tests; старые frozen hashes не изменяются |
| Секреты/учётные данные/файлы не попадают в wire/backup replay | strict codec/source/backup suites, payload whitelist; все проверки с временными профилями, без сети/реальных аккаунтов |

Имена без file-prefix в таблице относятся к tests/test_sync_queue_acceptance.py.
Fake server response в тестах использует реальные pure transition validators и
SnapshotCollector; в production server code и сетевых запросов нет.

## Подтверждённые проверки

- Expanded DEVELOPMENT: **886 passed, 6 GUI deselected (73,77 с)**,
  `/tmp/ritm-block1-final-verified.log`. Это весь test_sync_* и согласованный
  storage/recurrence/backup/garden-events/pomodoro/mail/domain набор.
- Дополнительно три завершающих lifecycle сценария (current child restore,
  skipped slots, raw ordinary full lifecycle): **3 passed (1,15 с)**,
  `/tmp/ritm-block1-extra-acceptance.log`; production code после общего прогона
  не менялся.
- GUI backup + recurrence, offscreen: **3 passed (24,45 с)**,
  `/tmp/ritm-block1-gui-ready.log`; native Linux X11: **3 passed (23,38 с)**,
  `/tmp/ritm-block1-native-ready.log`.
- Ruff check/format изменённых Python файлов и git diff --check пройдены.

В первом native прогоне был единичный early click failure (repeatEnabled ещё
недоступен). Изолированный probe его не воспроизвёл. GuiHarness теперь ждёт
window.isExposed и фактических visible/enabled controls вместо мгновенного assert;
поведение приложения/QML не менялось. Последующие offscreen/native сценарии
прошли. Точная причина первоначального сбоя не доказана; стабильность всей GUI
suite этим не заявляется. Ручной визуальный осмотр/Windows/full app suite не выполнялись.

## Проверенные границы готовности

Unknown/missing snapshot, future/stale local day, чужое deletion epoch, чужой UUID
и rejected request возвращают needs_snapshot/blocked/conflict и сохраняют данные.
Это явные результаты очереди, не «успешно обработанные» facts. Решения пользователя
и экран конфликтов относятся к runtime/UI блоку, не подменяются автоматическим
restore. Полноту category/calendar/AI capture и приём inbound эта приёмка не доказывает.

`next_sync_sendable` сохраняет глобальный coverage barrier до второго блока.
Методы preparation не запускают транспорт. Реальная авторизация/HTTPS/сервер,
initial merge preview, incoming apply и однократные garden rewards остаются в
следующих согласованных блоках. SQLite v21/garden v7/dependencies прежние.
