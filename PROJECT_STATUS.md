# StockSignal — Project Status & Implementation Audit

## Executive Summary
StockSignal is an AI-assisted inventory integrity and decision-support platform designed for organized supermarket chains in India. It addresses structural inventory discrepancies, shrink, phantom inventory, expiry waste, and supplier mis-shipments using an immutable double-entry style inventory ledger, statistical reconciliation, perishable risk tracking, and an actionable decision-support engine.

This document tracks implementation status, architectural components, test coverage, and delivery milestones.

---

## 1. Master Architecture & Delivery Matrix

| Phase | Component / Capability | Status | Tests | Notes / Blockers |
|---|---|---|---|---|
| **Phase 0** | **Audit & Preparation** | **Completed** | N/A | Starter code verified; Git repo initialized; `.gitignore` configured; `PROJECT_STATUS.md` established. |
| **Phase 1** | **Database & Docker Foundation** | **Completed** | 17/17 passed | Docker Compose (`stocksignal-api`, `stocksignal-db`), PG 16 healthy. Alembic `0001_core` and `0002_inventory_ledger_rules` applied. 15 migration integration tests passing on isolated DB. |
| **Phase 2** | **Inventory Ledger Service & API** | **Completed** | 10/10 passed | Immutable ledger movements, reversals, reclassifications, store-to-store transfers, bucket balances, FastAPI router registered at `/api/v1/inventory`. |
| **Phase 3** | **Deterministic Evaluation Scenarios** | **Planned** | Planned | 5 core synthetic test scenarios: correct stock, unrecorded damage, duplicate scan, receipt discrepancy, return misclassification. |
| **Phase 4** | **Synthetic Data Generator** | **Planned** | Planned | Realistic generator with operational vs. ground-truth separation (3-5 stores, 300+ SKUs, batch expiry, injected discrepancies). |
| **Phase 5** | **Public Data Pipeline** | **Planned** | Planned | M5 Walmart forecasting benchmark ingestion & Favorita grocery adapter, stored in Parquet with chronological validation. |
| **Phase 6** | **Inventory Discrepancy & Reconciliation Engine** | **Planned** | Planned | Ledger vs. physical count reconciliation, anomaly detection heuristics, discrepancy classification, physical count workflow. |
| **Phase 7** | **Perishable Inventory Risk Module** | **Planned** | Planned | Batch expiry tracking, shelf-life risk calculation, FIFO/FEFO allocation comparison, markdown triggers. |
| **Phase 8** | **Action Recommendation Engine** | **Planned** | Planned | Rules-first decision engine: `PHYSICAL_COUNT`, `REPLENISH`, `MARKDOWN_REVIEW`, `TRANSFER_REVIEW`, `MONITOR`, `NO_ACTION`. |
| **Phase 9** | **Outcome Tracking & Audit Trail** | **Planned** | Planned | Action state machine (`PENDING` -> `APPROVED` -> `IN_PROGRESS` -> `COMPLETED` -> `VERIFIED`), feedback capture, ledger links. |
| **Phase 10** | **Dashboard / User Interface** | **Planned** | Planned | Clean, production-ready frontend for supermarket managers and inventory controllers. |
| **Phase 11** | **End-to-End Verification & QA** | **Planned** | Planned | Full test suite across ledger, data pipelines, reconciliation, decision engine, and API. |
| **Phase 12** | **GitHub Delivery & Packaging** | **Planned** | Planned | Clean commits, verified remote push to `https://github.com/manvithsoma1/Stocksignal.git`, demo instructions. |

---

## 2. Component Audits & Current State

### A. Core Database Models & Migrations
- **Tables (Phase 1 Baseline)**:
  - `organizations`: Multi-tenant root.
  - `stores`: Retail locations.
  - `products`: SKU master with barcodes, categories, shelf-life.
  - `store_products`: Store-level assortment, reorder points, min/max thresholds.
  - `batches`: Perishable batch tracking with manufacturing and expiry dates.
  - `inventory_movements`: Immutable ledger movement headers (14 types: `GRN_RECEIPT`, `SALE`, `CUSTOMER_RETURN`, `SUPPLIER_RETURN`, `DAMAGE_WRITE_OFF`, `EXPIRY_WRITE_OFF`, `INTERNAL_CONSUMPTION`, `REVERSAL`, `AUDIT_ADJUSTMENT`, `RECLASSIFY`, `TRANSFER_OUT`, `TRANSFER_IN`, `STOCK_IN`, `STOCK_OUT`).
  - `inventory_movement_postings`: Double-entry inventory lines with stock buckets (`SELLABLE`, `QUARANTINED`, `DAMAGED`, `EXPIRED`, `IN_TRANSIT`, `COMMITTED`, `RESERVED`).
- **Deferred Database Constraints**:
  - `stocksignal_check_movement_postings()`: PL/pgSQL deferred constraint trigger validating that at transaction commit, movement quantity matches posting sum, and `RECLASSIFY` movements have exactly 2 postings (`SELLABLE` and `QUARANTINED`) of opposite signs summing to zero.
  - `fk_movements_reversal_source`: Tenant-safe composite foreign key `(organization_id, reversed_movement_id) -> (organization_id, id)`.
  - `uq_movements_reversed_once`: Partial unique index ensuring a movement can only be reversed once.
  - `ck_movement_delta_rules` & `ck_movement_reversal_reference`: Structural checks ensuring delta rules and self-reference/reversal typing invariants.

### B. Seed Data
- Idempotent seed script (`backend/app/db/seed.py`) tested with 0 duplicates:
  - 1 Organization: `STOCKSIGNAL_DEMO`
  - 2 Stores: `STORE001` (Indiranagar), `STORE002` (Koramangala)
  - 24 Supermarket SKUs spanning Dairy, Staples, Beverages, Snacks, Produce, Personal Care.
  - 48 Store-Product mapping rows.

---

## 3. Technology Stack & Operational Guardrails
- **Backend**: FastAPI, SQLAlchemy 2.0 (mapped column syntax), Alembic, Pydantic v2.
- **Database**: PostgreSQL 16 with UUID primary keys, Decimal numeric precision (no floating-point rounding errors for money or inventory quantities), deferred constraint triggers.
- **Architecture**: Modular Monolith. Strictly zero unneeded distributed infrastructure (no Kafka, no microservice orchestration).
- **Testing Safety**: Integration tests execute strictly against isolated `TEST_DATABASE_URL` (`stocksignal_test`), preserving development and production ledger integrity.

