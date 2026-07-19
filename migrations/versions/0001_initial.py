"""initial schema

Revision ID: 0001_initial
Revises:
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.String(length=255), nullable=False),
        sa.Column("product_code", sa.String(length=100), nullable=False),
        sa.Column("product_type", sa.String(length=20), nullable=False),
        sa.Column("historical_names", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_code"),
    )
    op.create_index("ix_products_product_code", "products", ["product_code"], unique=False)
    op.create_table(
        "nav_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("cumulative_nav", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "nav_date", "source_kind", name="uq_nav_source"),
    )
    op.create_index("ix_nav_observations_product_id", "nav_observations", ["product_id"], unique=False)
    op.create_table(
        "update_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("cutoff_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("output_path", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_update_runs_operator_id", "update_runs", ["operator_id"], unique=False)
    op.create_index("ix_update_runs_status", "update_runs", ["status"], unique=False)
    op.create_table(
        "run_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("file_type", sa.String(length=20), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["update_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_files_run_id", "run_files", ["run_id"], unique=False)
    op.create_table(
        "run_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("excel_row", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("match_source", sa.String(length=30), nullable=False),
        sa.Column("row_status", sa.String(length=30), nullable=False),
        sa.Column("metric_values", sa.JSON(), nullable=False),
        sa.Column("metric_status", sa.JSON(), nullable=False),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("original_values", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["update_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_items_product_id", "run_items", ["product_id"], unique=False)
    op.create_index("ix_run_items_run_id", "run_items", ["run_id"], unique=False)
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", sa.String(length=100), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"], unique=False)


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("run_items")
    op.drop_table("run_files")
    op.drop_table("update_runs")
    op.drop_table("nav_observations")
    op.drop_index("ix_products_product_code", table_name="products")
    op.drop_table("products")
    op.drop_table("users")
