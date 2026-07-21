import importlib
from types import SimpleNamespace


class _BatchOperations:
    def __init__(self, calls: list[tuple[object, ...]]) -> None:
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def drop_constraint(self, name: str, *, type_: str) -> None:
        self.calls.append(("drop", name, type_))

    def alter_column(self, name: str, **kwargs) -> None:
        self.calls.append(("alter", name, kwargs))

    def create_foreign_key(
        self,
        name: str,
        target_table: str,
        local_columns: list[str],
        remote_columns: list[str],
        **kwargs,
    ) -> None:
        self.calls.append(
            ("create", name, target_table, local_columns, remote_columns, kwargs)
        )


class _PostgresOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def batch_alter_table(self, *args, **kwargs) -> _BatchOperations:
        return _BatchOperations(self.calls)

    def drop_constraint(self, name: str, table_name: str, *, type_: str) -> None:
        self.calls.append(("drop", name, table_name, type_))

    def alter_column(self, table_name: str, name: str, **kwargs) -> None:
        self.calls.append(("alter", table_name, name, kwargs))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        **kwargs,
    ) -> None:
        self.calls.append(
            ("create", name, source_table, referent_table, local_cols, remote_cols, kwargs)
        )


def test_deleted_user_history_migration_uses_existing_postgres_constraint_names(
    monkeypatch,
) -> None:
    migration = importlib.import_module("migrations.versions.0003_allow_deleted_user_history")
    operations = _PostgresOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert ("drop", "update_runs_operator_id_fkey", "update_runs", "foreignkey") in operations.calls
    assert ("drop", "audit_logs_actor_id_fkey", "audit_logs", "foreignkey") in operations.calls
