"""Add deletion counters and deleted timetable audit actions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _audit_constraint() -> dict[str, object] | None:
    for constraint in sa.inspect(op.get_bind()).get_check_constraints(
        "timetable_import_audit"
    ):
        if constraint.get("name") == "ck_timetable_audit_action":
            return constraint
    return None


def upgrade() -> None:
    columns = {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(
            "timetable_import_runs"
        )
    }
    for name in ("deleted_courses", "deleted_sessions"):
        if name not in columns:
            op.add_column(
                "timetable_import_runs",
                sa.Column(
                    name,
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
            )

    constraint = _audit_constraint()
    if constraint is None or "deleted" not in str(constraint.get("sqltext") or ""):
        with op.batch_alter_table("timetable_import_audit") as batch:
            if constraint is not None:
                batch.drop_constraint(
                    "ck_timetable_audit_action", type_="check"
                )
            batch.create_check_constraint(
                "ck_timetable_audit_action",
                "action IN ('inserted', 'updated', 'unchanged', 'deleted')",
            )


def downgrade() -> None:
    constraint = _audit_constraint()
    if constraint is not None and "deleted" in str(
        constraint.get("sqltext") or ""
    ):
        with op.batch_alter_table("timetable_import_audit") as batch:
            batch.drop_constraint("ck_timetable_audit_action", type_="check")
            batch.create_check_constraint(
                "ck_timetable_audit_action",
                "action IN ('inserted', 'updated', 'unchanged')",
            )

    columns = {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(
            "timetable_import_runs"
        )
    }
    for name in ("deleted_sessions", "deleted_courses"):
        if name in columns:
            op.drop_column("timetable_import_runs", name)
