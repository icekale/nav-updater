"""add meeting storage

Revision ID: 0002_add_meetings
Revises: 0001_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_meetings"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("date_raw", sa.String(length=100), nullable=False),
        sa.Column("date_start", sa.Date(), nullable=True),
        sa.Column("date_end", sa.Date(), nullable=True),
        sa.Column("date_parse_status", sa.String(length=30), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("core_statement", sa.Text(), nullable=False),
        sa.Column("market_impact", sa.Text(), nullable=False),
        sa.Column("research_mapping", sa.Text(), nullable=False),
        sa.Column("follow_up", sa.Text(), nullable=False),
        sa.Column("source_link", sa.Text(), nullable=False),
        sa.Column("source_updated_at", sa.String(length=100), nullable=False),
        sa.Column("company_tags", sa.Text(), nullable=False),
        sa.Column("industry_tags", sa.Text(), nullable=False),
        sa.Column("attendance_status", sa.String(length=20), nullable=False),
        sa.Column("minutes", sa.Text(), nullable=False),
        sa.Column("todo", sa.Text(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key"),
    )
    op.create_index("ix_meetings_date_start", "meetings", ["date_start"], unique=False)
    op.create_index("ix_meetings_date_end", "meetings", ["date_end"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_meetings_date_end", table_name="meetings")
    op.drop_index("ix_meetings_date_start", table_name="meetings")
    op.drop_table("meetings")
