"""
Unit and integration tests for Phase 7 (Perishable Risk Module) and Phase 8 (Recommendation Engine).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.core import BucketType
from app.services.perishable import (
    BatchRiskAssessment,
    ProductPerishableReport,
    RiskTier,
    _days_remaining,
    _tier_from_days,
    rank_batches,
)
from app.services.reconciliation import (
    AnomalySeverity,
    DiscrepancyClass,
    ProductReconciliationReport,
    SuggestedAction,
)
from app.services.recommendations import (
    ActionRecommendation,
    ActionType,
    Priority,
    filter_recommendations,
    generate_recommendations,
)


class TestPerishablePureFunctions:
    def test_days_remaining(self):
        today = date(2026, 11, 9)
        assert _days_remaining(date(2026, 11, 19), today) == 10
        assert _days_remaining(date(2026, 11, 9), today) == 0
        assert _days_remaining(date(2026, 11, 5), today) == -4
        assert _days_remaining(None, today) is None

    def test_tier_from_days(self):
        assert _tier_from_days(None) == RiskTier.SAFE
        assert _tier_from_days(-1) == RiskTier.EXPIRED
        assert _tier_from_days(1) == RiskTier.CRITICAL
        assert _tier_from_days(3) == RiskTier.CRITICAL
        assert _tier_from_days(5) == RiskTier.URGENT
        assert _tier_from_days(7) == RiskTier.URGENT
        assert _tier_from_days(10) == RiskTier.WATCH
        assert _tier_from_days(14) == RiskTier.WATCH
        assert _tier_from_days(20) == RiskTier.SAFE

    def test_rank_batches(self):
        b1 = BatchRiskAssessment(
            batch_id=uuid.uuid4(),
            batch_code="B1",
            org_id=uuid.uuid4(),
            store_id=uuid.uuid4(),
            product_id=uuid.uuid4(),
            sku="SKU1",
            expiry_date=date(2026, 11, 20),
            days_remaining=11,
            current_qty=Decimal("10.000"),
            avg_daily_demand=Decimal("1.000"),
            expected_demand_before_expiry=Decimal("11.000"),
            estimated_unsold_qty=Decimal("0.000"),
            unit_cost=Decimal("100.000"),
            value_at_risk=Decimal("0.000"),
            risk_tier=RiskTier.WATCH,
        )
        b2 = BatchRiskAssessment(
            batch_id=uuid.uuid4(),
            batch_code="B2",
            org_id=uuid.uuid4(),
            store_id=uuid.uuid4(),
            product_id=uuid.uuid4(),
            sku="SKU1",
            expiry_date=date(2026, 11, 11),
            days_remaining=2,
            current_qty=Decimal("20.000"),
            avg_daily_demand=Decimal("1.000"),
            expected_demand_before_expiry=Decimal("2.000"),
            estimated_unsold_qty=Decimal("18.000"),
            unit_cost=Decimal("100.000"),
            value_at_risk=Decimal("1800.000"),
            risk_tier=RiskTier.CRITICAL,
        )
        b3 = BatchRiskAssessment(
            batch_id=uuid.uuid4(),
            batch_code="B3",
            org_id=uuid.uuid4(),
            store_id=uuid.uuid4(),
            product_id=uuid.uuid4(),
            sku="SKU1",
            expiry_date=None,
            days_remaining=None,
            current_qty=Decimal("5.000"),
            avg_daily_demand=Decimal("1.000"),
            expected_demand_before_expiry=Decimal("0.000"),
            estimated_unsold_qty=Decimal("5.000"),
            unit_cost=Decimal("100.000"),
            value_at_risk=Decimal("500.000"),
            risk_tier=RiskTier.SAFE,
        )
        ranked = rank_batches([b1, b3, b2])
        assert ranked[0].batch_code == "B2"
        assert ranked[0].fefo_rank == 1
        assert ranked[1].batch_code == "B1"
        assert ranked[1].fefo_rank == 2
        assert ranked[2].batch_code == "B3"
        assert ranked[2].fefo_rank == 3

    def test_assess_batch_confidence_and_assumptions(self):
        """Verify perishable assessment enforces estimated transparency and does not claim unsupported POS batch attribution."""
        from unittest.mock import MagicMock
        from app.models.core import Batch
        from app.services.perishable import assess_batch

        mock_db = MagicMock()
        # Mock batch sellable quantity
        mock_db.execute.return_value.scalar.return_value = Decimal("25.000")

        batch = Batch(
            id=uuid.uuid4(),
            batch_code="BATCH-FEFO-01",
            organization_id=uuid.uuid4(),
            store_id=uuid.uuid4(),
            product_id=uuid.uuid4(),
            expiry_date=date(2026, 11, 14),
        )

        today = date(2026, 11, 9)
        # 5 days remaining, demand = 2.000/day -> expected 10.000 units, unsold = 15.000 units
        assessment = assess_batch(
            db=mock_db,
            batch=batch,
            avg_daily_demand=Decimal("2.000"),
            unit_cost=Decimal("50.000"),
            today=today,
        )

        assert assessment.days_remaining == 5
        assert assessment.risk_tier == RiskTier.URGENT
        assert assessment.expected_demand_before_expiry == Decimal("10.000")
        assert assessment.estimated_unsold_qty == Decimal("15.000")
        assert assessment.value_at_risk == Decimal("750.000")
        assert assessment.confidence_level == "HIGH_CONFIDENCE"
        assert "SKU level" in assessment.assumptions
        assert "FEFO shelf depletion" in assessment.assumptions
        assert "barcode batch scanning at POS" in assessment.assumptions

    def test_assess_batch_low_data_confidence(self):
        """When demand is zero or unknown, assessment should flag LOW_DATA_CONFIDENCE."""
        from unittest.mock import MagicMock
        from app.models.core import Batch
        from app.services.perishable import assess_batch

        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = Decimal("10.000")

        batch = Batch(
            id=uuid.uuid4(),
            batch_code="BATCH-NODATA",
            organization_id=uuid.uuid4(),
            store_id=uuid.uuid4(),
            product_id=uuid.uuid4(),
            expiry_date=date(2026, 11, 12),
        )

        today = date(2026, 11, 9)
        assessment = assess_batch(
            db=mock_db,
            batch=batch,
            avg_daily_demand=Decimal("0.000"),
            unit_cost=Decimal("10.000"),
            today=today,
        )

        assert assessment.confidence_level == "LOW_DATA_CONFIDENCE"
        assert assessment.estimated_unsold_qty == Decimal("10.000")



class TestRecommendationsPureFunctions:
    def test_generate_from_reconciliation_only(self):
        org_id = uuid.uuid4()
        store_id = uuid.uuid4()
        product_id = uuid.uuid4()
        recon_report = ProductReconciliationReport(
            org_id=org_id,
            store_id=store_id,
            product_id=product_id,
            sku="SKU100",
            store_code="STORE001",
            reconciled_at=datetime.now(timezone.utc),
            buckets=[],
            overall_severity=AnomalySeverity.HIGH,
            overall_classification=DiscrepancyClass.SHRINK,
            suggested_action=SuggestedAction.PHYSICAL_COUNT,
            total_ledger_physical=Decimal("100.000"),
            total_physical_count=Decimal("80.000"),
            total_gap=Decimal("-20.000"),
        )
        recs = generate_recommendations(reconciliation_reports=[recon_report])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.action_type == ActionType.PHYSICAL_COUNT
        assert rec.priority == Priority.HIGH
        assert "SHRINK" in rec.reason

    def test_generate_from_stock_levels(self):
        org_id = uuid.uuid4()
        store_id = uuid.uuid4()
        product_id = uuid.uuid4()
        stock_levels = [
            {
                "org_id": org_id,
                "store_id": store_id,
                "product_id": product_id,
                "sku": "SKU_LOW",
                "store_code": "STORE001",
                "current_sellable": Decimal("2.000"),
                "reorder_level": Decimal("10.000"),
                "target_stock": Decimal("50.000"),
            }
        ]
        recs = generate_recommendations(stock_levels=stock_levels)
        assert len(recs) == 1
        assert recs[0].action_type == ActionType.REPLENISH
        assert recs[0].priority == Priority.HIGH

    def test_filter_recommendations(self):
        rec1 = ActionRecommendation(priority=Priority.CRITICAL)
        rec2 = ActionRecommendation(priority=Priority.MEDIUM)
        rec3 = ActionRecommendation(priority=Priority.LOW)
        filtered = filter_recommendations([rec1, rec2, rec3], min_priority=Priority.HIGH)
        assert len(filtered) == 1
        assert filtered[0].priority == Priority.CRITICAL

