"""inventory ledger rules: tenant-safe reversal FK, partial unique index, RECLASSIFY trigger

Revision ID: 0002_ledger_rules
Revises: 0001_core
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_ledger_rules"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ------------------------------------------------------------------ #
    # 1. Add RECLASSIFY to the movement_type PostgreSQL ENUM.             #
    #    ADD VALUE IF NOT EXISTS is safe to execute on PostgreSQL.       #
    # ------------------------------------------------------------------ #
    conn.execute(sa.text(
        "ALTER TYPE movement_type ADD VALUE IF NOT EXISTS 'RECLASSIFY'"
    ))

    # ------------------------------------------------------------------ #
    # 2. Add reversed_movement_id column if not already present.         #
    # ------------------------------------------------------------------ #
    existing_cols = [c["name"] for c in inspector.get_columns("inventory_movements")]
    if "reversed_movement_id" not in existing_cols:
        op.add_column(
            "inventory_movements",
            sa.Column(
                "reversed_movement_id",
                UUID(as_uuid=True),
                nullable=True,
            ),
        )

    # ------------------------------------------------------------------ #
    # 3. Tenant-safe composite foreign key self-referencing movements:   #
    #    (organization_id, reversed_movement_id) -> (organization_id, id)#
    #    utilizing the existing unique constraint uq_movements_org_id.   #
    # ------------------------------------------------------------------ #
    existing_fks = [fk["name"] for fk in inspector.get_foreign_keys("inventory_movements")]
    if "fk_movements_reversal_source" not in existing_fks:
        op.create_foreign_key(
            "fk_movements_reversal_source",
            "inventory_movements",
            "inventory_movements",
            ["organization_id", "reversed_movement_id"],
            ["organization_id", "id"],
        )

    # ------------------------------------------------------------------ #
    # 4. Partial unique index: each movement can be reversed at most once.#
    #    Applies only when reversed_movement_id IS NOT NULL.             #
    # ------------------------------------------------------------------ #
    existing_indexes = [idx["name"] for idx in inspector.get_indexes("inventory_movements")]
    if "uq_movements_reversed_once" not in existing_indexes:
        op.create_index(
            "uq_movements_reversed_once",
            "inventory_movements",
            ["reversed_movement_id"],
            unique=True,
            postgresql_where=sa.text("reversed_movement_id IS NOT NULL"),
        )

    # ------------------------------------------------------------------ #
    # 5. Delta rules check constraint:                                   #
    #    - RECLASSIFY requires quantity_delta = 0                        #
    #    - REVERSAL can have any delta (including 0 if reversing        #
    #      reclassification)                                            #
    #    - All other movement types require quantity_delta <> 0          #
    # ------------------------------------------------------------------ #
    existing_cks = [ck["name"] for ck in inspector.get_check_constraints("inventory_movements")]
    if "ck_movement_nonzero_delta" in existing_cks:
        op.drop_constraint("ck_movement_nonzero_delta", "inventory_movements", type_="check")
    if "ck_movement_delta_rules" not in existing_cks:
        op.create_check_constraint(
            "ck_movement_delta_rules",
            "inventory_movements",
            "(movement_type::text = 'RECLASSIFY' AND quantity_delta = 0)"
            " OR (movement_type::text = 'REVERSAL')"
            " OR (movement_type::text NOT IN ('RECLASSIFY', 'REVERSAL') AND quantity_delta <> 0)",
        )

    # ------------------------------------------------------------------ #
    # 6. Reversal reference integrity check constraint:                  #
    #    - If movement_type == 'REVERSAL', reversed_movement_id NOT NULL #
    #    - If movement_type != 'REVERSAL', reversed_movement_id IS NULL  #
    #    - Cannot reference self (reversed_movement_id <> id)            #
    # ------------------------------------------------------------------ #
    if "ck_movement_reversal_reference" not in existing_cks:
        op.create_check_constraint(
            "ck_movement_reversal_reference",
            "inventory_movements",
            "(movement_type::text = 'REVERSAL' AND reversed_movement_id IS NOT NULL AND reversed_movement_id <> id)"
            " OR (movement_type::text <> 'REVERSAL' AND reversed_movement_id IS NULL)",
        )

    # ------------------------------------------------------------------ #
    # 7. Update trigger function with RECLASSIFY structural verification:#
    #    - Exactly 2 postings                                            #
    #    - Exactly 1 SELLABLE and 1 QUARANTINED                          #
    #    - One positive and one negative posting                         #
    #    - Equal absolute quantities (net sum = 0)                       #
    #    - Non-RECLASSIFY maintains SUM(postings) = quantity_delta       #
    # ------------------------------------------------------------------ #
    op.execute("""
    CREATE OR REPLACE FUNCTION stocksignal_check_movement_postings()
    RETURNS trigger AS $$
    DECLARE
      target_id        uuid;
      mv_type          text;
      expected         numeric(14,3);
      actual           numeric(14,3);
      p_count          integer;
      pos_count        integer;
      neg_count        integer;
      sell_count       integer;
      quar_count       integer;
      abs_sell         numeric(14,3);
      abs_quar         numeric(14,3);
    BEGIN
      IF TG_TABLE_NAME = 'inventory_movements' THEN
        target_id := NEW.id;
      ELSE
        IF TG_OP = 'DELETE' THEN
          target_id := OLD.movement_id;
        ELSE
          target_id := NEW.movement_id;
        END IF;
      END IF;

      SELECT quantity_delta, movement_type::text
        INTO expected, mv_type
        FROM inventory_movements
       WHERE id = target_id;
      IF NOT FOUND THEN RETURN NULL; END IF;

      IF mv_type = 'RECLASSIFY' THEN
        SELECT
          COUNT(*)                                                    AS p_count,
          COUNT(*) FILTER (WHERE quantity_delta > 0)                 AS pos_count,
          COUNT(*) FILTER (WHERE quantity_delta < 0)                 AS neg_count,
          COUNT(*) FILTER (WHERE bucket_type::text = 'SELLABLE')     AS sell_count,
          COUNT(*) FILTER (WHERE bucket_type::text = 'QUARANTINED')  AS quar_count,
          COALESCE(ABS(SUM(quantity_delta)
                   FILTER (WHERE bucket_type::text = 'SELLABLE')), 0)    AS abs_sell,
          COALESCE(ABS(SUM(quantity_delta)
                   FILTER (WHERE bucket_type::text = 'QUARANTINED')), 0) AS abs_quar
          INTO p_count, pos_count, neg_count,
               sell_count, quar_count, abs_sell, abs_quar
          FROM inventory_movement_postings
         WHERE movement_id = target_id;

        IF p_count <> 2 THEN
          RAISE EXCEPTION
            'RECLASSIFY movement % must have exactly 2 postings, found %',
            target_id, p_count;
        END IF;

        IF sell_count <> 1 THEN
          RAISE EXCEPTION
            'RECLASSIFY movement % must have exactly 1 SELLABLE posting',
            target_id;
        END IF;

        IF quar_count <> 1 THEN
          RAISE EXCEPTION
            'RECLASSIFY movement % must have exactly 1 QUARANTINED posting',
            target_id;
        END IF;

        IF pos_count <> 1 OR neg_count <> 1 THEN
          RAISE EXCEPTION
            'RECLASSIFY movement % must have one positive and one negative posting',
            target_id;
        END IF;

        IF abs_sell <> abs_quar THEN
          RAISE EXCEPTION
            'RECLASSIFY movement % postings must have equal absolute quantities (SELLABLE abs=%, QUARANTINED abs=%)',
            target_id, abs_sell, abs_quar;
        END IF;

      ELSE
        SELECT COALESCE(SUM(quantity_delta), 0)
          INTO actual
          FROM inventory_movement_postings
         WHERE movement_id = target_id;

        IF actual <> expected THEN
          RAISE EXCEPTION
            'Movement % expects posting total %, got %',
            target_id, expected, actual;
        END IF;
      END IF;

      RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------ #
    # Safe downgrade guard: check if any RECLASSIFY or REVERSAL exist.   #
    # ------------------------------------------------------------------ #
    reclassify_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM inventory_movements WHERE movement_type::text = 'RECLASSIFY'")
    ).scalar() or 0
    reversal_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM inventory_movements WHERE movement_type::text = 'REVERSAL'")
    ).scalar() or 0

    if reclassify_count > 0 or reversal_count > 0:
        raise RuntimeError(
            f"Cannot downgrade migration 0002: {reclassify_count} RECLASSIFY record(s) and "
            f"{reversal_count} REVERSAL record(s) exist in inventory_movements. "
            "Downgrade would lose inventory classification or reversal history."
        )

    # Restore 0001 trigger function (without RECLASSIFY branch)
    op.execute("""
    CREATE OR REPLACE FUNCTION stocksignal_check_movement_postings()
    RETURNS trigger AS $$
    DECLARE
      target_id uuid;
      expected  numeric(14,3);
      actual    numeric(14,3);
    BEGIN
      IF TG_TABLE_NAME = 'inventory_movements' THEN
        target_id := NEW.id;
      ELSE
        IF TG_OP = 'DELETE' THEN
          target_id := OLD.movement_id;
        ELSE
          target_id := NEW.movement_id;
        END IF;
      END IF;

      SELECT quantity_delta INTO expected
        FROM inventory_movements WHERE id = target_id;
      IF NOT FOUND THEN RETURN NULL; END IF;

      SELECT COALESCE(SUM(quantity_delta), 0) INTO actual
        FROM inventory_movement_postings WHERE movement_id = target_id;
      IF actual <> expected THEN
        RAISE EXCEPTION 'Movement % expects posting total %, got %',
          target_id, expected, actual;
      END IF;

      RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)

    # Drop constraints and index
    op.drop_constraint("ck_movement_reversal_reference", "inventory_movements", type_="check")
    op.drop_constraint("ck_movement_delta_rules", "inventory_movements", type_="check")
    op.drop_index("uq_movements_reversed_once", table_name="inventory_movements")
    op.drop_constraint("fk_movements_reversal_source", "inventory_movements", type_="foreignkey")
    op.drop_column("inventory_movements", "reversed_movement_id")

    # Restore original non-zero constraint
    op.create_check_constraint(
        "ck_movement_nonzero_delta",
        "inventory_movements",
        "quantity_delta <> 0",
    )

    # NOTE: PostgreSQL does not support removing values from an ENUM type
    # via ALTER TYPE ... DROP VALUE. The 'RECLASSIFY' value will persist in
    # movement_type. A full removal requires recreating the enum type.
