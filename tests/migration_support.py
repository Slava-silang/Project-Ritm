"""Remove only later tables when building synthetic historical SQLite fixtures."""


def drop_later_task_tables(db, version):
    for table, introduced in (
        ("sync_intents", 15),
        ("sync_binding", 15),
        ("recurrence_occurrences", 14),
        ("recurrence_rules", 14),
    ):
        if version < introduced:
            db.execute(f"DROP TABLE IF EXISTS {table}")
