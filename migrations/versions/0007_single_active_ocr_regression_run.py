"""prevent concurrent active OCR regression runs

Revision ID: 0007_single_active_ocr_regression_run
Revises: 0006_add_ocr_regression_loop
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_single_active_run"
down_revision: str | None = "0006_add_ocr_regression_loop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    predicate = sa.text("status IN ('queued', 'running')")
    op.create_index(
        "uq_ocr_regression_active_status",
        "ocr_regression_runs",
        [sa.text("(1)")],
        unique=True,
        sqlite_where=predicate,
        postgresql_where=predicate,
    )


def downgrade() -> None:
    op.drop_index("uq_ocr_regression_active_status", table_name="ocr_regression_runs")
