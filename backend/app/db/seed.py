import sys
import uuid
from decimal import Decimal
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.core import Organization, Product, Store, StoreProduct

DEMO_ORGANIZATION = {
    "name": "StockSignal Demo Retail",
    "code": "STOCKSIGNAL_DEMO",
    "status": "ACTIVE",
}

DEMO_STORES = [
    {
        "store_code": "STORE001",
        "name": "Chennai Central",
        "timezone": "Asia/Kolkata",
        "status": "ACTIVE",
    },
    {
        "store_code": "STORE002",
        "name": "Chennai South",
        "timezone": "Asia/Kolkata",
        "status": "ACTIVE",
    },
]

DEMO_PRODUCTS = [
    # Dairy (4 items - perishable)
    {
        "sku": "DAI-MILK-500ML",
        "name": "Amul Taaza Homogenised Toned Milk 500ml",
        "category": "Dairy",
        "unit_of_measure": "unit",
        "is_perishable": True,
        "status": "ACTIVE",
    },
    {
        "sku": "DAI-CURD-400G",
        "name": "Nandini Fresh Curd 400g",
        "category": "Dairy",
        "unit_of_measure": "unit",
        "is_perishable": True,
        "status": "ACTIVE",
    },
    {
        "sku": "DAI-PAN-200G",
        "name": "Milky Mist Paneer 200g",
        "category": "Dairy",
        "unit_of_measure": "unit",
        "is_perishable": True,
        "status": "ACTIVE",
    },
    {
        "sku": "DAI-BUT-100G",
        "name": "Amul Pasteurised Butter 100g",
        "category": "Dairy",
        "unit_of_measure": "unit",
        "is_perishable": True,
        "status": "ACTIVE",
    },
    # Bakery (4 items)
    {
        "sku": "BAK-WHT-400G",
        "name": "Modern 100% Whole Wheat Bread 400g",
        "category": "Bakery",
        "unit_of_measure": "unit",
        "is_perishable": True,
        "status": "ACTIVE",
    },
    {
        "sku": "BAK-SAN-400G",
        "name": "Britannia White Sandwich Bread 400g",
        "category": "Bakery",
        "unit_of_measure": "unit",
        "is_perishable": True,
        "status": "ACTIVE",
    },
    {
        "sku": "BAK-PAV-250G",
        "name": "Fresh Pav Buns 6 pcs 250g",
        "category": "Bakery",
        "unit_of_measure": "unit",
        "is_perishable": True,
        "status": "ACTIVE",
    },
    {
        "sku": "BAK-RUSK-300G",
        "name": "Britannia Toastea Premium Bake Rusk 300g",
        "category": "Bakery",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    # Beverages (4 items)
    {
        "sku": "BEV-TEA-500G",
        "name": "Tata Tea Premium 500g",
        "category": "Beverages",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "BEV-COF-100G",
        "name": "Bru Instant Coffee 100g",
        "category": "Beverages",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "BEV-JUC-1L",
        "name": "Real Fruit Power Mixed Fruit Juice 1L",
        "category": "Beverages",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "BEV-SOD-750ML",
        "name": "Thums Up Soft Drink 750ml",
        "category": "Beverages",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    # Packaged Foods (4 items)
    {
        "sku": "PKG-NOD-280G",
        "name": "Maggi 2-Minute Noodles 4-Pack 280g",
        "category": "Packaged Foods",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "PKG-RIC-5KG",
        "name": "India Gate Basmati Rice Feast Rozzana 5kg",
        "category": "Packaged Foods",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "PKG-ATT-5KG",
        "name": "Aashirvaad Shudh Chakki Atta 5kg",
        "category": "Packaged Foods",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "PKG-OIL-1L",
        "name": "Fortune Sunlite Refined Sunflower Oil 1L",
        "category": "Packaged Foods",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    # Personal Care (4 items)
    {
        "sku": "PER-SOP-125G",
        "name": "Dettol Original Bathing Soap 125g",
        "category": "Personal Care",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "PER-SHA-340ML",
        "name": "Head & Shoulders Anti-Dandruff Shampoo 340ml",
        "category": "Personal Care",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "PER-TPS-150G",
        "name": "Colgate Strong Teeth Dental Cream Toothpaste 150g",
        "category": "Personal Care",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "PER-HWS-200ML",
        "name": "Lifebuoy Total 10 Handwash Pump 200ml",
        "category": "Personal Care",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    # Household Essentials (4 items)
    {
        "sku": "HOU-DET-1KG",
        "name": "Surf Excel Easy Wash Detergent Powder 1kg",
        "category": "Household Essentials",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "HOU-DIS-500ML",
        "name": "Vim Dishwash Gel Lemon 500ml",
        "category": "Household Essentials",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "HOU-CLN-1L",
        "name": "Lizol Disinfectant Surface Floor Cleaner Citrus 1L",
        "category": "Household Essentials",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
    {
        "sku": "HOU-TRH-30S",
        "name": "Shalimar Garbage Bags Medium 30 Bags",
        "category": "Household Essentials",
        "unit_of_measure": "unit",
        "is_perishable": False,
        "status": "ACTIVE",
    },
]


def seed_data(session: Session) -> Dict[str, Dict[str, int]]:
    summary = {
        "organizations": {"created": 0, "existing": 0},
        "stores": {"created": 0, "existing": 0},
        "products": {"created": 0, "existing": 0},
        "store_products": {"created": 0, "existing": 0},
    }

    # 1. Organization (Natural key: code)
    org = session.scalar(
        select(Organization).where(Organization.code == DEMO_ORGANIZATION["code"])
    )
    if not org:
        org = Organization(
            id=uuid.uuid4(),
            name=DEMO_ORGANIZATION["name"],
            code=DEMO_ORGANIZATION["code"],
            status=DEMO_ORGANIZATION["status"],
        )
        session.add(org)
        session.flush()
        summary["organizations"]["created"] += 1
    else:
        summary["organizations"]["existing"] += 1

    # 2. Stores (Natural key: organization_id, store_code)
    stores = []
    for store_data in DEMO_STORES:
        store = session.scalar(
            select(Store).where(
                Store.organization_id == org.id,
                Store.store_code == store_data["store_code"],
            )
        )
        if not store:
            store = Store(
                id=uuid.uuid4(),
                organization_id=org.id,
                store_code=store_data["store_code"],
                name=store_data["name"],
                timezone=store_data["timezone"],
                status=store_data["status"],
            )
            session.add(store)
            session.flush()
            summary["stores"]["created"] += 1
        else:
            summary["stores"]["existing"] += 1
        stores.append(store)

    # 3. Products (Natural key: organization_id, sku)
    products = []
    for prod_data in DEMO_PRODUCTS:
        product = session.scalar(
            select(Product).where(
                Product.organization_id == org.id,
                Product.sku == prod_data["sku"],
            )
        )
        if not product:
            product = Product(
                id=uuid.uuid4(),
                organization_id=org.id,
                sku=prod_data["sku"],
                name=prod_data["name"],
                category=prod_data["category"],
                unit_of_measure=prod_data["unit_of_measure"],
                is_perishable=prod_data["is_perishable"],
                status=prod_data["status"],
            )
            session.add(product)
            session.flush()
            summary["products"]["created"] += 1
        else:
            summary["products"]["existing"] += 1
        products.append(product)

    # 4. StoreProducts mapping (Natural key: organization_id, store_id, product_id)
    for store in stores:
        for product in products:
            store_prod = session.scalar(
                select(StoreProduct).where(
                    StoreProduct.organization_id == org.id,
                    StoreProduct.store_id == store.id,
                    StoreProduct.product_id == product.id,
                )
            )
            if not store_prod:
                store_prod = StoreProduct(
                    id=uuid.uuid4(),
                    organization_id=org.id,
                    store_id=store.id,
                    product_id=product.id,
                    reorder_level=Decimal("10.000"),
                    target_stock_level=Decimal("50.000"),
                    is_active=True,
                )
                session.add(store_prod)
                summary["store_products"]["created"] += 1
            else:
                summary["store_products"]["existing"] += 1

    return summary


def seed_demo_operations(session: Session) -> dict:
    """
    Seed realistic initial inventory stock receipts (GRN), perishable batches,
    and 14 days of historical daily sales movements for the demo stores.
    
    Ensures that:
      - Every product has realistic sellable stock balances.
      - Perishable items (Dairy, Bakery) have active batches with FEFO urgency.
      - Machine learning demand forecasting can query actual sales history.
      - Fully idempotent via unique deterministic idempotency keys.
    """
    from datetime import date, datetime, timedelta, timezone
    from app.models.core import (
        Batch,
        BucketType,
        InventoryMovement,
        InventoryMovementPosting,
        MovementType,
    )

    org = session.scalar(select(Organization).where(Organization.code == DEMO_ORGANIZATION["code"]))
    if not org:
        return {"status": "skipped", "reason": "demo organization not found"}

    stores = session.scalars(select(Store).where(Store.organization_id == org.id)).all()
    products = session.scalars(select(Product).where(Product.organization_id == org.id)).all()

    today_val = date.today()
    now_utc = datetime.now(timezone.utc)
    created_movements = 0
    created_batches = 0

    # Preload existing batches and movement idempotency keys in 2 round-trips
    existing_batches = {
        (b.store_id, b.product_id, b.batch_code): b.id
        for b in session.scalars(select(Batch).where(Batch.organization_id == org.id))
    }
    existing_keys = set(
        session.scalars(
            select(InventoryMovement.idempotency_key).where(InventoryMovement.organization_id == org.id)
        )
    )

    # Category baseline initial receipt quantities (units) to comfortably support 14 days of sales
    RECEIPT_TARGETS = {
        "Dairy": Decimal("450.000"),
        "Bakery": Decimal("320.000"),
        "Beverages": Decimal("180.000"),
        "Packaged Foods": Decimal("300.000"),
        "Personal Care": Decimal("120.000"),
        "Household Essentials": Decimal("100.000"),
    }

    # SKU-specific daily burn rates (units/day) reflecting realistic retail velocity
    SKU_DAILY_SALES = {
        # Dairy
        "DAI-MILK-500ML": Decimal("28.000"),
        "DAI-CURD-400G": Decimal("18.000"),
        "DAI-PAN-200G": Decimal("10.000"),
        "DAI-BUT-100G": Decimal("4.500"),
        # Bakery
        "BAK-WHT-400G": Decimal("18.000"),
        "BAK-SAN-400G": Decimal("14.000"),
        "BAK-PAV-250G": Decimal("11.000"),
        "BAK-RUSK-300G": Decimal("5.000"),
        # Beverages
        "BEV-TEA-500G": Decimal("6.000"),
        "BEV-COF-100G": Decimal("4.000"),
        "BEV-JUC-1L": Decimal("7.000"),
        "BEV-SOD-750ML": Decimal("5.500"),
        # Packaged Foods
        "PKG-NOD-280G": Decimal("16.000"),
        "PKG-RIC-5KG": Decimal("8.000"),
        "PKG-ATT-5KG": Decimal("7.500"),
        "PKG-OIL-1L": Decimal("9.000"),
        # Personal Care
        "PER-SOP-125G": Decimal("5.000"),
        "PER-SHA-180ML": Decimal("3.000"),
        "PER-TPS-150G": Decimal("4.000"),
        "PER-SAN-50ML": Decimal("2.000"),
        # Household Essentials
        "HOU-DET-1KG": Decimal("3.500"),
        "HOU-CLN-500ML": Decimal("2.500"),
        "HOU-DSH-500ML": Decimal("3.000"),
        "HOU-TRH-30S": Decimal("1.500"),
    }

    new_batches = []
    batch_lookup: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID] = {}

    for store in stores:
        for product in products:
            if product.is_perishable:
                batch_code_1 = f"BATCH-{store.store_code[:3]}-{product.sku[:6]}-A"
                key = (store.id, product.id, batch_code_1)
                if key in existing_batches:
                    batch_lookup[(store.id, product.id)] = existing_batches[key]
                else:
                    days_remaining = 3 if product.category == "Dairy" else 4
                    b1 = Batch(
                        id=uuid.uuid4(),
                        organization_id=org.id,
                        store_id=store.id,
                        product_id=product.id,
                        batch_code=batch_code_1,
                        expiry_date=today_val + timedelta(days=days_remaining),
                        received_date=today_val - timedelta(days=14),
                    )
                    new_batches.append(b1)
                    batch_lookup[(store.id, product.id)] = b1.id
                    created_batches += 1

    if new_batches:
        session.add_all(new_batches)
        session.flush()

    new_movements = []
    new_postings = []

    for store in stores:
        for product in products:
            batch_id = batch_lookup.get((store.id, product.id))

            # 1. Initial GRN Receipt
            grn_key = f"SEED_GRN_V2_{store.store_code}_{product.sku}"
            if grn_key not in existing_keys:
                rec_qty = RECEIPT_TARGETS.get(product.category, Decimal("100.000"))
                mov_id = uuid.uuid4()
                new_movements.append(
                    InventoryMovement(
                        id=mov_id,
                        organization_id=org.id,
                        store_id=store.id,
                        product_id=product.id,
                        batch_id=batch_id,
                        movement_type=MovementType.GRN_RECEIPT,
                        quantity_delta=rec_qty,
                        occurred_at=now_utc - timedelta(days=15),
                        source_system="SEED_DATA",
                        idempotency_key=grn_key,
                    )
                )
                new_postings.append(
                    InventoryMovementPosting(
                        id=uuid.uuid4(),
                        organization_id=org.id,
                        movement_id=mov_id,
                        bucket_type=BucketType.SELLABLE,
                        quantity_delta=rec_qty,
                    )
                )
                existing_keys.add(grn_key)
                created_movements += 1

            # 2. Seed 14 Days of Sales Transactions
            base_burn = SKU_DAILY_SALES.get(product.sku, Decimal("5.000"))
            for days_ago in range(14, 0, -1):
                past_date = today_val - timedelta(days=days_ago)
                sale_key = f"SEED_SALE_V2_{store.store_code}_{product.sku}_{past_date}"
                if sale_key not in existing_keys:
                    wday = past_date.weekday()
                    mult = Decimal("1.35") if wday in (5, 6) else Decimal("0.90")
                    sale_qty = (base_burn * mult).quantize(Decimal("0.001"))
                    mov_id = uuid.uuid4()
                    new_movements.append(
                        InventoryMovement(
                            id=mov_id,
                            organization_id=org.id,
                            store_id=store.id,
                            product_id=product.id,
                            batch_id=batch_id,
                            movement_type=MovementType.SALE,
                            quantity_delta=-sale_qty,
                            occurred_at=datetime(past_date.year, past_date.month, past_date.day, 18, 30, tzinfo=timezone.utc),
                            source_system="POS_TERMINAL",
                            idempotency_key=sale_key,
                        )
                    )
                    new_postings.append(
                        InventoryMovementPosting(
                            id=uuid.uuid4(),
                            organization_id=org.id,
                            movement_id=mov_id,
                            bucket_type=BucketType.SELLABLE,
                            quantity_delta=-sale_qty,
                        )
                    )
                    existing_keys.add(sale_key)
                    created_movements += 1

    if new_movements:
        session.add_all(new_movements)
        session.flush()
        session.add_all(new_postings)

    return {
        "status": "success",
        "created_movements": created_movements,
        "created_batches": created_batches,
    }


def run() -> None:
    with SessionLocal() as session:
        try:
            summary = seed_data(session)
            session.commit()
            print("=" * 60)
            print("StockSignal Demo Seed Data Summary")
            print("=" * 60)
            print(f"Organizations:   Created: {summary['organizations']['created']:<4} | Existing: {summary['organizations']['existing']}")
            print(f"Stores:          Created: {summary['stores']['created']:<4} | Existing: {summary['stores']['existing']}")
            print(f"Products:        Created: {summary['products']['created']:<4} | Existing: {summary['products']['existing']}")
            print(f"Store Products:  Created: {summary['store_products']['created']:<4} | Existing: {summary['store_products']['existing']}")
            
            # Seed realistic operations (inventory + sales)
            op_summary = seed_demo_operations(session)
            session.commit()
            print(f"Movements:       Created: {op_summary.get('created_movements', 0):<4}")
            print(f"Batches:         Created: {op_summary.get('created_batches', 0):<4}")
            print("=" * 60)
            print("Seed execution finished successfully (transaction committed).")
        except Exception as e:
            session.rollback()
            print(f"Seed operation failed: {e}", file=sys.stderr)
            raise


if __name__ == "__main__":
    run()

