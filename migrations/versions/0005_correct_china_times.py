"""correct legacy China display times

Revision ID: 0005_correct_china_times
Revises: 0004_add_ocr_review_samples
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_correct_china_times"
down_revision: str | None = "0004_add_ocr_review_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMP_COLUMNS = {
    "users": ("created_at",),
    "products": ("created_at",),
    "nav_observations": ("imported_at",),
    "update_runs": ("created_at", "started_at", "finished_at", "heartbeat_at"),
    "ocr_review_samples": ("created_at",),
    "audit_logs": ("created_at",),
    "meetings": ("imported_at", "updated_at"),
}


def _shift_timestamps(hours: int) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        sign = "+" if hours >= 0 else "-"
        for table, columns in _TIMESTAMP_COLUMNS.items():
            assignments = ", ".join(
                f"{column} = {column} {sign} INTERVAL '{abs(hours)} hours'"
                for column in columns
            )
            op.execute(sa.text(f"UPDATE {table} SET {assignments}"))
        return
    for table, columns in _TIMESTAMP_COLUMNS.items():
        assignments = ", ".join(
            f"{column} = datetime({column}, '{hours:+d} hours')" for column in columns
        )
        op.execute(sa.text(f"UPDATE {table} SET {assignments}"))


def upgrade() -> None:
    _shift_timestamps(8)


def downgrade() -> None:
    _shift_timestamps(-8)
