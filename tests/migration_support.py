"""Remove only later tables when building synthetic historical SQLite fixtures."""


def drop_later_task_tables(db, version):
    if version == 18:
        from ritm.storage.sync_regular_capture import REGULAR_CAPTURE_SCHEMA_V18

        columns = "seq,id,context_id,after_intent_id,kind,rule_id,root_id,before_json,after_json"
        db.execute("ALTER TABLE sync_regular_changes RENAME TO regular_v19_fixture")
        db.execute(REGULAR_CAPTURE_SCHEMA_V18[0])
        db.execute(
            f"INSERT INTO sync_regular_changes ({columns}) "
            f"SELECT {columns} FROM regular_v19_fixture"
        )
        db.execute("DROP TABLE regular_v19_fixture")
    for table, introduced in (
        ("sync_regular_roots", 19),
        ("sync_regular_changes", 18),
        ("sync_capture", 17),
        ("sync_task_presence", 17),
        ("sync_outbox", 16),
        ("sync_intents", 15),
        ("sync_binding", 15),
        ("recurrence_occurrences", 14),
        ("recurrence_rules", 14),
    ):
        if version < introduced:
            db.execute(f"DROP TABLE IF EXISTS {table}")
