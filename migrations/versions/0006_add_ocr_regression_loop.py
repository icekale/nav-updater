"""add OCR regression loop data

Revision ID: 0006_add_ocr_regression_loop
Revises: 0005_correct_china_times
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_add_ocr_regression_loop"
down_revision: str | None = "0005_correct_china_times"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_items",
        sa.Column(
            "ocr_evidence",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("run_items", "ocr_evidence", server_default=None)

    op.create_table(
        "ocr_regression_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_run_id", sa.Integer(), nullable=True),
        sa.Column("source_item_id", sa.Integer(), nullable=True),
        sa.Column("source_label", sa.String(length=80), nullable=False),
        sa.Column("excel_product_name", sa.String(length=255), nullable=False),
        sa.Column("candidate_names", sa.JSON(), nullable=False),
        sa.Column("expected_product_code", sa.String(length=100), nullable=True),
        sa.Column("expected_metric_values", sa.JSON(), nullable=False),
        sa.Column("expected_metric_status", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_item_id"], ["run_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_run_id"], ["update_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ocr_regression_samples_image_sha256",
        "ocr_regression_samples",
        ["image_sha256"],
    )
    op.create_index(
        "ix_ocr_regression_samples_source_run_id",
        "ocr_regression_samples",
        ["source_run_id"],
    )
    op.create_index(
        "ix_ocr_regression_samples_source_item_id",
        "ocr_regression_samples",
        ["source_item_id"],
    )
    op.create_index(
        "ix_ocr_regression_samples_created_by",
        "ocr_regression_samples",
        ["created_by"],
    )
    op.create_index(
        "ix_ocr_regression_samples_created_at",
        "ocr_regression_samples",
        ["created_at"],
    )
    op.create_index(
        "ix_ocr_regression_samples_is_active",
        "ocr_regression_samples",
        ["is_active"],
    )

    op.create_table(
        "ocr_regression_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ocr_regression_runs_status", "ocr_regression_runs", ["status"])
    op.create_index(
        "ix_ocr_regression_runs_requested_by", "ocr_regression_runs", ["requested_by"]
    )
    op.create_index(
        "ix_ocr_regression_runs_created_at", "ocr_regression_runs", ["created_at"]
    )

    op.create_table(
        "ocr_regression_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("expected", sa.JSON(), nullable=False),
        sa.Column("actual", sa.JSON(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["ocr_regression_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["sample_id"], ["ocr_regression_samples.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ocr_regression_results_run_id", "ocr_regression_results", ["run_id"])
    op.create_index(
        "ix_ocr_regression_results_sample_id", "ocr_regression_results", ["sample_id"]
    )


def downgrade() -> None:
    op.drop_table("ocr_regression_results")
    op.drop_table("ocr_regression_runs")
    op.drop_table("ocr_regression_samples")
    op.drop_column("run_items", "ocr_evidence")
