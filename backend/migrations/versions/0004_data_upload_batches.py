"""add data_upload_batches table

Revision ID: 0004_data_upload_batches
Revises: 0003_action_recommendations
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID, ENUM as PG_ENUM

revision = "0004_data_upload_batches"
down_revision = "0003_action_recommendations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure ENUMs exist (idempotent — no-op if already present)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dataset_category') THEN
                CREATE TYPE dataset_category AS ENUM (
                    'SALES', 'INVENTORY_SNAPSHOT', 'GOODS_RECEIVED',
                    'BATCH_EXPIRY', 'PHYSICAL_COUNT', 'PRODUCT_CATALOG'
                );
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'upload_status') THEN
                CREATE TYPE upload_status AS ENUM (
                    'UPLOADED', 'VALIDATING', 'VALIDATION_FAILED',
                    'READY_TO_IMPORT', 'PROCESSING', 'COMPLETED',
                    'COMPLETED_WITH_ERRORS', 'FAILED'
                );
            END IF;
        END $$;
    """)

    op.create_table(
        "data_upload_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "dataset_category",
            PG_ENUM(
                "SALES", "INVENTORY_SNAPSHOT", "GOODS_RECEIVED",
                "BATCH_EXPIRY", "PHYSICAL_COUNT", "PRODUCT_CATALOG",
                name="dataset_category",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_format", sa.String(20), nullable=False, server_default="CSV"),
        sa.Column("file_hash_sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=True),
        sa.Column(
            "status",
            PG_ENUM(
                "UPLOADED", "VALIDATING", "VALIDATION_FAILED",
                "READY_TO_IMPORT", "PROCESSING", "COMPLETED",
                "COMPLETED_WITH_ERRORS", "FAILED",
                name="upload_status",
                create_type=False,
            ),
            nullable=False,
            server_default="UPLOADED",
        ),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("validation_details", JSONB(), nullable=True),
        sa.Column("uploaded_by", sa.String(100), nullable=False, server_default="OPERATOR"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_upload_batch_organization",
        ),
    )
    op.create_index(
        "ix_data_upload_batches_org",
        "data_upload_batches",
        ["organization_id"],
    )
    op.create_index(
        "ix_data_upload_batches_status",
        "data_upload_batches",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_data_upload_batches_status", table_name="data_upload_batches")
    op.drop_index("ix_data_upload_batches_org", table_name="data_upload_batches")
    op.drop_table("data_upload_batches")
    op.execute("DROP TYPE IF EXISTS dataset_category")
    op.execute("DROP TYPE IF EXISTS upload_status")
