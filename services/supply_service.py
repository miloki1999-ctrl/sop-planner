"""
Supply Plan business logic — spec sections 13, 14, 15, 16.

Key design decision (documented because the source spec has a structural
gap): `RAW_PO` (purchase_orders) has no Dealer column — it represents PO to
the upstream supplier at SKU level, not allocated to a specific dealer.
`RAW_INVENTORY` (inventory_snapshots), however, already carries an
"Inbound Quantity" per Dealer + SKU (goods already confirmed incoming to
that dealer's warehouse). Since Plan SI / Suggested PO in this system are
computed per Dealer x SKU (per the Supply Plan page layout), we use
InventorySnapshot.inbound_quantity as "Confirmed Inbound" for those
formulas. RAW_PO stays the source of truth for company-level PO tracking
and exceptions (EOL-with-PO, ETA delays) — see exception_report module.

SO per Day is computed from the Final Forecast SO of the selected Forecast
Version (forward-looking), divided by the number of days in the target
month — this is what drives DOS/Suggested PO for *future* procurement
decisions, which is the actual business use of this page (Sellable
Inventory today vs. what we'll need going forward).

Performance note: this runs once per (scenario x ForecastDetail), i.e. 4x
the number of Dealer x SKU rows in the Forecast Version. The inventory
snapshot for a given (dealer, sku) is the same across all 4 scenarios, so
it's prefetched ONCE for the whole run (one query) instead of being
queried again for every scenario — see load_latest_inventory_map(). Same
idea for Assumptions (Scenario factors / DOS thresholds): loaded once into
a dict via forecast_service.load_all_assumptions() instead of querying the
Assumption table on every row.
"""
from datetime import date, timedelta
from calendar import monthrange
import math
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import delete

from database.models import (
    Product, ForecastVersion, ForecastDetail, InventorySnapshot, SupplyPlan,
)
from services.forecast_service import get_assumption, load_all_assumptions

SCENARIOS = ["Conservative", "Base", "Target", "Stretch"]


def get_scenario_factor(amap: dict, scenario: str) -> float:
    defaults = {"Conservative": 0.90, "Base": 1.00, "Target": 1.10, "Stretch": 1.20}
    return get_assumption(amap, "Scenario", scenario, defaults.get(scenario, 1.0))


def get_dos_thresholds(amap: dict) -> dict:
    defaults = {"critical": 30, "reorder": 45, "healthy": 70, "watch": 95}
    return {k: get_assumption(amap, "DOS_Threshold", k, v) for k, v in defaults.items()}


def classify_dos(dos, thresholds: dict) -> str:
    if dos is None:
        return "No Sales"
    if dos < thresholds["critical"]:
        return "Critical"
    if dos < thresholds["reorder"]:
        return "Reorder"
    if dos < thresholds["healthy"]:
        return "Healthy"
    if dos < thresholds["watch"]:
        return "Watch"
    return "Overstock"


def load_latest_inventory_map(db: Session, dealers: list, skus: list) -> dict:
    """One query for every relevant InventorySnapshot row, instead of one
    query per (dealer, sku) x scenario. Returns
    {(dealer, sku_code): (sellable_inventory, inbound_quantity, snapshot_date)}
    keeping only the most recent snapshot per (dealer, sku)."""
    if not dealers or not skus:
        return {}
    rows = (
        db.query(InventorySnapshot.dealer, InventorySnapshot.sku_code, InventorySnapshot.snapshot_date,
                  InventorySnapshot.sellable_inventory, InventorySnapshot.inbound_quantity)
        .filter(InventorySnapshot.dealer.in_(dealers), InventorySnapshot.sku_code.in_(skus))
        .all()
    )
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["dealer", "sku_code", "snapshot_date", "sellable_inventory", "inbound_quantity"])
    latest_idx = df.groupby(["dealer", "sku_code"])["snapshot_date"].idxmax()
    latest = df.loc[latest_idx]
    return {
        (r.dealer, r.sku_code): (r.sellable_inventory, r.inbound_quantity, r.snapshot_date)
        for r in latest.itertuples()
    }


def get_latest_inventory(inv_map: dict, dealer: str, sku: str):
    return inv_map.get((dealer, sku), (0.0, 0.0, None))


def round_to_order_multiple(qty: float, moq: int, order_multiple: int) -> float:
    if qty <= 0:
        return 0.0
    rounded = math.ceil(qty / max(order_multiple, 1)) * max(order_multiple, 1)
    return max(rounded, moq)


def compute_supply_plan_row(amap: dict, inv_map: dict, product: Product, dealer: str, forecast_so: float,
                             scenario: str, cutoff_date: date, target_period: str,
                             dos_thresholds: dict) -> dict:
    y, m = map(int, target_period.split("-"))
    days_in_month = monthrange(y, m)[1]

    sellable_inventory, confirmed_inbound, _ = get_latest_inventory(inv_map, dealer, product.sku_code)
    beginning_inventory = sellable_inventory

    scenario_factor = get_scenario_factor(amap, scenario)
    plan_so = round(forecast_so * scenario_factor, 1)
    so_per_day = round(forecast_so / days_in_month, 2) if days_in_month else 0.0

    is_eol = product.product_status in ("EOL", "Discontinued")

    required_ending_inventory = round(so_per_day * product.target_stock_days, 1)
    gross_requirement = plan_so + required_ending_inventory - beginning_inventory - confirmed_inbound
    plan_si = 0.0 if is_eol else round(max(0, gross_requirement), 1)

    dos = round(sellable_inventory / so_per_day, 1) if so_per_day > 0 else None
    weekly_so = so_per_day * 7
    weeks_of_cover = round(sellable_inventory / weekly_so, 2) if weekly_so > 0 else None
    dos_status = classify_dos(dos, dos_thresholds)
    if dos is None and sellable_inventory > 0:
        dos_status = "No Sales"  # Dead Stock Risk flagged separately in Exception Report

    if is_eol:
        suggested_po_raw = 0.0
    else:
        suggested_po_raw = max(
            0.0,
            so_per_day * (product.lead_time + product.target_stock_days + product.safety_stock_days)
            - sellable_inventory - confirmed_inbound,
        )
    suggested_po_rounded = 0.0 if is_eol else round_to_order_multiple(
        suggested_po_raw, product.moq, product.order_multiple)

    reorder_point = round(so_per_day * (product.lead_time + product.safety_stock_days), 1)

    if so_per_day > 0 and dos is not None:
        estimated_stockout_date = cutoff_date + timedelta(days=int(dos))
        latest_po_date = estimated_stockout_date - timedelta(days=product.lead_time)
    else:
        estimated_stockout_date = None
        latest_po_date = None

    if is_eol:
        po_status = "EOL - No PO"
        supply_risk = "EOL"
    elif dos_status == "Overstock":
        po_status = "Overstock"
        supply_risk = "Overstock"
    elif suggested_po_rounded == 0 and dos_status not in ("Critical", "Reorder"):
        po_status = "No Order Required"
        supply_risk = dos_status
    elif latest_po_date and latest_po_date <= cutoff_date:
        po_status = "Order Now"
        supply_risk = dos_status
    elif latest_po_date and latest_po_date <= cutoff_date + timedelta(days=7):
        po_status = "Order Within 7 Days"
        supply_risk = dos_status
    else:
        po_status = "Plan Next Month"
        supply_risk = dos_status

    return dict(
        dealer=dealer, sku_code=product.sku_code, product_status=product.product_status,
        beginning_inventory=beginning_inventory, sellable_inventory=sellable_inventory,
        confirmed_inbound=confirmed_inbound, forecast_so=forecast_so, plan_so=plan_so,
        target_stock_days=product.target_stock_days, required_ending_inventory=required_ending_inventory,
        gross_requirement=round(gross_requirement, 1), plan_si=plan_si, so_per_day=so_per_day,
        dos=dos, weeks_of_cover=weeks_of_cover, dos_status=dos_status,
        suggested_po_raw=round(suggested_po_raw, 1), suggested_po_rounded=suggested_po_rounded,
        reorder_point=reorder_point, estimated_stockout_date=estimated_stockout_date,
        latest_po_date=latest_po_date, po_status=po_status, supply_risk=supply_risk,
    )


def run_supply_plan(db: Session, *, forecast_version_id: int, cutoff_date: date, username: str,
                     scenarios: list = None) -> int:
    """Generates SupplyPlan rows for all scenarios from a Forecast Version's details.
    Returns number of rows created. Replaces any prior SupplyPlan rows for this version."""
    scenarios = scenarios or SCENARIOS
    version = db.get(ForecastVersion, forecast_version_id)
    details = db.query(ForecastDetail).filter_by(version_id=forecast_version_id).all()

    db.execute(delete(SupplyPlan).where(SupplyPlan.version_id == forecast_version_id))

    amap = load_all_assumptions(db)
    dos_thresholds = get_dos_thresholds(amap)
    products = {p.sku_code: p for p in db.query(Product).all()}

    dealers_in_scope = list({d.dealer for d in details})
    skus_in_scope = list({d.sku_code for d in details})
    inv_map = load_latest_inventory_map(db, dealers_in_scope, skus_in_scope)

    count = 0
    base_rows = []
    for scenario in scenarios:
        for d in details:
            product = products.get(d.sku_code)
            if not product:
                continue
            row = compute_supply_plan_row(
                amap, inv_map, product, d.dealer, d.final_forecast_so, scenario, cutoff_date,
                version.data_period, dos_thresholds,
            )
            db.add(SupplyPlan(
                version_id=forecast_version_id, scenario=scenario, data_period=version.data_period,
                created_by=username, **row,
            ))
            count += 1
            if scenario == "Base":
                base_rows.append(row)

    version.total_plan_so = round(sum(r["plan_so"] for r in base_rows), 1)
    version.total_plan_si = round(sum(r["plan_si"] for r in base_rows), 1)
    version.total_suggested_po = round(sum(r["suggested_po_rounded"] for r in base_rows), 1)

    return count
