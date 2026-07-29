import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from utils.auth import require_login, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from database.models import ForecastVersion
from services.dashboard_service import (
    kpi_summary, trend_si_so_forecast, actual_vs_plan_so, top_sku_movers,
    top_dealers, supply_risk_breakdown, inventory_dos_trend,
)
from services.export_service import export_df_to_excel

st.set_page_config(page_title="Management Dashboard", page_icon="📊", layout="wide")
require_login()
render_sidebar("dashboard")
user = current_user()

st.title("📊 Management Dashboard")

with get_session() as db:
    versions = db.query(ForecastVersion).order_by(ForecastVersion.created_at.desc()).all()
    version_options = {f"{v.version_name} ({v.status}) — {v.data_period}": v.record_id for v in versions}

if not version_options:
    st.info("Chưa có Forecast/Supply Plan Version nào. Hãy chạy Forecast Engine + Supply Plan trước để "
            "Dashboard có đủ dữ liệu Forecast/Plan/Suggested PO.")
    st.stop()

col_sel, col_spacer = st.columns([2, 3])
with col_sel:
    selected = st.selectbox("Forecast/Supply Plan Version", list(version_options.keys()))
vid = version_options[selected]

with get_session() as db:
    version = db.get(ForecastVersion, vid)
    period = version.data_period
    kpi = kpi_summary(db, period, vid)

st.caption(f"Kỳ dữ liệu: **{period}** — Version: **{version.version_name}** ({version.status})")
st.divider()

# --- KPI CARDS ---
r1 = st.columns(6)
r1[0].metric("Actual SI (MTD)", f"{kpi['actual_si']:,.0f}")
r1[1].metric("Actual SO (MTD)", f"{kpi['actual_so']:,.0f}")
r1[2].metric("Forecast SO", f"{kpi['forecast_so']:,.0f}")
r1[3].metric("Plan SO", f"{kpi['plan_so']:,.0f}")
r1[4].metric("Plan SI", f"{kpi['plan_si']:,.0f}")
r1[5].metric("Inventory", f"{kpi['inventory']:,.0f}")

r2 = st.columns(6)
r2[0].metric("Inbound (Confirmed)", f"{kpi['inbound']:,.0f}")
r2[1].metric("Suggested PO", f"{kpi['suggested_po']:,.0f}")
r2[2].metric("Average DOS", f"{kpi['avg_dos']:,.0f} ngày")
r2[3].metric("Forecast Accuracy", f"{kpi['forecast_accuracy']:.1f}%" if kpi['forecast_accuracy'] is not None else "N/A")
r2[4].metric("MoM Growth (SO)", f"{kpi['mom_growth']:+.1f}%" if kpi['mom_growth'] is not None else "N/A")
r2[5].metric("YoY Growth (SO)", f"{kpi['yoy_growth']:+.1f}%" if kpi['yoy_growth'] is not None else "N/A")

st.divider()

# --- CHARTS ROW 1 ---
c1, c2, c3 = st.columns(3)

with c1:
    st.markdown("###### SI vs SO vs Forecast (12 tháng)")
    with get_session() as db:
        trend = trend_si_so_forecast(db, period, 12, vid)
    fig = go.Figure()
    fig.add_bar(x=trend["period"], y=trend["si"], name="SI Quantity", marker_color="#93C5FD")
    fig.add_bar(x=trend["period"], y=trend["so"], name="SO Quantity", marker_color="#1D4ED8")
    fig.add_trace(go.Scatter(x=trend["period"], y=trend["forecast"].replace(0, None),
                              name="Forecast SO", mode="markers+lines", line=dict(color="#F59E0B", dash="dot")))
    fig.update_layout(barmode="group", height=320, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.markdown("###### Actual SO vs Plan SO")
    with get_session() as db:
        avp = actual_vs_plan_so(db, vid)
    fig2 = go.Figure(data=[
        go.Bar(name="Actual SO", x=["Kỳ hiện tại"], y=[avp.get("actual", 0)], marker_color="#1D4ED8"),
        go.Bar(name="Plan SO (Base)", x=["Kỳ hiện tại"], y=[avp.get("plan", 0)], marker_color="#F59E0B"),
    ])
    fig2.update_layout(barmode="group", height=320, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
    st.plotly_chart(fig2, use_container_width=True)

with c3:
    st.markdown("###### Inventory & DOS Trend (12 tháng)")
    with get_session() as db:
        invdos = inventory_dos_trend(db, period, 12, vid)
    fig3 = go.Figure()
    fig3.add_bar(x=invdos["period"], y=invdos["inventory"], name="Inventory", marker_color="#93C5FD")
    fig3.add_trace(go.Scatter(x=invdos["period"], y=invdos["dos"], name="DOS (days)",
                               yaxis="y2", mode="lines+markers", line=dict(color="#10B981")))
    fig3.update_layout(
        height=320, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"),
        yaxis2=dict(overlaying="y", side="right", title="DOS"),
    )
    st.plotly_chart(fig3, use_container_width=True)

st.divider()

# --- CHARTS ROW 2 ---
d1, d2, d3 = st.columns(3)

with d1:
    st.markdown("###### Top 10 SKU Tăng trưởng (SO)")
    with get_session() as db:
        gainers, losers = top_sku_movers(db, period)
    if not gainers.empty:
        fig4 = px.bar(gainers, x="mom_growth", y="sku_code", orientation="h", color_discrete_sequence=["#10B981"])
        fig4.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="", xaxis_title="MoM Growth (%)")
        st.plotly_chart(fig4, use_container_width=True)
    else:
        st.caption("Không đủ dữ liệu.")

with d2:
    st.markdown("###### Top 10 SKU Suy giảm (SO)")
    if not losers.empty:
        fig5 = px.bar(losers, x="mom_growth", y="sku_code", orientation="h", color_discrete_sequence=["#EF4444"])
        fig5.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="", xaxis_title="MoM Growth (%)")
        st.plotly_chart(fig5, use_container_width=True)
    else:
        st.caption("Không đủ dữ liệu.")

with d3:
    st.markdown("###### Supply Risk Matrix (theo SKU)")
    with get_session() as db:
        risk = supply_risk_breakdown(db, vid)
    RISK_COLOR = {"Critical": "#EF4444", "Reorder": "#F59E0B", "Healthy": "#10B981",
                  "Watch": "#FACC15", "Overstock": "#9CA3AF", "EOL": "#4B5563", "No Sales": "#D1D5DB"}
    if not risk.empty:
        fig6 = go.Figure(data=[go.Pie(
            labels=risk["risk"], values=risk["count"], hole=0.55,
            marker=dict(colors=[RISK_COLOR.get(r, "#999") for r in risk["risk"]]),
        )])
        fig6.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                            annotations=[dict(text=f"{risk['count'].sum()}<br>Total SKU", x=0.5, y=0.5, showarrow=False)])
        st.plotly_chart(fig6, use_container_width=True)
    else:
        st.caption("Không đủ dữ liệu.")

st.divider()
st.markdown("###### Top Dealer theo SO")
with get_session() as db:
    dealers_df = top_dealers(db, period)
if not dealers_df.empty:
    fig7 = px.bar(dealers_df, x="dealer", y="so_quantity", color_discrete_sequence=["#1D4ED8"])
    fig7.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="", yaxis_title="SO Quantity")
    st.plotly_chart(fig7, use_container_width=True)
else:
    st.caption("Không đủ dữ liệu.")

st.divider()
st.markdown("###### Export báo cáo tổng hợp")
exp1, exp2 = st.columns(2)
with exp1:
    if not dealers_df.empty:
        st.download_button(
            "⬇️ Export Dealer Summary", data=export_df_to_excel(dealers_df, sheet_name="Dealer Summary"),
            file_name=f"dealer_summary_{period}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
with exp2:
    with get_session() as db:
        gainers_all, losers_all = top_sku_movers(db, period, n=1000)
    if not gainers_all.empty:
        sku_summary = pd.concat([gainers_all, losers_all]).drop_duplicates(subset="sku_code")
        st.download_button(
            "⬇️ Export SKU Summary", data=export_df_to_excel(sku_summary, sheet_name="SKU Summary"),
            file_name=f"sku_summary_{period}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
