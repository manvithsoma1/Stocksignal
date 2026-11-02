import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey,
    ForeignKeyConstraint, Index, Numeric, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class MovementType(str, enum.Enum):
    SALE = "SALE"
    RETURN_SELLABLE = "RETURN_SELLABLE"
    RETURN_UNSELLABLE = "RETURN_UNSELLABLE"
    GRN_RECEIPT = "GRN_RECEIPT"
    DAMAGE = "DAMAGE"
    THEFT_OR_LOSS = "THEFT_OR_LOSS"
    ADJUSTMENT_CORRECTION = "ADJUSTMENT_CORRECTION"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    EXPIRY_WRITEOFF = "EXPIRY_WRITEOFF"
    PHYSICAL_COUNT_ADJUSTMENT = "PHYSICAL_COUNT_ADJUSTMENT"
    REVERSAL = "REVERSAL"
    RETURN_TO_SUPPLIER = "RETURN_TO_SUPPLIER"
    RECLASSIFY = "RECLASSIFY"


class BucketType(str, enum.Enum):
    SELLABLE = "SELLABLE"
    QUARANTINED = "QUARANTINED"


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("code", name="uq_organizations_code"),)


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    store_code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_stores_org_id"),
        UniqueConstraint("organization_id", "store_code", name="uq_stores_org_code"),
    )


class Product(Base):
    __tablename__ = "products"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    unit_of_measure: Mapped[str] = mapped_column(String(30), nullable=False, default="unit")
    is_perishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_products_org_id"),
        UniqueConstraint("organization_id", "sku", name="uq_products_org_sku"),
    )


class StoreProduct(Base):
    __tablename__ = "store_products"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reorder_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    target_stock_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "store_id"], ["stores.organization_id", "stores.id"], name="fk_store_products_store"),
        ForeignKeyConstraint(["organization_id", "product_id"], ["products.organization_id", "products.id"], name="fk_store_products_product"),
        UniqueConstraint("organization_id", "store_id", "product_id", name="uq_store_products_store_product"),
    )


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    batch_code: Mapped[str | None] = mapped_column(String(100))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    received_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "store_id"], ["stores.organization_id", "stores.id"], name="fk_batches_store"),
        ForeignKeyConstraint(["organization_id", "product_id"], ["products.organization_id", "products.id"], name="fk_batches_product"),
        UniqueConstraint("organization_id", "store_id", "product_id", "id", name="uq_batches_org_store_product_id"),
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    store_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    movement_type: Mapped[MovementType] = mapped_column(
        Enum(MovementType, name="movement_type", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    external_document_id: Mapped[str | None] = mapped_column(String(160))
    source_line_number: Mapped[str | None] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    reversed_movement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    postings: Mapped[list["InventoryMovementPosting"]] = relationship(
        "InventoryMovementPosting",
        primaryjoin="InventoryMovement.id == InventoryMovementPosting.movement_id",
        foreign_keys="[InventoryMovementPosting.movement_id]",
        lazy="selectin",
        back_populates="movement",
    )

    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "store_id"], ["stores.organization_id", "stores.id"], name="fk_movements_store"),
        ForeignKeyConstraint(["organization_id", "product_id"], ["products.organization_id", "products.id"], name="fk_movements_product"),
        ForeignKeyConstraint(["organization_id", "store_id", "product_id", "batch_id"], ["batches.organization_id", "batches.store_id", "batches.product_id", "batches.id"], name="fk_movements_batch"),
        UniqueConstraint("organization_id", "id", name="uq_movements_org_id"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_movements_idempotency"),
        # Composite self-referencing foreign key guaranteeing tenant safety:
        ForeignKeyConstraint(
            ["organization_id", "reversed_movement_id"],
            ["inventory_movements.organization_id", "inventory_movements.id"],
            name="fk_movements_reversal_source",
        ),
        # Partial unique index ensuring a movement is reversed at most once:
        Index(
            "uq_movements_reversed_once",
            "reversed_movement_id",
            unique=True,
            postgresql_where=text("reversed_movement_id IS NOT NULL"),
        ),
        # Delta rules:
        CheckConstraint(
            "(movement_type::text = 'RECLASSIFY' AND quantity_delta = 0)"
            " OR (movement_type::text = 'REVERSAL')"
            " OR (movement_type::text NOT IN ('RECLASSIFY', 'REVERSAL') AND quantity_delta <> 0)",
            name="ck_movement_delta_rules",
        ),
        # Reversal reference integrity:
        CheckConstraint(
            "(movement_type::text = 'REVERSAL' AND reversed_movement_id IS NOT NULL AND reversed_movement_id <> id)"
            " OR (movement_type::text <> 'REVERSAL' AND reversed_movement_id IS NULL)",
            name="ck_movement_reversal_reference",
        ),
    )


class InventoryMovementPosting(Base):
    __tablename__ = "inventory_movement_postings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    movement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    bucket_type: Mapped[BucketType] = mapped_column(
        Enum(BucketType, name="bucket_type", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    movement: Mapped["InventoryMovement"] = relationship(
        "InventoryMovement",
        primaryjoin="InventoryMovementPosting.movement_id == InventoryMovement.id",
        foreign_keys="[InventoryMovementPosting.movement_id]",
        back_populates="postings",
    )

    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "movement_id"], ["inventory_movements.organization_id", "inventory_movements.id"], name="fk_postings_movement"),
        CheckConstraint("quantity_delta <> 0", name="ck_posting_nonzero_delta"),
        UniqueConstraint("movement_id", "bucket_type", name="uq_posting_movement_bucket"),
    )
