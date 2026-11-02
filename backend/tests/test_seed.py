from decimal import Decimal
import pytest
from sqlalchemy import select, func

from app.db.session import SessionLocal
from app.db.seed import seed_data, DEMO_ORGANIZATION, DEMO_STORES, DEMO_PRODUCTS
from app.models.core import Organization, Store, Product, StoreProduct


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_seed_creates_expected_entities_and_is_idempotent(db_session):
    # 1. Run seed
    first_summary = seed_data(db_session)
    db_session.commit()

    # 2. Verify Organization
    orgs = db_session.scalars(
        select(Organization).where(Organization.code == DEMO_ORGANIZATION["code"])
    ).all()
    assert len(orgs) == 1
    demo_org = orgs[0]
    assert demo_org.name == DEMO_ORGANIZATION["name"]
    assert demo_org.status == "ACTIVE"

    # 3. Verify Stores
    stores = db_session.scalars(
        select(Store).where(Store.organization_id == demo_org.id)
    ).all()
    assert len(stores) == 2
    store_codes = {s.store_code for s in stores}
    assert store_codes == {"STORE001", "STORE002"}
    for store in stores:
        assert store.timezone == "Asia/Kolkata"
        assert store.status == "ACTIVE"

    # 4. Verify Products
    products = db_session.scalars(
        select(Product).where(Product.organization_id == demo_org.id)
    ).all()
    assert len(products) == 24
    expected_skus = {p["sku"] for p in DEMO_PRODUCTS}
    actual_skus = {p.sku for p in products}
    assert actual_skus == expected_skus

    # Verify categories distribution (6 categories, 4 each)
    categories = {}
    for p in products:
        assert p.organization_id == demo_org.id
        assert p.status == "ACTIVE"
        assert p.unit_of_measure == "unit"
        categories[p.category] = categories.get(p.category, 0) + 1

    expected_categories = {
        "Dairy": 4,
        "Bakery": 4,
        "Beverages": 4,
        "Packaged Foods": 4,
        "Personal Care": 4,
        "Household Essentials": 4,
    }
    assert categories == expected_categories

    # 5. Verify StoreProducts Mappings
    mappings = db_session.scalars(
        select(StoreProduct).where(StoreProduct.organization_id == demo_org.id)
    ).all()
    assert len(mappings) == 48

    store_ids = {s.id for s in stores}
    product_ids = {p.id for p in products}

    for mapping in mappings:
        assert mapping.organization_id == demo_org.id
        assert mapping.store_id in store_ids
        assert mapping.product_id in product_ids
        assert mapping.is_active is True
        assert mapping.reorder_level == Decimal("10.000")
        assert mapping.target_stock_level == Decimal("50.000")

    # Verify each store has exactly 24 products mapped
    for store_id in store_ids:
        store_mappings = [m for m in mappings if m.store_id == store_id]
        assert len(store_mappings) == 24
        mapped_product_ids = {m.product_id for m in store_mappings}
        assert mapped_product_ids == product_ids

    # 6. Verify Idempotency: Running seed again should create 0 new records
    second_summary = seed_data(db_session)
    db_session.commit()

    assert second_summary["organizations"]["created"] == 0
    assert second_summary["organizations"]["existing"] == 1
    assert second_summary["stores"]["created"] == 0
    assert second_summary["stores"]["existing"] == 2
    assert second_summary["products"]["created"] == 0
    assert second_summary["products"]["existing"] == 24
    assert second_summary["store_products"]["created"] == 0
    assert second_summary["store_products"]["existing"] == 48

    # Verify counts in DB remain unchanged
    total_orgs = db_session.scalar(
        select(func.count(Organization.id)).where(Organization.code == DEMO_ORGANIZATION["code"])
    )
    total_stores = db_session.scalar(
        select(func.count(Store.id)).where(Store.organization_id == demo_org.id)
    )
    total_products = db_session.scalar(
        select(func.count(Product.id)).where(Product.organization_id == demo_org.id)
    )
    total_mappings = db_session.scalar(
        select(func.count(StoreProduct.id)).where(StoreProduct.organization_id == demo_org.id)
    )

    assert total_orgs == 1
    assert total_stores == 2
    assert total_products == 24
    assert total_mappings == 48

