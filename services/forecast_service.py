"""
Forecast Engine business logic — spec sections 10, 11, 12.

Design notes:
- All "tunable" numbers (WMA weights, seasonality index, growth rates,
  promotion uplift, EOL reduction rate, NPI ramp-up) are read from the
  `assumptions` table, NOT hardcoded, so Assumptions UI (built later) can
  change behavior without a code change.
- `run_forecast_engine()` is the single entrypoint pages/forecast_engine.py
  calls: it creates a new ForecastVersion (status=Draft) and one
  ForecastDetail row per Dealer x SKU combination that has master data +
  history (or is NPI).
- Growth-rate priority chain (spec section 10 "Growth Forecast"):
  Manual (SKU+Dealer) > Dealer > Category > Brand > Default.
"""
from datetime import date
from calendar import monthrange
from dataclasses import dataclass
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from database.models import (
    Product, Dealer, RawSO, Assumption, ForecastVersion, ForecastDetail,
)

# ---------------------------------------------------------------------------
# Assumption lookup helpers
# ---------------------------------------------------------------------------
def get_assumption(db: Session, atype: str, scope_key: str, default: float = None):
    row = db.query(Assumption).filter_by(assumption_type=atype, scope_key=scope_key).first()
    return row.value if row else default


def get_wma_weights(db: Session) -> dict:
    weights = {}
    for m in ("M-1", "M-2", "M-3"):
        weights[m] = get_assumption(db, "WMA_Weight", m, {"M-1": 0.5, "M-2": 0.3, "M-3": 0.2}[m])
    return weights


def get_seasonal_factor(db: Session, target_period: str) -> float:
    y, m = map(int, target_period.split("-"))
    q = (m - 1) // 3 + 1
    return get_assumption(db, "Seasonality", f"Q{q}", {1: 1.10, 2: 1.15, 3: 1.30, 4: 1.35}[q])


def get_growth_rate(db: Session, dealer: str, sku: str, brand: str, category: str,
                     product_default_growth: float) -> tuple:
    """Returns (growth_rate, source_label) following the priority chain."""
    v = get_assumption(db, "Growth", f"SKU:{sku}|Dealer:{dealer}")
    if v is not None:
        return v, "Manual (SKU+Dealer)"
    v = get_assumption(db, "Growth", f"Dealer:{dealer}")
    if v is not None:
        return v, "Dealer Growth"
    v = get_assumption(db, "Growth", f"Category:{category}")
    if v is not None:
        return v, "Category Growth"
    v = get_assumption(db, "Growth", f"Brand:{brand}")
    if v is not None:
        return v, "Brand Growth"
    if product_default_growth:
        return product_default_growth, "SKU Default Growth"
    v = get_assumption(db, "Growth", "Default", 0.0)
    return v, "Default Growth"


def get_promotion_uplift(db: Session, sku: str) -> float:
    return get_assumption(db, "Promotion", f"SKU:{sku}", None) or \
           get_assumption(db, "Promotion", "Default_Uplift", 0.15)


def get_phase_out_reduction(db: Session) -> float:
    return get_assumption(db, "EOL", "Phase_Out_Reduction_Rate", 0.30)


def get_npi_ramp_up(db: Session, sku: str) -> float:
    return get_assumption(db, "NPI", f"RampUp:{sku}", None) or \
           get_assumption(db, "NPI", "Default_Ramp_Up_Rate", 0.60)


# ---------------------------------------------------------------------------
# Historical SO series
# ---------------------------------------------------------------------------
def monthly_so_series(db: Session, dealer: str, sku: str, before_period: str, n_months: int = 12) -> dict:
    """Returns {period_str '2024-03': qty} for up to n_months before before_period (exclusive)."""
    y, m = map(int, before_period.split("-"))
    rows = db.query(RawSO.txn_date, RawSO.so_quantity).filter(
        RawSO.dealer == dealer, RawSO.sku_code == sku,
        RawSO.txn_date < date(y, m, 1),
    ).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["txn_date", "so_quantity"])
    df["period"] = pd.to_datetime(df["txn_date"]).dt.to_period("M").astype(str)
    grouped = df.groupby("period")["so_quantity"].sum().sort_index()
    return dict(grouped.tail(n_months))


def _last_n_month_labels(before_period: str, n: int) -> list:
    y, m = map(int, before_period.split("-"))
    cur = date(y, m, 1)
    labels = []
    for _ in range(n):
        cur = (cur - pd.DateOffset(months=1)).date()
        labels.append(cur.strftime("%Y-%m"))
    return labels  # nearest-first: [M-1, M-2, M-3, ...]


def avg_3m(series: dict, target_period: str) -> float:
    labels = _last_n_month_labels(target_period, 3)
    vals = [series[l] for l in labels if l in series]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def avg_6m(series: dict, target_period: str) -> float:
    labels = _last_n_month_labels(target_period, 6)
    vals = [series[l] for l in labels if l in series]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def weighted_forecast(series: dict, target_period: str, weights: dict) -> float:
    labels = _last_n_month_labels(target_period, 3)  # [M-1, M-2, M-3]
    keys = ["M-1", "M-2", "M-3"]
    total = 0.0
    for label, key in zip(labels, keys):
        total += series.get(label, 0) * weights.get(key, 0)
    return round(total, 1)


def runrate_forecast(db: Session, dealer: str, sku: str, cutoff_date: date, target_period: str) -> float:
    """Only meaningful when target_period == cutoff month (projecting the partial current month)."""
    cutoff_period = cutoff_date.strftime("%Y-%m")
    if target_period != cutoff_period:
        return 0.0
    y, m = map(int, target_period.split("-"))
    month_start = date(y, m, 1)
    days_in_month = monthrange(y, m)[1]
    elapsed_days = (cutoff_date - month_start).days + 1
    if elapsed_days <= 0:
        return 0.0
    mtd_qty = db.query(func.sum(RawSO.so_quantity)).filter(
        RawSO.dealer == dealer, RawSO.sku_code == sku,
        RawSO.txn_date >= month_start, RawSO.txn_date <= cutoff_date,
    ).scalar() or 0
    so_per_day = mtd_qty / elapsed_days
    return round(so_per_day * days_in_month, 1)


# ---------------------------------------------------------------------------
# Coverage factor (Planned Active Stores / Current Active Stores)
# ---------------------------------------------------------------------------
def get_coverage_factor(db: Session, dealer: str, sku: str, before_period: str) -> float:
    y, m = map(int, before_period.split("-"))
    current_stores = db.query(RawSO.store).filter(
        RawSO.dealer == dealer, RawSO.sku_code == sku,
        RawSO.txn_date < date(y, m, 1),
        RawSO.txn_date >= (date(y, m, 1) - pd.DateOffset(months=1)).date(),
    ).distinct().count()
    if current_stores == 0:
        return 1.0
    return 1.0  # planned = current by default (no override entered yet)


# ---------------------------------------------------------------------------
# Core per-row forecast computation
# ---------------------------------------------------------------------------
@dataclass
class ForecastRow:
    dealer: str
    brand: str
    category: str
    sku_code: str
    avg_3m: float
    avg_6m: float
    weighted_forecast: float
    runrate_forecast: float
    statistical_forecast: float
    growth_factor: float
    growth_source: str
    seasonal_factor: float
    promotion_factor: float
    coverage_factor: float
    manual_adjustment_factor: float
    npi_forecast: float
    final_forecast_so: float
    forecast_method: str
    forecast_comment: str


def compute_forecast_row(db: Session, product: Product, dealer_name: str, cutoff_date: date,
                          target_period: str, method: str, wma_weights: dict) -> ForecastRow:
    sku = product.sku_code
    series = monthly_so_series(db, dealer_name, sku, target_period, n_months=12)

    a3 = avg_3m(series, target_period)
    a6 = avg_6m(series, target_period)
    wf = weighted_forecast(series, target_period, wma_weights)
    rr = runrate_forecast(db, dealer_name, sku, cutoff_date, target_period)

    method_map = {
        "Average 3M": a3, "Average 6M": a6, "Weighted Moving Average": wf,
        "Run-rate": rr if rr > 0 else a3,
    }
    statistical = method_map.get(method, wf)

    growth_rate, growth_source = get_growth_rate(
        db, dealer_name, sku, product.brand, product.category, product.default_growth_rate)
    growth_factor = 1 + growth_rate
    seasonal_factor = get_seasonal_factor(db, target_period)
    coverage_factor = get_coverage_factor(db, dealer_name, sku, target_period)

    # promotion applies only if same calendar month last year had a promo/campaign flag
    y, m = map(int, target_period.split("-"))
    had_promo_ly = db.query(RawSO.record_id).filter(
        RawSO.dealer == dealer_name, RawSO.sku_code == sku,
        func.strftime("%m", RawSO.txn_date) == f"{m:02d}",
        func.strftime("%Y", RawSO.txn_date) == str(y - 1),
        (RawSO.promotion != "") | (RawSO.campaign != ""),
    ).first()
    promotion_factor = (1 + get_promotion_uplift(db, sku)) if had_promo_ly else 1.0

    npi_forecast_val = 0.0
    comment = ""

    if product.product_status == "EOL":
        forecast_method_label = "EOL - Clearance"
        final = a3  # clear-inventory basis only, no growth/seasonal amplification
        growth_factor = 1.0
        seasonal_factor = 1.0
        promotion_factor = 1.0
        comment = f"EOL — chỉ dùng để giải phóng tồn kho. Replacement SKU: {product.replacement_sku or 'N/A'}"

    elif product.product_status == "Phase-out":
        forecast_method_label = "Phase-out Reduction"
        reduction = get_phase_out_reduction(db)
        final = statistical * (1 - reduction)
        growth_factor = 1.0
        promotion_factor = 1.0
        comment = f"Phase-out — giảm {reduction*100:.0f}% so với Base Forecast"

    elif product.product_status in ("NPI",) or product.npi_flag:
        forecast_method_label = "NPI Forecast"
        ramp_up = get_npi_ramp_up(db, sku)
        device_forecast = get_assumption(db, "NPI", f"DeviceForecast:{sku}", 0)
        attach_rate = get_assumption(db, "NPI", f"AttachRate:{sku}", 0)
        brand_share = get_assumption(db, "NPI", f"BrandShare:{sku}", 1.0)
        sku_share = get_assumption(db, "NPI", f"SKUShare:{sku}", 1.0)
        npi_forecast_val = device_forecast * attach_rate * brand_share * sku_share * ramp_up
        statistical = npi_forecast_val
        growth_factor = 1.0
        final = npi_forecast_val * seasonal_factor * coverage_factor
        comment = ("Chưa nhập NPI Assumptions (Device Forecast/Attach Rate/Share) — vào Assumptions "
                   "để cấu hình." if device_forecast == 0 else "")

    else:
        forecast_method_label = method
        final = statistical * growth_factor * seasonal_factor * promotion_factor * coverage_factor

    return ForecastRow(
        dealer=dealer_name, brand=product.brand, category=product.category, sku_code=sku,
        avg_3m=a3, avg_6m=a6, weighted_forecast=wf, runrate_forecast=rr,
        statistical_forecast=round(statistical, 1),
        growth_factor=round(growth_factor, 3), growth_source=growth_source,
        seasonal_factor=round(seasonal_factor, 3), promotion_factor=round(promotion_factor, 3),
        coverage_factor=round(coverage_factor, 3), manual_adjustment_factor=1.0,
        npi_forecast=round(npi_forecast_val, 1), final_forecast_so=round(max(0, final), 1),
        forecast_method=forecast_method_label, forecast_comment=comment,
    )


# ---------------------------------------------------------------------------
# Version-level orchestration
# ---------------------------------------------------------------------------
def run_forecast_engine(db: Session, *, version_name: str, target_period: str, cutoff_date: date,
                         method: str, username: str, dealer_filter: list = None,
                         brand_filter: list = None, category_filter: list = None,
                         sku_filter: list = None) -> ForecastVersion:
    wma_weights = get_wma_weights(db)

    q = db.query(Product).filter(Product.product_status != "Discontinued")
    if brand_filter:
        q = q.filter(Product.brand.in_(brand_filter))
    if category_filter:
        q = q.filter(Product.category.in_(category_filter))
    if sku_filter:
        q = q.filter(Product.sku_code.in_(sku_filter))
    products = q.all()

    dealers = db.query(Dealer).filter(Dealer.is_active == True)
    if dealer_filter:
        dealers = dealers.filter(Dealer.dealer_name.in_(dealer_filter))
    dealers = dealers.all()

    version = ForecastVersion(
        version_name=version_name, data_period=target_period, data_cutoff_date=cutoff_date,
        status="Draft", created_by=username,
    )
    db.add(version)
    db.flush()

    total_forecast = 0.0
    for product in products:
        for dealer in dealers:
            row = compute_forecast_row(db, product, dealer.dealer_name, cutoff_date, target_period, method, wma_weights)
            detail = ForecastDetail(
                version_id=version.record_id, dealer=row.dealer, brand=row.brand, category=row.category,
                sku_code=row.sku_code, avg_3m=row.avg_3m, avg_6m=row.avg_6m,
                weighted_forecast=row.weighted_forecast, runrate_forecast=row.runrate_forecast,
                statistical_forecast=row.statistical_forecast, growth_factor=row.growth_factor,
                seasonal_factor=row.seasonal_factor, promotion_factor=row.promotion_factor,
                coverage_factor=row.coverage_factor, manual_adjustment_factor=row.manual_adjustment_factor,
                npi_forecast=row.npi_forecast or None, final_forecast_so=row.final_forecast_so,
                forecast_method=row.forecast_method, forecast_comment=row.forecast_comment,
                adjustment_owner=username, data_period=target_period, created_by=username,
            )
            db.add(detail)
            total_forecast += row.final_forecast_so

    version.total_forecast_so = round(total_forecast, 1)
    db.flush()
    return version


def apply_manual_adjustment(db: Session, detail: ForecastDetail, new_factor: float, reason: str, username: str):
    old_factor = detail.manual_adjustment_factor
    base = detail.statistical_forecast if detail.npi_forecast is None else detail.npi_forecast
    non_manual = detail.growth_factor * detail.seasonal_factor * detail.promotion_factor * detail.coverage_factor
    detail.manual_adjustment_factor = new_factor
    detail.final_forecast_so = round(max(0, base * non_manual * new_factor), 1)
    detail.adjustment_owner = username
    from database.models import ManualAdjustment
    db.add(ManualAdjustment(
        version_id=detail.version_id, target_table="forecast_detail", target_record_id=detail.record_id,
        dealer=detail.dealer, sku_code=detail.sku_code, field_name="manual_adjustment_factor",
        old_value=str(old_factor), new_value=str(new_factor), reason=reason,
        save_status="Saved", adjusted_by=username,
    ))


# ---------------------------------------------------------------------------
# Forecast accuracy (spec section 12)
# ---------------------------------------------------------------------------
def classify_accuracy(pct: float) -> str:
    if pct is None:
        return "N/A"
    if pct >= 90:
        return "Excellent"
    if pct >= 80:
        return "Good"
    if pct >= 70:
        return "Need Review"
    return "Poor"


def compute_accuracy_for_version(db: Session, version: ForecastVersion) -> pd.DataFrame:
    """Fills actual_so / forecast_error / forecast_accuracy for a version's details
    by looking up realized RawSO in the target period, and returns a summary df."""
    details = db.query(ForecastDetail).filter_by(version_id=version.record_id).all()
    rows = []
    for d in details:
        y, m = map(int, d.data_period.split("-"))
        actual = db.query(func.sum(RawSO.so_quantity)).filter(
            RawSO.dealer == d.dealer, RawSO.sku_code == d.sku_code,
            RawSO.txn_date >= date(y, m, 1), RawSO.txn_date <= date(y, m, monthrange(y, m)[1]),
        ).scalar()
        if actual is None:
            continue
        d.actual_so = actual
        d.forecast_error = actual - d.final_forecast_so
        d.forecast_accuracy = round(1 - abs(d.forecast_error) / actual, 4) if actual else None
        rows.append(dict(
            dealer=d.dealer, brand=d.brand, category=d.category, sku_code=d.sku_code,
            forecast_method=d.forecast_method, final_forecast_so=d.final_forecast_so,
            actual_so=actual, forecast_error=d.forecast_error,
            abs_error=abs(d.forecast_error), accuracy_pct=(d.forecast_accuracy or 0) * 100,
        ))
    return pd.DataFrame(rows)
