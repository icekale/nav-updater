"""retain history when a user is deleted

Revision ID: 0003_allow_deleted_user_history
Revises: 0002_add_meetings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_allow_deleted_user_history"
down_revision: str | None = "0002_add_meetings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("update_runs_operator_id_fkey", "update_runs", type_="foreignkey")
        op.alter_column("update_runs", "operator_id", existing_type=sa.Integer(), nullable=True)
        op.create_foreign_key(
            "fk_update_runs_operator_id_users",
            "update_runs",
            "users",
            ["operator_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.drop_constraint("audit_logs_actor_id_fkey", "audit_logs", type_="foreignkey")
        op.create_foreign_key(
            "fk_audit_logs_actor_id_users",
            "audit_logs",
            "users",
            ["actor_id"],
            ["id"],
            ondelete="SET NULL",
        )
        return
    with op.batch_alter_table(
        "update_runs", recreate="always", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint("fk_update_runs_operator_id_users", type_="foreignkey")
        batch.alter_column("operator_id", existing_type=sa.Integer(), nullable=True)
        batch.create_foreign_key(
            "fk_update_runs_operator_id_users",
            "users",
            ["operator_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table(
        "audit_logs", recreate="always", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint("fk_audit_logs_actor_id_users", type_="foreignkey")
        batch.create_foreign_key(
            "fk_audit_logs_actor_id_users",
            "users",
            ["actor_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM update_runs WHERE operator_id IS NULL")):
        raise RuntimeError("cannot restore required operator_id while deleted-user history exists")
    if connection.dialect.name == "postgresql":
        op.drop_constraint("fk_audit_logs_actor_id_users", "audit_logs", type_="foreignkey")
        op.create_foreign_key(
            "audit_logs_actor_id_fkey", "audit_logs", "users", ["actor_id"], ["id"]
        )
        op.drop_constraint("fk_update_runs_operator_id_users", "update_runs", type_="foreignkey")
        op.alter_column("update_runs", "operator_id", existing_type=sa.Integer(), nullable=False)
        op.create_foreign_key(
            "update_runs_operator_id_fkey", "update_runs", "users", ["operator_id"], ["id"]
        )
        return
    with op.batch_alter_table(
        "audit_logs", recreate="always", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint("fk_audit_logs_actor_id_users", type_="foreignkey")
        batch.create_foreign_key(
            "fk_audit_logs_actor_id_users", "users", ["actor_id"], ["id"]
        )
    with op.batch_alter_table(
        "update_runs", recreate="always", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint("fk_update_runs_operator_id_users", type_="foreignkey")
        batch.alter_column("operator_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_update_runs_operator_id_users", "users", ["operator_id"], ["id"]
        )
