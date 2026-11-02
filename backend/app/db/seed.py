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
            print("=" * 60)
            print("Seed execution finished successfully (transaction committed).")
        except Exception as e:
            session.rollback()
            print(f"Seed operation failed: {e}", file=sys.stderr)
            raise


if __name__ == "__main__":
    run()

