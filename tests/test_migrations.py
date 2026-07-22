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

    def execute(self, statement) -> None:
        self.calls.append(("execute", str(statement)))

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


class _RegressionOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def add_column(self, table_name: str, column) -> None:
        self.calls.append(("add_column", table_name, column.name))

    def alter_column(self, table_name: str, column_name: str, **kwargs) -> None:
        self.calls.append(("alter_column", table_name, column_name, kwargs))

    def create_table(self, table_name: str, *columns, **kwargs) -> None:
        self.calls.append(("create_table", table_name, [column.name for column in columns]))

    def create_index(self, name: str, table_name: str, columns, **kwargs) -> None:
        self.calls.append(("create_index", name, table_name, columns))

    def drop_index(self, name: str, **kwargs) -> None:
        self.calls.append(("drop_index", name))

    def drop_table(self, table_name: str) -> None:
        self.calls.append(("drop_table", table_name))

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.calls.append(("drop_column", table_name, column_name))


def test_deleted_user_history_migration_uses_existing_postgres_constraint_names(
    monkeypatch,
) -> None:
    migration = importlib.import_module("migrations.versions.0003_allow_deleted_user_history")
    operations = _PostgresOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert ("drop", "update_runs_operator_id_fkey", "update_runs", "foreignkey") in operations.calls
    assert ("drop", "audit_logs_actor_id_fkey", "audit_logs", "foreignkey") in operations.calls


def test_china_time_migration_shifts_existing_postgres_timestamps(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.0005_correct_china_times")
    operations = _PostgresOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    statements = "\n".join(str(call) for call in operations.calls)
    assert "update_runs" in statements
    assert "created_at = created_at + INTERVAL '8 hours'" in statements
    assert "ocr_review_samples" in statements


def test_ocr_regression_migration_creates_and_drops_regression_tables(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.0006_add_ocr_regression_loop")
    operations = _RegressionOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    assert ("add_column", "run_items", "ocr_evidence") in operations.calls
    assert any(call[:2] == ("create_table", "ocr_regression_samples") for call in operations.calls)
    assert any(call[:2] == ("create_table", "ocr_regression_runs") for call in operations.calls)
    assert any(call[:2] == ("create_table", "ocr_regression_results") for call in operations.calls)

    operations.calls.clear()
    migration.downgrade()
    assert operations.calls[:3] == [
        ("drop_table", "ocr_regression_results"),
        ("drop_table", "ocr_regression_runs"),
        ("drop_table", "ocr_regression_samples"),
    ]
    assert operations.calls[-1] == ("drop_column", "run_items", "ocr_evidence")


def test_active_regression_migration_adds_and_removes_single_run_index(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.0007_single_active_ocr_regression_run")
    operations = _RegressionOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()
    create_index = next(
        call for call in operations.calls if call[:3] == (
            "create_index",
            "uq_ocr_regression_active_status",
            "ocr_regression_runs",
        )
    )
    assert [str(column) for column in create_index[3]] == ["(1)"]

    operations.calls.clear()
    migration.downgrade()
    assert operations.calls == [("drop_index", "uq_ocr_regression_active_status")]


def test_active_regression_migration_revision_fits_alembic_version_column() -> None:
    migration = importlib.import_module("migrations.versions.0007_single_active_ocr_regression_run")

    assert len(migration.revision) <= 32
