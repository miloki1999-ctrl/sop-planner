"""
Generates a realistic sample dataset per spec section 23:
- 24 months of history
- 5 dealers, 3 brands, 5 categories, 30 SKUs
- SKU statuses: Active, NPI, Slow Moving, EOL
- NPI season in Q3, Black Friday uplift in Q4
- One stockout case, one overstock case, one continuously-declining SKU,
  one dealer with strong growth

Writes a single multi-sheet Excel workbook to sample_data/SOP_Sample_Upload.xlsx
with sheets: MASTER_DATA, RAW_SI, RAW_SO, RAW_INVENTORY, RAW_PO — matching the
exact upload format the Upload Center expects (spec section 4-5).

Usage:
    python -m sample_data.generate_sample_data
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
from datetime import date, timedelta
import pandas as pd
import numpy as np
from dateutil.relativedelta import relativedelta

from config.settings import settings

random.seed(42)
np.random.seed(42)

TODAY = date(2024, 5, 31)
MONTHS = [(TODAY.replace(day=1) - relativedelta(months=i)) for i in range(23, -1, -1)]  # 24 months asc

DEALERS = [
    dict(code="A", name="CellphoneS", group="CellphoneS Group", channel="KA", region="South"),
    dict(code="B", name="Thế Giới Di Động", group="TGDD Group", channel="KA", region="South"),
    dict(code="C", name="Di Động Việt", group="Di Dong Viet", channel="MT", region="South"),
    dict(code="D", name="Minh Tuấn Mobile", group="Minh Tuan Group", channel="MT", region="North"),
    dict(code="E", name="Giá Kho", group="Gia Kho Group", channel="GT", region="North"),
]

BRANDS = ["Belkin", "ESR", "Anker"]
CATEGORIES = ["Charger", "Cable", "Case", "Wireless Power Bank", "Screen Protector"]

CATEGORY_PREFIX = {
    "Charger": "CHG", "Cable": "CAB", "Case": "CASE",
    "Wireless Power Bank": "PB", "Screen Protector": "SP",
}

# Dealer relative volume weights (D = strong growth story)
DEALER_WEIGHT = {"A": 1.3, "B": 1.1, "C": 0.8, "D": 0.7, "E": 0.5}
DEALER_GROWTH_TREND = {"A": 0.01, "B": 0.005, "C": 0.0, "D": 0.035, "E": -0.005}  # monthly trend


def build_master_data():
    rows = []
    sku_idx = 1
    special_flags = {}  # sku_code -> role

    for b_i, brand in enumerate(BRANDS):
        for c_i, cat in enumerate(CATEGORIES):
            n_sku = 2 if (b_i + c_i) % 3 == 0 else 2  # keep to 30 total; adjust below
            for k in range(n_sku):
                sku_code = f"{brand[:3].upper()}-{CATEGORY_PREFIX[cat]}-{sku_idx:02d}"
                sku_idx += 1
                rows.append([sku_code, brand, cat])

    rows = rows[:30]  # exactly 30 SKUs

    # Assign special-case roles
    special_flags[rows[0][0]] = "NPI"           # newest NPI SKU
    special_flags[rows[1][0]] = "NPI"
    special_flags[rows[5][0]] = "SLOW_MOVING"
    special_flags[rows[10][0]] = "EOL"
    special_flags[rows[15][0]] = "DECLINING"     # continuously declining
    special_flags[rows[20][0]] = "STOCKOUT"      # stockout case
    special_flags[rows[25][0]] = "OVERSTOCK"     # overstock case

    master_rows = []
    for i, (sku_code, brand, cat) in enumerate(rows):
        role = special_flags.get(sku_code, "NORMAL")
        launch_date = TODAY - relativedelta(months=random.randint(6, 20))
        eol_date = None
        status = "Active"
        npi_flag = False
        eol_flag = False
        replacement_sku = ""

        if role == "NPI":
            launch_date = TODAY - relativedelta(months=1)
            status = "NPI"
            npi_flag = True
        elif role == "SLOW_MOVING":
            status = "Slow Moving"
        elif role == "EOL":
            status = "EOL"
            eol_flag = True
            eol_date = TODAY - relativedelta(months=1)
            replacement_sku = rows[(i + 3) % len(rows)][0]
        elif role == "DECLINING":
            status = "Active"
        elif role in ("STOCKOUT", "OVERSTOCK"):
            status = "Active"

        unit_cost = round(random.uniform(3, 25), 2)
        dealer_price = round(unit_cost * random.uniform(1.25, 1.45), 2)
        srp = round(dealer_price * random.uniform(1.15, 1.3), 2)

        master_rows.append(dict(
            **{
                "SKU Code": sku_code,
                "SKU Name": f"{brand} {cat} {sku_code.split('-')[-1]}",
                "Brand": brand,
                "Category": cat,
                "Product Group": f"{cat} Group",
                "Product Type": "Accessory",
                "Model Compatibility": "iPhone 15/16/17 Series",
                "Color": random.choice(["Black", "White", "Blue", "Clear"]),
                "Launch Date": launch_date,
                "EOL Date": eol_date,
                "Product Status": status,
                "ABC Classification": random.choice(["A", "A", "B", "B", "C"]),
                "Unit Cost": unit_cost,
                "Dealer Price": dealer_price,
                "SRP": srp,
                "Lead Time": random.choice([30, 35, 40, 45, 60]),
                "MOQ": random.choice([50, 100, 200]),
                "Order Multiple": random.choice([10, 20, 50]),
                "Safety Stock Days": 15,
                "Target Stock Days": random.choice([30, 45, 60]),
                "Default Growth Rate": round(random.uniform(-0.02, 0.08), 3),
                "Seasonality Group": cat,
                "NPI Flag": npi_flag,
                "EOL Flag": eol_flag,
                "Replacement SKU": replacement_sku,
                "Main Dealer": random.choice([d["name"] for d in DEALERS]),
                "Notes": role if role != "NORMAL" else "",
            }
        ))

    df = pd.DataFrame(master_rows)
    return df, special_flags


def seasonal_index(m: date):
    q = (m.month - 1) // 3 + 1
    return {1: 1.10, 2: 1.15, 3: 1.30, 4: 1.35}[q]


def build_transactions(master_df: pd.DataFrame, special_flags: dict):
    si_rows, so_rows, inv_rows, po_rows = [], [], [], []
    inv_state = {}  # (dealer, sku) -> running sellable inventory

    for _, prod in master_df.iterrows():
        sku = prod["SKU Code"]
        role = special_flags.get(sku, "NORMAL")
        base_daily_demand = random.uniform(2, 12)

        for dealer in DEALERS:
            d_code = dealer["code"]
            inv_state[(d_code, sku)] = random.randint(200, 600)

        for m in MONTHS:
            days_in_month = ((m + relativedelta(months=1)) - m).days
            month_str = m.strftime("%Y-%m")
            is_launch_month_or_before = prod["Launch Date"] and m < (
                prod["Launch Date"].to_pydatetime().date() if hasattr(prod["Launch Date"], "to_pydatetime") else prod["Launch Date"]
            )
            if isinstance(prod["Launch Date"], pd.Timestamp):
                launch_d = prod["Launch Date"].date()
            else:
                launch_d = prod["Launch Date"]
            if launch_d and m < launch_d.replace(day=1):
                continue  # no data before launch

            for dealer in DEALERS:
                d_code = dealer["code"]
                weight = DEALER_WEIGHT[d_code]
                trend_months = MONTHS.index(m)
                growth_mult = (1 + DEALER_GROWTH_TREND[d_code]) ** trend_months
                season = seasonal_index(m)

                monthly_so = base_daily_demand * days_in_month * weight * growth_mult * season
                monthly_so *= np.random.normal(1.0, 0.08)

                # Special role adjustments
                if role == "SLOW_MOVING":
                    monthly_so *= 0.15
                elif role == "DECLINING":
                    decline_factor = max(0.1, 1 - trend_months * 0.035)
                    monthly_so *= decline_factor
                elif role == "EOL":
                    if m >= (TODAY - relativedelta(months=1)).replace(day=1):
                        monthly_so *= 0.2
                elif role == "NPI" and m < (TODAY - relativedelta(months=1)).replace(day=1):
                    continue  # NPI has no history before launch
                elif role == "STOCKOUT" and m == MONTHS[-1]:
                    monthly_so *= 1.6  # demand spike depleting inventory
                elif role == "OVERSTOCK":
                    monthly_so *= 0.35  # weak sell-through vs inventory built up

                monthly_so = max(0, round(monthly_so))
                monthly_si = max(0, round(monthly_so * np.random.normal(1.05, 0.1)))

                if monthly_so > 0:
                    so_rows.append(dict(
                        **{
                            "Date": m + relativedelta(day=28),
                            "Dealer": dealer["name"],
                            "Store": f"{dealer['name']} - Store 01",
                            "Region": dealer["region"],
                            "Channel": dealer["channel"],
                            "Brand": prod["Brand"],
                            "Category": prod["Category"],
                            "SKU Code": sku,
                            "SKU Name": prod["SKU Name"],
                            "SO Quantity": monthly_so,
                            "SO Revenue": round(monthly_so * prod["SRP"], 0),
                            "Promotion": "Black Friday" if m.month == 11 else "",
                            "Campaign": "BF2024" if m.month == 11 else "",
                            "NPI Period": "Y" if role == "NPI" else "",
                            "Note": "",
                        }
                    ))

                if monthly_si > 0:
                    si_rows.append(dict(
                        **{
                            "Date": m + relativedelta(day=25),
                            "Dealer": dealer["name"],
                            "Dealer Group": dealer["group"],
                            "Channel": dealer["channel"],
                            "Region": dealer["region"],
                            "Brand": prod["Brand"],
                            "Category": prod["Category"],
                            "SKU Code": sku,
                            "SKU Name": prod["SKU Name"],
                            "SI Quantity": monthly_si,
                            "SI Revenue": round(monthly_si * prod["Dealer Price"], 0),
                            "Unit Price": prod["Dealer Price"],
                            "Promotion": "",
                            "Campaign": "",
                            "PO Number": f"PO-{d_code}-{month_str}-{sku[-2:]}",
                            "Invoice Number": f"INV-{d_code}-{month_str}-{sku[-2:]}-{random.randint(100,999)}",
                        }
                    ))

                # Update running inventory
                prev_inv = inv_state[(d_code, sku)]
                new_inv = prev_inv + monthly_si - monthly_so
                if role == "STOCKOUT" and m == MONTHS[-1]:
                    new_inv = max(0, min(new_inv, 15))
                if role == "OVERSTOCK":
                    new_inv = max(new_inv, prev_inv + monthly_si * 0.6)
                new_inv = max(0, round(new_inv))
                inv_state[(d_code, sku)] = new_inv

                if m == MONTHS[-1]:  # only snapshot latest month per spec preview simplicity
                    reserved = round(new_inv * 0.03)
                    damaged = round(new_inv * 0.01)
                    sellable = max(0, new_inv - reserved - damaged)
                    inbound_qty = round(monthly_si * random.uniform(0.3, 0.6)) if role != "EOL" else 0
                    inbound_eta = m + relativedelta(days=random.randint(5, 20))
                    inv_rows.append(dict(
                        **{
                            "Snapshot Date": TODAY,
                            "Dealer": dealer["name"],
                            "Store hoặc Warehouse": f"{dealer['name']} - WH01",
                            "Brand": prod["Brand"],
                            "Category": prod["Category"],
                            "SKU Code": sku,
                            "SKU Name": prod["SKU Name"],
                            "Available Inventory": new_inv,
                            "Reserved Inventory": reserved,
                            "Damaged Inventory": damaged,
                            "Sellable Inventory": sellable,
                            "Inbound Quantity": inbound_qty,
                            "Inbound ETA": inbound_eta,
                            "Backorder": 0,
                            "Note": role if role != "NORMAL" else "",
                        }
                    ))

                    if role != "EOL" and inbound_qty > 0:
                        po_qty = inbound_qty + random.randint(0, 50)
                        received_qty = inbound_qty
                        po_rows.append(dict(
                            **{
                                "PO Date": TODAY - relativedelta(days=25),
                                "PO Number": f"PO-{d_code}-{sku}-{TODAY.strftime('%Y%m')}",
                                "Supplier": f"{prod['Brand']} Factory",
                                "Brand": prod["Brand"],
                                "SKU Code": sku,
                                "SKU Name": prod["SKU Name"],
                                "PO Quantity": po_qty,
                                "Received Quantity": received_qty,
                                "Outstanding Quantity": po_qty - received_qty,
                                "Expected Arrival Date": inbound_eta,
                                "Actual Arrival Date": None if po_qty > received_qty else inbound_eta,
                                "PO Status": "In Transit" if po_qty > received_qty else "Received",
                                "Unit Cost": prod["Unit Cost"],
                                "Total PO Value": round(po_qty * prod["Unit Cost"], 0),
                                "Note": "",
                            }
                        ))

    return (pd.DataFrame(si_rows), pd.DataFrame(so_rows),
            pd.DataFrame(inv_rows), pd.DataFrame(po_rows))


def generate():
    master_df, special_flags = build_master_data()
    si_df, so_df, inv_df, po_df = build_transactions(master_df, special_flags)

    out_path = settings.SAMPLE_DATA_DIR / "SOP_Sample_Upload.xlsx"
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        master_df.to_excel(writer, sheet_name="MASTER_DATA", index=False)
        si_df.to_excel(writer, sheet_name="RAW_SI", index=False)
        so_df.to_excel(writer, sheet_name="RAW_SO", index=False)
        inv_df.to_excel(writer, sheet_name="RAW_INVENTORY", index=False)
        po_df.to_excel(writer, sheet_name="RAW_PO", index=False)

    print(f"[generate_sample_data] Wrote {out_path}")
    print(f"  MASTER_DATA: {len(master_df)} SKUs")
    print(f"  RAW_SI:      {len(si_df)} rows")
    print(f"  RAW_SO:      {len(so_df)} rows")
    print(f"  RAW_INVENTORY: {len(inv_df)} rows (latest snapshot)")
    print(f"  RAW_PO:      {len(po_df)} rows")
    print(f"  Special-case SKUs: {special_flags}")
    return master_df, si_df, so_df, inv_df, po_df


if __name__ == "__main__":
    generate()
