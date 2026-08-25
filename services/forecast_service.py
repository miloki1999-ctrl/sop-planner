"""
Forecast Engine business logic — spec sections 10, 11, 12.

Performance note: the main loop below runs once per Product x Dealer
combination (often 100-1000+ combinations). The original implementation
queried the database (Assumption / RawSO tables) several times INSIDE that
loop, which is fine on local SQLite but very slow on a remote database
(Neon/Postgres) where every query costs a network round-trip. This version
preloads Assumptions and the relevant RawSO history ONCE into memory
(a dict and a pandas DataFrame respectively) before the loop starts, and
every per-combination lookup below reads from that in-memory data instead
of hitting the database again.
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

def load_all_assumptions(db: Session) -> dict:
    """One query for the whole (small) Assumptions table."""
    return {(a.assumption_type, a.scope_key): a.value for a in db.query(Assumption).all()}


def get_assumption(amap: dict, atype: str, scope_key: str, default: float = None):
    v = amap.get((atype, scope_key))
    return v if v is not None else default


def get_wma_weights(amap: dict) -> dict:
    weights = {}
    for m in ("M-1", "M-2", "M-3"):
        weights[m] = get_assumption(amap, "WMA_Weight", m, {"M-1": 0.5, "M-2": 0.3, "M-3": 0.2}[m])
    return weights


def get_seasonal_factor(amap: dict, target_period: str) -> float:
    y, m = map(int, target_period.split("-"))
    q = (m - 1) // 3 + 1
    return get_assumption(amap, "Seasonality", f"Q{q}", {1: 1.10, 2: 1.15, 3: 1.30, 4: 1.35}[q])


def get_growth_rate(amap: dict, dealer: str, sku: str, brand: str, category: str,
                     product_default_growth: float) -> tuple:
    v = get_assumption(amap, "Growth", f"SKU:{sku}|Dealer:{dealer}")
    if v is not None:
        return v, "Manual (SKU+Dealer)"
    v = get_assumption(amap, "Growth", f"Dealer:{dealer}")
    if v is not None:
        return v, "Dealer Growth"
    v = get_assumption(amap, "Growth", f"Category:{category}")
    if v is not None:
        return v, "Category Growth"
    v = get_assumption(amap, "Growth", f"Brand:{brand}")
    if v is not None:
        return v, "Brand Growth"
    if product_default_growth:
        return product_default_growth, "SKU Default Growth"
    v = get_assumption(amap, "Growth", "Default", 0.0)
    return v, "Default Growth"


def get_promotion_uplift(amap: dict, sku: str) -> float:
    return get_assumption(amap, "Promotion", f"SKU:{sku}", None) or \
           get_assumption(amap, "Promotion", "Default_Uplift", 0.15)


def get_phase_out_reduction(amap: dict) -> float:
    return get_assumption(amap, "EOL", "Phase_Out_Reduction_Rate", 0.30)


def get_npi_ramp_up(amap: dict, sku: str) -> float:
    return get_assumption(amap, "NPI", f"RampUp:{sku}", None) or \
           get_assumption(amap, "NPI", "Default_Ramp_Up_Rate", 0.60)


def load_so_history(db: Session, dealers: list, skus: list) -> pd.DataFrame:
    cols = [RawSO.dealer, RawSO.sku_code, RawSO.txn_date, RawSO.so_quantity,
            RawSO.promotion, RawSO.campaign, RawSO.store]
    if not dealers or not skus:
        return pd.DataFrame(columns=["dealer", "sku_code", "txn_date", "so_quantity",
                                      "promotion", "campaign", "store"])
    rows = db.query(*cols).filter(RawSO.dealer.in_(dealers), RawSO.sku_code.in_(skus)).all()
    df = pd.DataFrame(rows, columns=["dealer", "sku_code", "txn_date", "so_quantity",
                                      "promotion", "campaign", "store"])
    if not df.empty:
        df["promotion"] = df["promotion"].fillna("")
        df["campaign"] = df["campaign"].fillna("")
    return df


def monthly_so_series(so_df: pd.DataFrame, dealer: str, sku: str, before_period: str, n_months: int = 12) -> dict:
    y, m = map(int, before_period.split("-"))
    cutoff = date(y, m, 1)
    sub = so_df[(so_df["dealer"] == dealer) & (so_df["sku_code"] == sku) & (so_df["txn_date"] < cutoff)]
    if sub.empty:
        return {}
    sub = sub.copy()
    sub["period"] = pd.to_datetime(sub["txn_date"]).dt.to_period("M").astype(str)
    grouped = sub.groupby("period")["so_quantity"].sum().sort_index()
    return dict(grouped.tail(n_months))


def _last_n_month_labels(before_period: str, n: int) -> list:
    y, m = map(int, before_period.split("-"))
    cur = date(y, m, 1)
    labels = []
    for _ in range(n):
        cur = (cur - pd.DateOffset(months=1)).date()
        labels.append(cur.strftime("%Y-%m"))
    return labels


def avg_3m(series: dict, target_period: str) -> float:
    labels = _last_n_month_labels(target_period, 3)
    vals = [series[l] for l in labels if l in series]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def avg_6m(series: dict, target_period: str) -> float:
    labels = _last_n_month_labels(target_period, 6)
    vals = [series[l] for l in labels if l in series]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def weighted_forecast(series: dict, target_period: str, weights: dict) -> float:
    labels = _last_n_month_labels(target_period, 3)
    keys = ["M-1", "M-2", "M-3"]
    total = 0.0
    for label, key in zip(labels, keys):
        total += series.get(label, 0) * weights.get(key, 0)
    return round(total, 1)


def runrate_forecast(so_df: pd.DataFrame, dealer: str, sku: str, cutoff_date: date, target_period: str) -> float:
    cutoff_period = cutoff_date.strftime("%Y-%m")
    if target_period != cutoff_period:
        return 0.0
    y, m = map(int, target_period.split("-"))
    month_start = date(y, m, 1)
    days_in_month = monthrange(y, m)[1]
    elapsed_days = (cutoff_date - month_start).days + 1
    if elapsed_days <= 0:
        return 0.0
    sub = so_df[(so_df["dealer"] == dealer) & (so_df["sku_code"] == sku) &
                (so_df["txn_date"] >= month_start) & (so_df["txn_date"] <= cutoff_date)]
    mtd_qty = sub["so_quantity"].sum()
    so_per_day = mtd_qty / elapsed_days
    return round(so_per_day * days_in_month, 1)


def had_promotion_last_year(so_df: pd.DataFrame, dealer: str, sku: str, target_period: str) -> bool:
    y, m = map(int, target_period.split("-"))
    dt = pd.to_datetime(so_df["txn_date"])
    sub = so_df[(so_df["dealer"] == dealer) & (so_df["sku_code"] == sku) &
                (dt.dt.month == m) & (dt.dt.year == y - 1) &
                ((so_df["promotion"] != "") | (so_df["campaign"] != ""))]
    return not sub.empty


def get_coverage_factor() -> float:
    return 1.0


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


def compute_forecast_row(amap: dict, so_df: pd.DataFrame, product: Product, dealer_name: str, cutoff_date: date,
                          target_period: str, method: str, wma_weights: dict) -> ForecastRow:
    sku = product.sku_code
    series = monthly_so_series(so_df, dealer_name, sku, target_period, n_months=12)

    a3 = avg_3m(series, target_period)
    a6 = avg_6m(series, target_period)
    wf = weighted_forecast(series, target_period, wma_weights)
    rr = runrate_forecast(so_df, dealer_name, sku, cutoff_date, target_period)

    method_map = {
        "Average 3M": a3, "Average 6M": a6, "Weighted Moving Average": wf,
        "Run-rate": rr if rr > 0 else a3,
    }
    statistical = method_map.get(method, wf)

    growth_rate, growth_source = get_growth_rate(
        amap, dealer_name, sku, product.brand, product.category, product.default_growth_rate)
    growth_factor = 1 + growth_rate
    seasonal_factor = get_seasonal_factor(amap, target_period)
    coverage_factor = get_coverage_factor()

    had_promo_ly = had_promotion_last_year(so_df, dealer_name, sku, target_period)
    promotion_factor = (1 + get_promotion_uplift(amap, sku)) if had_promo_ly else 1.0

    npi_forecast_val = 0.0
    comment = ""

    if product.product_status == "EOL":
        forecast_method_label = "EOL - Clearance"
        final = a3
        growth_factor = 1.0
        seasonal_factor = 1.0
        promotion_factor = 1.0
        comment = f"EOL — chỉ dùng để giải phóng tồn kho. Replacement SKU: {product.replacement_sku or 'N/A'}"

    elif product.product_status == "Phase-out":
        forecast_method_label = "Phase-out Reduction"
        reduction = get_phase_out_reduction(amap)
        final = statistical * (1 - reduction)
        growth_factor = 1.0
        promotion_factor = 1.0
        comment = f"Phase-out — giảm {reduction*100:.0f}% so với Base Forecast"

    elif product.product_status in ("NPI",) or product.npi_flag:
        forecast_method_label = "NPI Forecast"
        ramp_up = get_npi_ramp_up(amap, sku)
        device_forecast = get_assumption(amap, "NPI", f"DeviceForecast:{sku}", 0)
        attach_rate = get_assumption(amap, "NPI", f"AttachRate:{sku}", 0)
        brand_share = get_assumption(amap, "NPI", f"BrandShare:{sku}", 1.0)
        sku_share = get_assumption(amap, "NPI", f"SKUShare:{sku}", 1.0)
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


def run_forecast_engine(db: Session, *, version_name: str, target_period: str, cutoff_date: date,
                         method: str, username: str, dealer_filter: list = None,
                         brand_filter: list = None, category_filter: list = None,
                         sku_filter: list = None) -> ForecastVersion:
    amap = load_all_assumptions(db)
    wma_weights = get_wma_weights(amap)

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

    so_df = load_so_history(db, [d.dealer_name for d in dealers], [p.sku_code for p in products])

    version = ForecastVersion(
        version_name=version_name, data_period=target_period, data_cutoff_date=cutoff_date,
        status="Draft", created_by=username,
    )
    db.add(version)
    db.flush()

    total_forecast = 0.0
    for product in products:
        for dealer in dealers:
            row = compute_forecast_row(amap, so_df, product, dealer.dealer_name, cutoff_date,
                                        target_period, method, wma_weights)
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
    details = db.query(ForecastDetail).filter_by(version_id=version.record_id).all()
    if not details:
        return pd.DataFrame()

    y, m = map(int, details[0].data_period.split("-"))
    start, end = date(y, m, 1), date(y, m, monthrange(y, m)[1])
    dealers = list({d.dealer for d in details})
    skus = list({d.sku_code for d in details})
    actuals = (
        db.query(RawSO.dealer, RawSO.sku_code, func.sum(RawSO.so_quantity))
        .filter(RawSO.dealer.in_(dealers), RawSO.sku_code.in_(skus),
                RawSO.txn_date >= start, RawSO.txn_date <= end)
        .group_by(RawSO.dealer, RawSO.sku_code)
        .all()
    )
    actual_map = {(dealer, sku): qty for dealer, sku, qty in actuals}

    rows = []
    for d in details:
        actual = actual_map.get((d.dealer, d.sku_code))
        if actual is None:
            continue
        d.actual_so = actual
        d.forecast_error = actual - d.final_forecast_so
        d.forecast_accuracy = None

        # Guard against division by zero when actual demand is 0.
        if actual:
            accuracy_pct = round((1 - abs(d.forecast_error) / actual) * 100, 1)
            accuracy_pct = max(0.0, accuracy_pct)  # clamp: don't show negative accuracy
        else:
            accuracy_pct = None

        d.forecast_accuracy = accuracy_pct

        rows.append({
            "dealer": d.dealer,
            "brand": d.brand,
            "category": d.category,
            "sku_code": d.sku_code,
            "data_period": d.data_period,
            "final_forecast_so": d.final_forecast_so,
            "actual_so": d.actual_so,
            "forecast_error": d.forecast_error,
            "forecast_accuracy_pct": accuracy_pct,
            "accuracy_category": classify_accuracy(accuracy_pct),
        })

    db.flush()
    return pd.DataFrame(rows)
