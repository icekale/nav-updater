"""add OCR review samples

Revision ID: 0004_add_ocr_review_samples
Revises: 0003_allow_deleted_user_history
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_ocr_review_samples"
down_revision: str | None = "0003_allow_deleted_user_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ocr_review_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("run_item_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("excel_product_name", sa.String(length=255), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("ocr_match_source", sa.String(length=30), nullable=False),
        sa.Column("ocr_product_id", sa.Integer(), nullable=True),
        sa.Column("ocr_metric_values", sa.JSON(), nullable=False),
        sa.Column("ocr_metric_status", sa.JSON(), nullable=False),
        sa.Column("confirmed_metric_values", sa.JSON(), nullable=False),
        sa.Column("confirmed_metric_status", sa.JSON(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ocr_product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["update_runs.id"]),
        sa.ForeignKeyConstraint(["run_item_id"], ["run_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ocr_review_samples_run_id", "ocr_review_samples", ["run_id"])
    op.create_index("ix_ocr_review_samples_run_item_id", "ocr_review_samples", ["run_item_id"])
    op.create_index("ix_ocr_review_samples_actor_id", "ocr_review_samples", ["actor_id"])
    op.create_index("ix_ocr_review_samples_product_id", "ocr_review_samples", ["product_id"])
    op.create_index(
        "ix_ocr_review_samples_ocr_product_id", "ocr_review_samples", ["ocr_product_id"]
    )
    op.create_index("ix_ocr_review_samples_created_at", "ocr_review_samples", ["created_at"])


def downgrade() -> None:
    op.drop_table("ocr_review_samples")
