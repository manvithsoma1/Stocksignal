"""action_recommendations outcome tracking

Revision ID: 0003_action_recommendations
Revises: 0002_ledger_rules
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID, ENUM

revision = "0003_action_recommendations"
down_revision = "0002_ledger_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Define ENUM type with create_type=False so SQLAlchemy doesn't re-create it
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recommendation_status') THEN
                CREATE TYPE recommendation_status AS ENUM (
                    'PENDING', 'APPROVED', 'REJECTED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'VERIFIED'
                );
            END IF;
        END$$;
    """))

    rec_status_enum = ENUM(
        "PENDING", "APPROVED", "REJECTED", "IN_PROGRESS", "COMPLETED", "CANCELLED", "VERIFIED",
        name="recommendation_status",
        create_type=False,
    )

    # 2. Create action_recommendations table if not existing
    existing_tables = inspector.get_table_names()
    if "action_recommendations" not in existing_tables:
        op.create_table(
            "action_recommendations",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("store_id", UUID(as_uuid=True), nullable=False),
            sa.Column("product_id", UUID(as_uuid=True), nullable=False),
            sa.Column("action_type", sa.String(50), nullable=False),
            sa.Column("priority", sa.String(20), nullable=False),
            sa.Column(
                "status",
                rec_status_enum,
                nullable=False,
                server_default="PENDING",
            ),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("detail", JSONB(), nullable=True),
            sa.Column("assigned_to", sa.String(100), nullable=True),
            sa.Column("resolution_notes", sa.Text(), nullable=True),
            sa.Column("predicted_opportunity_value", sa.Numeric(12, 3), nullable=True),
            sa.Column("estimated_benefit_value", sa.Numeric(12, 3), nullable=True),
            sa.Column("realized_benefit_value", sa.Numeric(12, 3), nullable=True),
            sa.Column("actual_outcome_notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("verified_by", sa.String(100), nullable=True),
            sa.ForeignKeyConstraint(
                ["organization_id", "store_id"],
                ["stores.organization_id", "stores.id"],
                name="fk_action_recs_store",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["organization_id", "product_id"],
                ["products.organization_id", "products.id"],
                name="fk_action_recs_product",
                ondelete="CASCADE",
            ),
        )

        op.create_index(
            "ix_action_recs_org_store_status",
            "action_recommendations",
            ["organization_id", "store_id", "status"],
        )
        op.create_index(
            "ix_action_recs_org_product",
            "action_recommendations",
            ["organization_id", "product_id"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "action_recommendations" in inspector.get_table_names():
        op.drop_table("action_recommendations")
    conn.execute(sa.text("DROP TYPE IF EXISTS recommendation_status CASCADE"))
