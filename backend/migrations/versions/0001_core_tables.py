from alembic import op
from app.db.base import Base
from app import models  # noqa: F401

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    Base.metadata.create_all(bind=op.get_bind())
    op.execute("""
    CREATE OR REPLACE FUNCTION stocksignal_check_movement_postings()
    RETURNS trigger AS $$
    DECLARE
      target_id uuid;
      expected numeric(14,3);
      actual numeric(14,3);
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
      SELECT quantity_delta INTO expected FROM inventory_movements WHERE id = target_id;
      IF NOT FOUND THEN RETURN NULL; END IF;
      SELECT COALESCE(SUM(quantity_delta), 0) INTO actual
        FROM inventory_movement_postings WHERE movement_id = target_id;
      IF actual <> expected THEN
        RAISE EXCEPTION 'Movement % expects posting total %, got %', target_id, expected, actual;
      END IF;
      RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE CONSTRAINT TRIGGER trg_check_postings_movement
    AFTER INSERT OR UPDATE ON inventory_movements
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION stocksignal_check_movement_postings();
    """)
    op.execute("""
    CREATE CONSTRAINT TRIGGER trg_check_postings_rows
    AFTER INSERT OR UPDATE OR DELETE ON inventory_movement_postings
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION stocksignal_check_movement_postings();
    """)

def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_check_postings_rows ON inventory_movement_postings")
    op.execute("DROP TRIGGER IF EXISTS trg_check_postings_movement ON inventory_movements")
    op.execute("DROP FUNCTION IF EXISTS stocksignal_check_movement_postings()")
    Base.metadata.drop_all(bind=op.get_bind())
