"""
SQLAlchemy ORM models — single shared data model for the whole application.

Design rules followed throughout:
- Every business table carries: upload_id, data_period, source_file,
  created_at, created_by, updated_at  (per spec section 8).
- SQLite-compatible types only (String/Integer/Float/Date/DateTime/Boolean)
  so the schema ports cleanly to PostgreSQL later (see database/init_db.py
  docstring for the upgrade path).
- No business logic here — this file is pure schema. Calculations live in
  services/forecast_service.py and services/supply_service.py.
"""
from datetime import datetime, date
from sqlalchemy import (
    String, Integer, Float, Boolean, Date, DateTime, ForeignKey, Text,
    UniqueConstraint, Index
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Mixin with the common audit columns required by spec section 8
# ---------------------------------------------------------------------------
class AuditMixin:
    upload_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    data_period: Mapped[str] = mapped_column(String(16), nullable=True, index=True)  # e.g. '2024-05'
    source_file: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# 1. USERS & AUTH
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="Viewer")  # Admin / Planner / Viewer
    email: Mapped[str] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login: Mapped[datetime] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# 2. MASTER DATA
# ---------------------------------------------------------------------------
class Dealer(Base, AuditMixin):
    __tablename__ = "dealers"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dealer_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    dealer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dealer_group: Mapped[str] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=True)  # KA / MT / GT
    region: Mapped[str] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)


class Product(Base, AuditMixin):
    __tablename__ = "products"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    sku_name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    product_group: Mapped[str] = mapped_column(String(64), nullable=True)
    product_type: Mapped[str] = mapped_column(String(64), nullable=True)
    model_compatibility: Mapped[str] = mapped_column(String(128), nullable=True)
    color: Mapped[str] = mapped_column(String(32), nullable=True)
    launch_date: Mapped[date] = mapped_column(Date, nullable=True)
    eol_date: Mapped[date] = mapped_column(Date, nullable=True)
    product_status: Mapped[str] = mapped_column(String(16), default="Active", index=True)
    # NPI / Active / Seasonal / Slow Moving / Phase-out / EOL / Discontinued
    abc_classification: Mapped[str] = mapped_column(String(4), nullable=True)  # A/B/C
    unit_cost: Mapped[float] = mapped_column(Float, default=0)
    dealer_price: Mapped[float] = mapped_column(Float, default=0)
    srp: Mapped[float] = mapped_column(Float, default=0)
    lead_time: Mapped[int] = mapped_column(Integer, default=30)  # days
    moq: Mapped[int] = mapped_column(Integer, default=1)
    order_multiple: Mapped[int] = mapped_column(Integer, default=1)
    safety_stock_days: Mapped[int] = mapped_column(Integer, default=15)
    target_stock_days: Mapped[int] = mapped_column(Integer, default=45)
    default_growth_rate: Mapped[float] = mapped_column(Float, default=0.0)
    seasonality_group: Mapped[str] = mapped_column(String(32), nullable=True)
    npi_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    eol_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    replacement_sku: Mapped[str] = mapped_column(String(32), nullable=True)
    main_dealer: Mapped[str] = mapped_column(String(64), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# 3. RAW TRANSACTIONAL DATA
# ---------------------------------------------------------------------------
class RawSI(Base, AuditMixin):
    __tablename__ = "raw_si"
    __table_args__ = (
        UniqueConstraint("txn_date", "dealer", "sku_code", "invoice_number", name="uq_si_key"),
        Index("ix_si_period_dealer_sku", "data_period", "dealer", "sku_code"),
    )

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    dealer: Mapped[str] = mapped_column(String(64), nullable=False)
    dealer_group: Mapped[str] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=True)
    region: Mapped[str] = mapped_column(String(64), nullable=True)
    brand: Mapped[str] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=True)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku_name: Mapped[str] = mapped_column(String(255), nullable=True)
    si_quantity: Mapped[float] = mapped_column(Float, default=0)
    si_revenue: Mapped[float] = mapped_column(Float, default=0)
    unit_price: Mapped[float] = mapped_column(Float, default=0)
    promotion: Mapped[str] = mapped_column(String(128), nullable=True)
    campaign: Mapped[str] = mapped_column(String(128), nullable=True)
    po_number: Mapped[str] = mapped_column(String(64), nullable=True)
    invoice_number: Mapped[str] = mapped_column(String(64), nullable=True)


class RawSO(Base, AuditMixin):
    __tablename__ = "raw_so"
    __table_args__ = (
        UniqueConstraint("txn_date", "dealer", "store", "sku_code", name="uq_so_key"),
        Index("ix_so_period_dealer_sku", "data_period", "dealer", "sku_code"),
    )

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    dealer: Mapped[str] = mapped_column(String(64), nullable=False)
    store: Mapped[str] = mapped_column(String(64), nullable=True, default="")
    region: Mapped[str] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=True)
    brand: Mapped[str] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=True)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku_name: Mapped[str] = mapped_column(String(255), nullable=True)
    so_quantity: Mapped[float] = mapped_column(Float, default=0)
    so_revenue: Mapped[float] = mapped_column(Float, default=0)
    promotion: Mapped[str] = mapped_column(String(128), nullable=True)
    campaign: Mapped[str] = mapped_column(String(128), nullable=True)
    npi_period: Mapped[str] = mapped_column(String(32), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=True)


class InventorySnapshot(Base, AuditMixin):
    __tablename__ = "inventory_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_date", "dealer", "warehouse", "sku_code", name="uq_inv_key"),
        Index("ix_inv_period_dealer_sku", "data_period", "dealer", "sku_code"),
    )

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    dealer: Mapped[str] = mapped_column(String(64), nullable=False)
    warehouse: Mapped[str] = mapped_column(String(64), nullable=True, default="")  # Store hoặc Warehouse
    brand: Mapped[str] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=True)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku_name: Mapped[str] = mapped_column(String(255), nullable=True)
    available_inventory: Mapped[float] = mapped_column(Float, default=0)
    reserved_inventory: Mapped[float] = mapped_column(Float, default=0)
    damaged_inventory: Mapped[float] = mapped_column(Float, default=0)
    sellable_inventory: Mapped[float] = mapped_column(Float, default=0)  # computed on load
    inbound_quantity: Mapped[float] = mapped_column(Float, default=0)
    inbound_eta: Mapped[date] = mapped_column(Date, nullable=True)
    backorder: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str] = mapped_column(Text, nullable=True)


class PurchaseOrder(Base, AuditMixin):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("po_number", "sku_code", name="uq_po_key"),
    )

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_date: Mapped[date] = mapped_column(Date, nullable=False)
    po_number: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier: Mapped[str] = mapped_column(String(128), nullable=True)
    brand: Mapped[str] = mapped_column(String(64), nullable=True)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku_name: Mapped[str] = mapped_column(String(255), nullable=True)
    po_quantity: Mapped[float] = mapped_column(Float, default=0)
    received_quantity: Mapped[float] = mapped_column(Float, default=0)
    outstanding_quantity: Mapped[float] = mapped_column(Float, default=0)  # computed on load
    expected_arrival_date: Mapped[date] = mapped_column(Date, nullable=True)
    actual_arrival_date: Mapped[date] = mapped_column(Date, nullable=True)
    po_status: Mapped[str] = mapped_column(String(32), nullable=True)
    unit_cost: Mapped[float] = mapped_column(Float, default=0)
    total_po_value: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# 4. UPLOAD HISTORY
# ---------------------------------------------------------------------------
class UploadHistory(Base):
    __tablename__ = "upload_history"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_by: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    data_type: Mapped[str] = mapped_column(String(32))  # MASTER_DATA / RAW_SI / RAW_SO / RAW_INVENTORY / RAW_PO
    data_period: Mapped[str] = mapped_column(String(16))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    warning_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="Pending")
    # Pending / Validated / Saved / Failed / Rejected
    update_mode: Mapped[str] = mapped_column(String(32), nullable=True)
    # Preview only / Append / Replace selected period / Update existing records
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    error_report_path: Mapped[str] = mapped_column(String(255), nullable=True)


# ---------------------------------------------------------------------------
# 5. FORECAST
# ---------------------------------------------------------------------------
class ForecastVersion(Base):
    __tablename__ = "forecast_versions"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    data_period: Mapped[str] = mapped_column(String(16), index=True)
    data_cutoff_date: Mapped[date] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="Draft")  # Draft/Revised/Approved/Locked
    total_forecast_so: Mapped[float] = mapped_column(Float, default=0)
    total_plan_so: Mapped[float] = mapped_column(Float, default=0)
    total_plan_si: Mapped[float] = mapped_column(Float, default=0)
    total_suggested_po: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ForecastDetail(Base, AuditMixin):
    __tablename__ = "forecast_detail"
    __table_args__ = (
        Index("ix_fd_version_period_sku", "version_id", "data_period", "sku_code"),
    )

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("forecast_versions.record_id"), nullable=False)
    dealer: Mapped[str] = mapped_column(String(64), nullable=False)
    brand: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(64))
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    avg_3m: Mapped[float] = mapped_column(Float, default=0)
    avg_6m: Mapped[float] = mapped_column(Float, default=0)
    weighted_forecast: Mapped[float] = mapped_column(Float, default=0)
    runrate_forecast: Mapped[float] = mapped_column(Float, default=0)
    statistical_forecast: Mapped[float] = mapped_column(Float, default=0)

    growth_factor: Mapped[float] = mapped_column(Float, default=1.0)
    seasonal_factor: Mapped[float] = mapped_column(Float, default=1.0)
    promotion_factor: Mapped[float] = mapped_column(Float, default=1.0)
    coverage_factor: Mapped[float] = mapped_column(Float, default=1.0)
    manual_adjustment_factor: Mapped[float] = mapped_column(Float, default=1.0)

    npi_forecast: Mapped[float] = mapped_column(Float, nullable=True)
    final_forecast_so: Mapped[float] = mapped_column(Float, default=0)
    forecast_method: Mapped[str] = mapped_column(String(32), default="Hybrid Forecast")
    forecast_comment: Mapped[str] = mapped_column(Text, nullable=True)
    adjustment_owner: Mapped[str] = mapped_column(String(64), nullable=True)

    actual_so: Mapped[float] = mapped_column(Float, nullable=True)  # filled once month closes
    forecast_error: Mapped[float] = mapped_column(Float, nullable=True)
    forecast_accuracy: Mapped[float] = mapped_column(Float, nullable=True)


# ---------------------------------------------------------------------------
# 6. SUPPLY PLAN
# ---------------------------------------------------------------------------
class SupplyPlan(Base, AuditMixin):
    __tablename__ = "supply_plan"
    __table_args__ = (
        Index("ix_sp_version_period_sku", "version_id", "data_period", "sku_code"),
    )

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("forecast_versions.record_id"), nullable=False)
    scenario: Mapped[str] = mapped_column(String(16), default="Base")  # Conservative/Base/Target/Stretch
    dealer: Mapped[str] = mapped_column(String(64), nullable=False)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    product_status: Mapped[str] = mapped_column(String(16))

    beginning_inventory: Mapped[float] = mapped_column(Float, default=0)
    sellable_inventory: Mapped[float] = mapped_column(Float, default=0)
    confirmed_inbound: Mapped[float] = mapped_column(Float, default=0)
    forecast_so: Mapped[float] = mapped_column(Float, default=0)
    plan_so: Mapped[float] = mapped_column(Float, default=0)
    target_stock_days: Mapped[int] = mapped_column(Integer, default=45)
    required_ending_inventory: Mapped[float] = mapped_column(Float, default=0)
    gross_requirement: Mapped[float] = mapped_column(Float, default=0)
    plan_si: Mapped[float] = mapped_column(Float, default=0)

    so_per_day: Mapped[float] = mapped_column(Float, default=0)
    dos: Mapped[float] = mapped_column(Float, nullable=True)
    weeks_of_cover: Mapped[float] = mapped_column(Float, nullable=True)
    dos_status: Mapped[str] = mapped_column(String(16), nullable=True)

    suggested_po_raw: Mapped[float] = mapped_column(Float, default=0)
    suggested_po_rounded: Mapped[float] = mapped_column(Float, default=0)
    reorder_point: Mapped[float] = mapped_column(Float, default=0)
    estimated_stockout_date: Mapped[date] = mapped_column(Date, nullable=True)
    latest_po_date: Mapped[date] = mapped_column(Date, nullable=True)
    po_status: Mapped[str] = mapped_column(String(32), nullable=True)
    supply_risk: Mapped[str] = mapped_column(String(16), nullable=True)  # Critical/Reorder/Healthy/Watch/Overstock
    planner_note: Mapped[str] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# 7. MANUAL ADJUSTMENTS / APPROVALS / ASSUMPTIONS / AUDIT
# ---------------------------------------------------------------------------
class ManualAdjustment(Base):
    __tablename__ = "manual_adjustments"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("forecast_versions.record_id"), nullable=True)
    target_table: Mapped[str] = mapped_column(String(32))  # forecast_detail / supply_plan
    target_record_id: Mapped[int] = mapped_column(Integer, nullable=True)
    dealer: Mapped[str] = mapped_column(String(64), nullable=True)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=True, index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str] = mapped_column(String(255), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    save_status: Mapped[str] = mapped_column(String(16), default="Saved")  # Saving/Saved/Save Failed
    adjusted_by: Mapped[str] = mapped_column(String(64))
    adjusted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApprovedPlan(Base):
    __tablename__ = "approved_plans"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("forecast_versions.record_id"), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(16), default="Approved")  # Approved / Locked / Unlocked
    notes: Mapped[str] = mapped_column(Text, nullable=True)


class Assumption(Base):
    __tablename__ = "assumptions"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assumption_type: Mapped[str] = mapped_column(String(32), index=True)
    # Growth / Seasonality / Promotion / Scenario / NPI / DOS_Threshold / WMA_Weight
    scope_key: Mapped[str] = mapped_column(String(128), index=True)
    # e.g. 'Dealer:A', 'Category:Cable', 'Brand:Belkin', 'Default', 'Q3'
    value: Mapped[float] = mapped_column(Float, default=0)
    value_text: Mapped[str] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(String(64))
    record_ref: Mapped[str] = mapped_column(String(64), nullable=True)
    field_name: Mapped[str] = mapped_column(String(64), nullable=True)
    old_value: Mapped[str] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(32))  # INSERT/UPDATE/DELETE/UPLOAD/APPROVE/LOCK/UNLOCK/LOGIN
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    forecast_version: Mapped[str] = mapped_column(String(64), nullable=True)
    performed_by: Mapped[str] = mapped_column(String(64))
    performed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
