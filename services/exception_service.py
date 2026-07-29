"""
Exception Report business logic — spec section 18.5. Surfaces only the
items that need planner action, pulled from data already computed by
Upload Center / Forecast Engine / Supply Plan (no separate exception
table — this module queries + synthesizes on read).
"""
from datetime import date, timedelta
from calendar import monthrange
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from database.models import (
    Product, Dealer, RawSO, InventorySnapshot, PurchaseOrder,
    SupplyPlan, ForecastVersion, ForecastDetail,
)
from services.forecast_service import monthly_so_series, _last_n_month_labels


def _row(exception_type, dealer, sku_code, sku_name, dos, suggested_po, stockout_date, action, severity):
    return dict(
        exception_type=exception_type, dealer=dealer or "-", sku_code=sku_code, sku_name=sku_name or "",
        dos=dos, suggested_po=suggested_po, stockout_date=stockout_date, recommended_action=action,
        severity=severity,
    )


def check_stockout_30d(db: Session, version_id: int, cutoff_date: date) -> list:
    rows = db.query(SupplyPlan).filter_by(version_id=version_id, scenario="Base").all()
    out = []
    for r in rows:
        if r.estimated_stockout_date and (r.estimated_stockout_date - cutoff_date).days <= 30 and r.dos_status in ("Critical", "Reorder"):
            out.append(_row("Stockout trong 30 ngày", r.dealer, r.sku_code, "", r.dos,
                             r.suggested_po_rounded, r.estimated_stockout_date, r.po_status, "critical"))
    return out


def check_overstock(db: Session, version_id: int) -> list:
    rows = db.query(SupplyPlan).filter_by(version_id=version_id, scenario="Base", dos_status="Overstock").all()
    return [_row("DOS trên 95 ngày (Overstock)", r.dealer, r.sku_code, "", r.dos, r.suggested_po_rounded,
                  None, "Review Price / Clear Inventory", "warning") for r in rows]


def check_dead_stock(db: Session, version_id: int) -> list:
    rows = db.query(SupplyPlan).filter_by(version_id=version_id, scenario="Base", dos_status="No Sales").all()
    return [_row("Không có SO nhưng còn tồn", r.dealer, r.sku_code, "", r.dos, r.suggested_po_rounded,
                  None, "Review / Clear Inventory", "warning") for r in rows if r.sellable_inventory > 0]


def check_declining_so(db: Session, target_period: str) -> list:
    """SO giảm liên tục 3 tháng — strictly decreasing M-3 > M-2 > M-1 > 0."""
    out = []
    combos = db.query(RawSO.dealer, RawSO.sku_code, RawSO.sku_name).distinct().all()
    for dealer, sku, sku_name in combos:
        series = monthly_so_series(db, dealer, sku, target_period, n_months=6)
        labels = _last_n_month_labels(target_period, 3)  # [M-1, M-2, M-3]
        vals = [series.get(l, 0) for l in reversed(labels)]  # chronological M-3, M-2, M-1
        if len(vals) == 3 and vals[0] > vals[1] > vals[2] > 0:
            out.append(_row("SO giảm liên tục 3 tháng", dealer, sku, sku_name, None, None, None,
                             "Reduce SI / Review Forecast", "warning"))
    return out


def check_low_forecast_accuracy(db: Session, version_id: int, threshold: float = 70.0) -> list:
    version = db.get(ForecastVersion, version_id)
    if not version:
        return []
    y, m = map(int, version.data_period.split("-"))
    details = db.query(ForecastDetail).filter_by(version_id=version_id).all()
    out = []
    for d in details:
        actual = db.query(func.sum(RawSO.so_quantity)).filter(
            RawSO.dealer == d.dealer, RawSO.sku_code == d.sku_code,
            RawSO.txn_date >= date(y, m, 1), RawSO.txn_date <= date(y, m, monthrange(y, m)[1]),
        ).scalar()
        if actual is None or actual == 0:
            continue
        error = actual - d.final_forecast_so
        accuracy = round((1 - abs(error) / actual) * 100, 1)
        if accuracy < threshold:
            out.append(_row("Forecast Accuracy thấp (<70%)", d.dealer, d.sku_code, "", None, None, None,
                             "Review Forecast Method", "warning"))
    return out


def check_inbound_delayed(db: Session, cutoff_date: date) -> list:
    rows = db.query(PurchaseOrder).filter(
        PurchaseOrder.expected_arrival_date < cutoff_date,
        PurchaseOrder.actual_arrival_date.is_(None),
        PurchaseOrder.outstanding_quantity > 0,
    ).all()
    return [_row("Inbound trễ ETA", None, r.sku_code, r.sku_name, None, r.outstanding_quantity,
                 r.expected_arrival_date, "Follow up Supplier", "warning") for r in rows]


def check_npi_no_inventory(db: Session) -> list:
    npi_products = db.query(Product).filter(
        (Product.product_status == "NPI") | (Product.npi_flag == True)
    ).all()
    out = []
    for p in npi_products:
        total_inv = db.query(func.sum(InventorySnapshot.sellable_inventory)).filter_by(sku_code=p.sku_code).scalar() or 0
        if total_inv <= 0:
            out.append(_row("NPI chưa có Inventory", None, p.sku_code, p.sku_name, None, None, None,
                             "Order Now", "critical"))
    return out


def check_eol_with_po(db: Session) -> list:
    eol_skus = {p.sku_code: p for p in db.query(Product).filter(
        Product.product_status.in_(["EOL", "Discontinued"])).all()}
    rows = db.query(PurchaseOrder).filter(
        PurchaseOrder.sku_code.in_(list(eol_skus.keys())), PurchaseOrder.outstanding_quantity > 0
    ).all() if eol_skus else []
    return [_row("EOL nhưng vẫn còn PO", None, r.sku_code, r.sku_name, None, r.outstanding_quantity,
                 None, "Cancel PO", "critical") for r in rows]


def check_negative_inventory(db: Session) -> list:
    rows = db.query(InventorySnapshot).filter(InventorySnapshot.sellable_inventory < 0).all()
    return [_row("Inventory âm", r.dealer, r.sku_code, r.sku_name, None, None, None,
                  "Kiểm tra lại dữ liệu tồn kho", "critical") for r in rows]


def check_unmapped_dealer(db: Session) -> list:
    known = {d.dealer_name for d in db.query(Dealer).all()}
    out = []
    for model, label in [(RawSO, "RAW_SO"), (InventorySnapshot, "RAW_INVENTORY")]:
        dealers_in_data = {r[0] for r in db.query(model.dealer).distinct().all()}
        for d in dealers_in_data - known:
            out.append(_row(f"Thiếu Mapping Dealer ({label})", d, "-", "", None, None, None,
                             "Map Dealer trong Master Data", "warning"))
    return out


def check_missing_master_fields(db: Session) -> list:
    out = []
    products = db.query(Product).filter(
        (Product.lead_time <= 0) | (Product.safety_stock_days <= 0) | (Product.target_stock_days <= 0)
    ).all()
    for p in products:
        missing = []
        if p.lead_time <= 0:
            missing.append("Lead Time")
        if p.safety_stock_days <= 0:
            missing.append("Safety Stock Days")
        if p.target_stock_days <= 0:
            missing.append("Target Stock Days")
        out.append(_row(f"Thiếu {', '.join(missing)}", None, p.sku_code, p.sku_name, None, None, None,
                         "Cập nhật Master Data", "warning"))
    return out


def get_all_exceptions(db: Session, *, version_id: int = None, cutoff_date: date = None) -> pd.DataFrame:
    cutoff_date = cutoff_date or date.today()
    rows = []

    if version_id:
        version = db.get(ForecastVersion, version_id)
        rows += check_stockout_30d(db, version_id, cutoff_date)
        rows += check_overstock(db, version_id)
        rows += check_dead_stock(db, version_id)
        rows += check_low_forecast_accuracy(db, version_id)
        if version:
            rows += check_declining_so(db, version.data_period)

    rows += check_inbound_delayed(db, cutoff_date)
    rows += check_npi_no_inventory(db)
    rows += check_eol_with_po(db)
    rows += check_negative_inventory(db)
    rows += check_unmapped_dealer(db)
    rows += check_missing_master_fields(db)

    if not rows:
        return pd.DataFrame(columns=["exception_type", "dealer", "sku_code", "sku_name", "dos",
                                      "suggested_po", "stockout_date", "recommended_action", "severity"])
    df = pd.DataFrame(rows)
    severity_order = {"critical": 0, "warning": 1}
    df["_sort"] = df["severity"].map(severity_order).fillna(2)
    df = df.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)
    return df
