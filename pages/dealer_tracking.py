import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from datetime import date
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from utils.auth import require_login, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from services.dealer_tracking_service import (
    list_dealers, dealer_period_trend, dealer_breakdown, dealer_sku_breakdown, kpi_totals,
    period_key_to_range,
)
from services.export_service import export_df_to_excel

st.set_page_config(page_title="Dealer Tracking", page_icon="🏬", layout="wide")
require_login()
render_sidebar("dealer_tracking")
user = current_user()

st.title("🏬 Dealer Tracking")

with get_session() as db:
    all_dealers = list_dealers(db)

DEFAULT_DEALERS = [d for d in all_dealers if d in ("CellphoneS", "Minh Tuấn Mobile")]
if not DEFAULT_DEALERS:
    DEFAULT_DEALERS = all_dealers[:1] if all_dealers else []

# --- FILTERS ---
f1, f2, f3, f4 = st.columns([3, 1.4, 1.6, 1.6])
with f1:
    selected_dealers = st.multiselect("Đại lý", all_dealers, default=DEFAULT_DEALERS)
with f2:
    granularity = st.radio("Xem theo", ["week", "month", "year"], index=1, horizontal=True,
                            format_func=lambda g: {"week": "Tuần", "month": "Tháng", "year": "Năm"}[g])
with f3:
    start_date = st.date_input("Từ ngày", value=None, format="DD/MM/YYYY")
with f4:
    end_date = st.date_input("Đến ngày", value=None, format="DD/MM/YYYY")

if not selected_dealers:
    st.info("Chọn ít nhất 1 đại lý để xem dữ liệu.")
    st.stop()

start = start_date if isinstance(start_date, date) else None
end = end_date if isinstance(end_date, date) else None

with get_session() as db:
    trend = dealer_period_trend(db, selected_dealers, granularity, start, end)

if trend.empty:
    st.warning("Không có dữ liệu SI/SO cho lựa chọn hiện tại.")
    st.stop()

period_options = {"Tất cả": None}
for _, row in trend.iterrows():
    period_options[row["period_label"]] = row["period_key"]
selected_period_label = st.selectbox("Xem chi tiết 1 kỳ (breakdown đại lý/SKU)", list(period_options.keys()))
selected_period_key = period_options[selected_period_label]

st.divider()

# --- KPI CARDS ---
kpi = kpi_totals(trend, selected_period_key)
k1, k2, k3, k4 = st.columns(4)
k1.metric("SELL-IN", f"{kpi['si']:,.0f}")
k2.metric("SELL-OUT", f"{kpi['so']:,.0f}")
k3.metric("SELL-OUT / SELL-IN", f"{kpi['ratio']:.1f}%")
k4.metric("CHÊNH LỆCH SI − SO", f"{kpi['delta']:+,.0f}")

st.divider()

# --- TREND CHART ---
gran_label = {"week": "TUẦN", "month": "THÁNG", "year": "NĂM"}[granularity]
st.markdown(f"###### SẢN LƯỢNG THEO {gran_label} — {', '.join(selected_dealers)}")
fig = go.Figure()
fig.add_bar(x=trend["period_label"], y=trend["si"], name="Sell-in", marker_color="#93C5FD")
fig.add_bar(x=trend["period_label"], y=trend["so"], name="Sell-out", marker_color="#1D4ED8")
fig.update_layout(barmode="group", height=380, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
st.plotly_chart(fig, use_container_width=True)

st.divider()

c1, c2 = st.columns(2)

# Nếu chọn 1 kỳ cụ thể, breakdown đại lý/SKU chỉ tính trong đúng kỳ đó;
# nếu chọn "Tất cả" thì dùng nguyên khoảng ngày đã lọc ở trên (nếu có).
if selected_period_key is not None:
    scope_start, scope_end = period_key_to_range(selected_period_key, granularity)
else:
    scope_start, scope_end = start, end

with c1:
    st.markdown("###### THEO ĐẠI LÝ" + ("" if selected_period_label == "Tất cả" else f" — {selected_period_label}"))
    with get_session() as db:
        dbk = dealer_breakdown(db, selected_dealers, scope_start, scope_end)
    if len(selected_dealers) > 1 and not dbk.empty:
        fig2 = go.Figure()
        fig2.add_bar(y=dbk["dealer"], x=dbk["si"], name="Sell-in", orientation="h", marker_color="#93C5FD")
        fig2.add_bar(y=dbk["dealer"], x=dbk["so"], name="Sell-out", orientation="h", marker_color="#1D4ED8")
        fig2.update_layout(barmode="group", height=280, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.caption("Chọn từ 2 đại lý trở lên để so sánh.")

with c2:
    st.markdown("###### TOP SKU (theo Sell-out)")
    with get_session() as db:
        sku_df = dealer_sku_breakdown(db, selected_dealers, scope_start, scope_end, top_n=15)
    if not sku_df.empty:
        fig3 = px.bar(sku_df.sort_values("so"), x="so", y="sku_code", orientation="h",
                      hover_data=["sku_name"], color_discrete_sequence=["#1D4ED8"])
        fig3.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="", xaxis_title="Sell-out")
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.caption("Không có dữ liệu SKU.")

st.divider()
st.markdown("###### Export")
e1, e2 = st.columns(2)
with e1:
    st.download_button(
        "⬇️ Export xu hướng theo kỳ", data=export_df_to_excel(trend, sheet_name="Trend"),
        file_name=f"dealer_trend_{granularity}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
with e2:
    if not sku_df.empty:
        st.download_button(
            "⬇️ Export SKU breakdown", data=export_df_to_excel(sku_df, sheet_name="SKU"),
            file_name="dealer_sku_breakdown.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
