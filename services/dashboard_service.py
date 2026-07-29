"""
Management Dashboard data layer — spec section 18.1. Pure query/aggregation
functions; pages/dashboard.py handles all rendering (Plotly charts, KPI
cards). Kept separate so Dashboard logic is testable without Streamlit.
"""
from datetime import date
from calendar import monthrange
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from database.models import RawSI, RawSO, InventorySnapshot, SupplyPlan, ForecastDetail, ForecastVersion


def _month_bounds(period: str):
    y, m = map(int, period.split("-"))
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


def kpi_summary(db: Session, period: str, version_id: int = None) -> dict:
    start, end = _month_bounds(period)

    actual_si = db.query(func.sum(RawSI.si_quantity)).filter(RawSI.txn_date.between(start, end)).scalar() or 0
    actual_so = db.query(func.sum(RawSO.so_quantity)).filter(RawSO.txn_date.between(start, end)).scalar() or 0

    forecast_so = plan_so = plan_si = suggested_po = inventory = inbound = avg_dos = accuracy = 0
    if version_id:
        details = db.query(ForecastDetail).filter_by(version_id=version_id).all()
        forecast_so = sum(d.final_forecast_so for d in details)

        plans = db.query(SupplyPlan).filter_by(version_id=version_id, scenario="Base").all()
        plan_so = sum(p.plan_so for p in plans)
        plan_si = sum(p.plan_si for p in plans)
        suggested_po = sum(p.suggested_po_rounded for p in plans)
        inventory = sum(p.sellable_inventory for p in plans)
        inbound = sum(p.confirmed_inbound for p in plans)
        dos_vals = [p.dos for p in plans if p.dos is not None]
        avg_dos = round(sum(dos_vals) / len(dos_vals), 1) if dos_vals else 0

        accs = [d for d in details if d.forecast_accuracy is not None]
        accuracy = round(sum(d.forecast_accuracy for d in accs) / len(accs) * 100, 1) if accs else None

    prev_start, prev_end = _month_bounds(_shift_period(period, -1))
    prev_so = db.query(func.sum(RawSO.so_quantity)).filter(RawSO.txn_date.between(prev_start, prev_end)).scalar() or 0
    mom_growth = round((actual_so / prev_so - 1) * 100, 1) if prev_so else None

    ly_start, ly_end = _month_bounds(_shift_period(period, -12))
    ly_so = db.query(func.sum(RawSO.so_quantity)).filter(RawSO.txn_date.between(ly_start, ly_end)).scalar() or 0
    yoy_growth = round((actual_so / ly_so - 1) * 100, 1) if ly_so else None

    return dict(
        actual_si=actual_si, actual_so=actual_so, forecast_so=forecast_so, plan_so=plan_so,
        plan_si=plan_si, inventory=inventory, inbound=inbound, suggested_po=suggested_po,
        avg_dos=avg_dos, forecast_accuracy=accuracy, mom_growth=mom_growth, yoy_growth=yoy_growth,
    )


def _shift_period(period: str, months: int) -> str:
    y, m = map(int, period.split("-"))
    total = y * 12 + (m - 1) + months
    return f"{total // 12}-{(total % 12) + 1:02d}"


def trend_si_so_forecast(db: Session, end_period: str, n_months: int = 12, version_id: int = None) -> pd.DataFrame:
    periods = [_shift_period(end_period, -i) for i in range(n_months - 1, -1, -1)]
    rows = []
    for p in periods:
        start, end = _month_bounds(p)
        si = db.query(func.sum(RawSI.si_quantity)).filter(RawSI.txn_date.between(start, end)).scalar() or 0
        so = db.query(func.sum(RawSO.so_quantity)).filter(RawSO.txn_date.between(start, end)).scalar() or 0
        fc = 0
        if version_id and p == end_period:
            fc = db.query(func.sum(ForecastDetail.final_forecast_so)).filter_by(version_id=version_id).scalar() or 0
        rows.append(dict(period=p, si=si, so=so, forecast=fc))
    return pd.DataFrame(rows)


def actual_vs_plan_so(db: Session, version_id: int) -> dict:
    version = db.get(ForecastVersion, version_id)
    if not version:
        return {}
    start, end = _month_bounds(version.data_period)
    actual = db.query(func.sum(RawSO.so_quantity)).filter(RawSO.txn_date.between(start, end)).scalar() or 0
    plan = db.query(func.sum(SupplyPlan.plan_so)).filter_by(version_id=version_id, scenario="Base").scalar() or 0
    return dict(actual=actual, plan=plan)


def top_sku_movers(db: Session, period: str, n: int = 10) -> tuple:
    cur_start, cur_end = _month_bounds(period)
    prev_start, prev_end = _month_bounds(_shift_period(period, -1))

    cur = dict(db.query(RawSO.sku_code, func.sum(RawSO.so_quantity)).filter(
        RawSO.txn_date.between(cur_start, cur_end)).group_by(RawSO.sku_code).all())
    prev = dict(db.query(RawSO.sku_code, func.sum(RawSO.so_quantity)).filter(
        RawSO.txn_date.between(prev_start, prev_end)).group_by(RawSO.sku_code).all())

    rows = []
    for sku in set(cur) | set(prev):
        c, p = cur.get(sku, 0), prev.get(sku, 0)
        if p > 0:
            growth = round((c / p - 1) * 100, 1)
        elif c > 0:
            growth = 100.0
        else:
            continue
        rows.append(dict(sku_code=sku, so_mtd=c, mom_growth=growth))
    df = pd.DataFrame(rows)
    if df.empty:
        return df, df
    gainers = df.sort_values("mom_growth", ascending=False).head(n)
    losers = df.sort_values("mom_growth", ascending=True).head(n)
    return gainers, losers


def top_dealers(db: Session, period: str, n: int = 10) -> pd.DataFrame:
    start, end = _month_bounds(period)
    rows = db.query(RawSO.dealer, func.sum(RawSO.so_quantity)).filter(
        RawSO.txn_date.between(start, end)).group_by(RawSO.dealer).order_by(func.sum(RawSO.so_quantity).desc()).limit(n).all()
    return pd.DataFrame(rows, columns=["dealer", "so_quantity"])


def supply_risk_breakdown(db: Session, version_id: int) -> pd.DataFrame:
    rows = db.query(SupplyPlan.supply_risk, func.count()).filter_by(
        version_id=version_id, scenario="Base").group_by(SupplyPlan.supply_risk).all()
    return pd.DataFrame(rows, columns=["risk", "count"])


def inventory_dos_trend(db: Session, end_period: str, n_months: int = 12, version_id: int = None) -> pd.DataFrame:
    periods = [_shift_period(end_period, -i) for i in range(n_months - 1, -1, -1)]
    rows = []
    for p in periods:
        start, end = _month_bounds(p)
        # approximate historical inventory using latest snapshot at/just after month end available
        inv = db.query(func.sum(InventorySnapshot.sellable_inventory)).filter(
            InventorySnapshot.snapshot_date.between(start, end)).scalar()
        so = db.query(func.sum(RawSO.so_quantity)).filter(RawSO.txn_date.between(start, end)).scalar() or 0
        days_in_month = (end - start).days + 1
        so_per_day = so / days_in_month if days_in_month else 0
        dos = round((inv or 0) / so_per_day, 1) if so_per_day > 0 else None
        rows.append(dict(period=p, inventory=inv or 0, dos=dos))
    return pd.DataFrame(rows)
