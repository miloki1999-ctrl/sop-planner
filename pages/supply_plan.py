import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from datetime import date
import pandas as pd
import streamlit as st

from utils.auth import require_login, require_permission, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from database.models import ForecastVersion, SupplyPlan, AuditLog
from services.supply_service import run_supply_plan, SCENARIOS
from services.export_service import export_df_to_excel

st.set_page_config(page_title="Supply Plan", page_icon="🚚", layout="wide")
require_login()
render_sidebar("supply_plan")
user = current_user()

st.title("🚚 Supply Plan")
st.caption("Plan SO theo 4 scenario, Plan SI, DOS/Weeks of Cover, Suggested PO — tính từ Forecast Version đã chọn.")

with get_session() as db:
    versions = (
        db.query(ForecastVersion)
        .filter(ForecastVersion.status.in_(["Draft", "Revised", "Approved", "Locked"]))
        .order_by(ForecastVersion.created_at.desc())
        .all()
    )
    version_options = {f"{v.version_name} ({v.status}) — {v.data_period}": v.record_id for v in versions}

if not version_options:
    st.info("Chưa có Forecast Version nào. Hãy chạy Forecast Engine trước.")
    st.stop()

col1, col2 = st.columns([2, 1])
with col1:
    selected_label = st.selectbox("Chọn Forecast Version làm nguồn Supply Plan", list(version_options.keys()))
    vid = version_options[selected_label]
with col2:
    cutoff_date = st.date_input("Ngày tính toán (cutoff)", value=date.today())

require_permission("edit_forecast")
if st.button("🔄 Tính lại Supply Plan (4 scenario)", type="primary"):
    with st.spinner("Đang tính Plan SO / Plan SI / Suggested PO..."):
        with get_session() as db:
            n = run_supply_plan(db, forecast_version_id=vid, cutoff_date=cutoff_date, username=user["username"])
            db.add(AuditLog(table_name="supply_plan", record_ref=str(vid), action="INSERT",
                             performed_by=user["username"], reason=f"Run Supply Plan — {n} rows"))
        st.success(f"✅ Đã tạo {n} dòng Supply Plan (4 scenario x SKU x Dealer).")
        st.rerun()

st.divider()

with get_session() as db:
    existing_count = db.query(SupplyPlan).filter_by(version_id=vid).count()

if existing_count == 0:
    st.warning("Chưa có Supply Plan cho version này. Bấm nút 'Tính lại Supply Plan' ở trên.")
    st.stop()

scenario = st.radio("Scenario", SCENARIOS, horizontal=True, index=1)

with get_session() as db:
    rows = db.query(SupplyPlan).filter_by(version_id=vid, scenario=scenario).all()

    st.markdown("##### Bộ lọc")
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        f_dealer = st.multiselect("Dealer", sorted({r.dealer for r in rows}))
    with fc2:
        f_status = st.multiselect("Product Status", sorted({r.product_status for r in rows}))
    with fc3:
        f_risk = st.multiselect("Supply Risk", sorted({r.supply_risk for r in rows if r.supply_risk}))
    with fc4:
        f_sku = st.text_input("Tìm SKU")

    filtered = []
    for r in rows:
        if f_dealer and r.dealer not in f_dealer:
            continue
        if f_status and r.product_status not in f_status:
            continue
        if f_risk and r.supply_risk not in f_risk:
            continue
        if f_sku and f_sku.upper() not in r.sku_code.upper():
            continue
        filtered.append(r)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Tổng dòng", len(filtered))
    m2.metric("Plan SO", f"{sum(r.plan_so for r in filtered):,.0f}")
    m3.metric("Plan SI", f"{sum(r.plan_si for r in filtered):,.0f}")
    m4.metric("Suggested PO", f"{sum(r.suggested_po_rounded for r in filtered):,.0f}")
    m5.metric("SKU cần Order Now", sum(1 for r in filtered if r.po_status == "Order Now"))

    RISK_ICON = {"Critical": "🔴", "Reorder": "🟠", "Healthy": "🟢", "Watch": "🟡",
                 "Overstock": "⚪", "EOL": "⚫", "No Sales": "⚪"}

    table_rows = []
    for r in filtered:
        table_rows.append({
            "id": r.record_id, "Dealer": r.dealer, "SKU Code": r.sku_code, "Status": r.product_status,
            "Beginning Inv.": r.beginning_inventory, "Sellable Inv.": r.sellable_inventory,
            "Inbound": r.confirmed_inbound, "Forecast SO": r.forecast_so, "Plan SO": r.plan_so,
            "Target Stock (days)": r.target_stock_days, "Plan SI": r.plan_si,
            "Suggested PO": r.suggested_po_rounded, "DOS": r.dos,
            "Risk": f"{RISK_ICON.get(r.supply_risk, '')} {r.supply_risk}",
            "PO Status": r.po_status,
            "Stockout Date": r.estimated_stockout_date.strftime("%d/%m/%Y") if r.estimated_stockout_date else "-",
            "Latest PO Date": r.latest_po_date.strftime("%d/%m/%Y") if r.latest_po_date else "-",
            "Planner Note": r.planner_note or "",
        })

    if not table_rows:
        st.warning("Không có dòng nào khớp bộ lọc.")
    else:
        df = pd.DataFrame(table_rows)
        st.caption(f"{len(df)} dòng — Scenario: **{scenario}**. Chỉnh **Planner Note** trực tiếp rồi bấm Lưu.")
        edited = st.data_editor(
            df, use_container_width=True, height=460, hide_index=True,
            disabled=[c for c in df.columns if c != "Planner Note"],
            column_config={"id": None},
            key=f"supply_editor_{vid}_{scenario}",
        )

        exp1, exp2 = st.columns(2)
        with exp1:
            st.download_button(
                f"⬇️ Export Supply Plan ({scenario})",
                data=export_df_to_excel(df.drop(columns=["id"]), sheet_name="Supply Plan"),
                file_name=f"supply_plan_{scenario}_{vid}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        with exp2:
            po_only = df[df["Suggested PO"] > 0][["Dealer", "SKU Code", "Suggested PO", "PO Status",
                                                    "Latest PO Date", "Stockout Date"]]
            st.download_button(
                "⬇️ Export Suggested PO only",
                data=export_df_to_excel(po_only, sheet_name="Suggested PO"),
                file_name=f"suggested_po_{scenario}_{vid}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        changed = edited[edited["Planner Note"] != df["Planner Note"]]
        if len(changed) > 0 and st.button(f"💾 Lưu {len(changed)} Planner Note"):
            with get_session() as db2:
                for _, row in changed.iterrows():
                    rec = db2.get(SupplyPlan, int(row["id"]))
                    rec.planner_note = row["Planner Note"]
                db2.add(AuditLog(table_name="supply_plan", record_ref=str(vid), action="UPDATE",
                                  performed_by=user["username"], reason=f"Updated {len(changed)} planner notes"))
            st.success("Đã lưu Planner Note.")
            st.rerun()
