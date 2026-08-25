import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from datetime import date
import pandas as pd
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


# --- Helpers: format số kiểu Việt Nam (252.588 thay vì 252,588) ---
def format_vn(value, signed: bool = False) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    fmt = "{:+,.0f}" if signed else "{:,.0f}"
    return fmt.format(value).replace(",", ".")


def format_pct(value, decimals: int = 1, signed: bool = False):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    fmt = f"{{:+.{decimals}f}}%" if signed else f"{{:.{decimals}f}}%"
    return fmt.format(value)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_dealer_list():
    with get_session() as db:
        return list_dealers(db)


all_dealers = _cached_dealer_list()
DEFAULT_DEALERS = [d for d in all_dealers if d in ("CellphoneS", "Minh Tuấn Mobile")]
if not DEFAULT_DEALERS:
    DEFAULT_DEALERS = all_dealers[:1] if all_dealers else []

FILTER_DEFAULTS = {
    "dt_dealers": DEFAULT_DEALERS,
    "dt_granularity": "month",
    "dt_start": None,
    "dt_end": None,
    "dt_top_n": 10,
}
for _k, _v in FILTER_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ============================== BỘ LỌC ==============================
with st.container(border=True):
    st.markdown("###### 🔎 Bộ lọc")

    r1c1, r1c2 = st.columns([3, 2])
    with r1c1:
        selected_dealers = st.multiselect("Đại lý", all_dealers, key="dt_dealers")
    with r1c2:
        granularity = st.radio(
            "Xem theo", ["week", "month", "year"], horizontal=True,
            format_func=lambda g: {"week": "Tuần", "month": "Tháng", "year": "Năm"}[g],
            key="dt_granularity",
        )

    r2c1, r2c2, r2c3 = st.columns(3)
    with r2c1:
        start_date = st.date_input("Từ ngày", format="DD/MM/YYYY", key="dt_start")
    with r2c2:
        end_date = st.date_input("Đến ngày", format="DD/MM/YYYY", key="dt_end")
    with r2c3:
        top_n = st.selectbox("Top SKU", [5, 10, 15], key="dt_top_n")

    start = start_date if isinstance(start_date, date) else None
    end = end_date if isinstance(end_date, date) else None

    if start and end and start > end:
        st.error("⚠️ 'Từ ngày' đang lớn hơn 'Đến ngày' — vui lòng chọn lại khoảng ngày hợp lệ.")
        st.stop()

    if not selected_dealers:
        st.info("Chọn ít nhất 1 đại lý để xem dữ liệu.")
        st.stop()

    with get_session() as db:
        trend = dealer_period_trend(db, selected_dealers, granularity, start, end)

    if trend.empty:
        st.warning("Không có dữ liệu SI/SO cho lựa chọn hiện tại.")
        st.stop()

    period_options = {"Tất cả": None}
    for _, row in trend.iterrows():
        period_options[row["period_label"]] = row["period_key"]
    selected_period_label = st.selectbox(
        "Xem chi tiết 1 kỳ (breakdown đại lý/SKU)", list(period_options.keys()),
    )
    selected_period_key = period_options[selected_period_label]

    if st.button("↺ Đặt lại bộ lọc"):
        for _k, _v in FILTER_DEFAULTS.items():
            st.session_state[_k] = _v
        st.rerun()

# ============================== KPI ==============================
st.divider()

kpi = kpi_totals(trend, selected_period_key)

delta_si_pct = delta_so_pct = None
if selected_period_key is not None:
    idx_matches = trend.index[trend["period_key"] == selected_period_key].tolist()
    if idx_matches and idx_matches[0] > 0:
        prev_row = trend.iloc[idx_matches[0] - 1]
        if prev_row["si"] > 0:
            delta_si_pct = (kpi["si"] - prev_row["si"]) / prev_row["si"] * 100
        if prev_row["so"] > 0:
            delta_so_pct = (kpi["so"] - prev_row["so"]) / prev_row["so"] * 100

k1, k2, k3, k4 = st.columns(4)
k1.metric("Sell-in", format_vn(kpi["si"]), format_pct(delta_si_pct, signed=True))
k2.metric("Sell-out", format_vn(kpi["so"]), format_pct(delta_so_pct, signed=True))
k3.metric("Sell-out / Sell-in", f"{kpi['ratio']:.1f}%")
k4.metric("Chênh lệch SI − SO", format_vn(kpi["delta"], signed=True))

if selected_period_key is None:
    st.caption("Đang xem toàn bộ giai đoạn — chọn 1 kỳ cụ thể ở bộ lọc để so sánh % tăng/giảm so với kỳ liền trước.")

# ============================== BIỂU ĐỒ XU HƯỚNG ==============================
st.divider()
gran_label = {"week": "tuần", "month": "tháng", "year": "năm"}[granularity]
st.markdown(f"###### Sản lượng theo {gran_label} — {', '.join(selected_dealers)}")

fig = go.Figure()
fig.add_bar(
    x=trend["period_label"], y=trend["si"], name="Sell-in", marker_color="#93C5FD",
    hovertemplate="%{x}<br>Sell-in: %{y:,.0f}<extra></extra>",
)
fig.add_bar(
    x=trend["period_label"], y=trend["so"], name="Sell-out", marker_color="#2563EB",
    hovertemplate="%{x}<br>Sell-out: %{y:,.0f}<extra></extra>",
)
fig.update_layout(
    barmode="group", height=370, margin=dict(l=10, r=10, t=10, b=10),
    legend=dict(orientation="h"),
    xaxis=dict(tickangle=-45 if len(trend) > 8 else 0),
    separators=",.",
)
st.plotly_chart(fig, use_container_width=True)

# ============================== KHU VỰC PHÂN TÍCH ==============================
st.divider()

# Breakdown lấy phần GIAO NHAU giữa khoảng ngày người dùng chọn và khoảng ngày
# của kỳ đang xem — không được bỏ qua khoảng ngày ban đầu khi chọn 1 kỳ cụ thể.
if selected_period_key is not None:
    period_start, period_end = period_key_to_range(selected_period_key, granularity)
    scope_start = max(d for d in [period_start, start] if d is not None)
    scope_end = min(d for d in [period_end, end] if d is not None)
else:
    scope_start, scope_end = start, end

with get_session() as db:
    if len(selected_dealers) > 1:
        analysis_df = dealer_breakdown(db, selected_dealers, scope_start, scope_end)
    else:
        analysis_df = dealer_sku_breakdown(db, selected_dealers, scope_start, scope_end,
                                            top_n=top_n, sort_by="abs_delta")
    sku_df = dealer_sku_breakdown(db, selected_dealers, scope_start, scope_end,
                                   top_n=top_n, sort_by="so")

period_suffix = "" if selected_period_label == "Tất cả" else f" — {selected_period_label}"

c1, c2 = st.columns(2)

with c1:
    if len(selected_dealers) > 1:
        st.markdown(f"###### Hiệu quả theo đại lý{period_suffix}")
        if not analysis_df.empty:
            fig2 = go.Figure()
            fig2.add_bar(y=analysis_df["dealer"], x=analysis_df["si"], name="Sell-in",
                         orientation="h", marker_color="#93C5FD",
                         hovertemplate="%{y}<br>Sell-in: %{x:,.0f}<extra></extra>")
            fig2.add_bar(y=analysis_df["dealer"], x=analysis_df["so"], name="Sell-out",
                         orientation="h", marker_color="#2563EB",
                         hovertemplate="%{y}<br>Sell-out: %{x:,.0f}<extra></extra>")
            fig2.update_layout(barmode="group", height=300, margin=dict(l=10, r=10, t=10, b=10),
                                legend=dict(orientation="h"), separators=",.")
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.caption("Không có dữ liệu trong phạm vi đã chọn.")
    else:
        st.markdown(f"###### SKU chênh lệch SI–SO lớn nhất{period_suffix}")
        if not analysis_df.empty:
            fig2b = go.Figure()
            fig2b.add_bar(
                y=analysis_df["sku_code"], x=analysis_df["delta"], orientation="h",
                marker_color="#2563EB", customdata=analysis_df[["sku_name", "si", "so"]],
                hovertemplate="<b>%{customdata[0]}</b><br>Sell-in: %{customdata[1]:,.0f}<br>"
                              "Sell-out: %{customdata[2]:,.0f}<br>Chênh lệch: %{x:,.0f}<extra></extra>",
            )
            fig2b.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                                 yaxis_title="", xaxis_title="Chênh lệch SI − SO", separators=",.")
            st.plotly_chart(fig2b, use_container_width=True)
        else:
            st.caption("Không có dữ liệu trong phạm vi đã chọn.")

with c2:
    st.markdown(f"###### Top {top_n} SKU theo Sell-out{period_suffix}")
    if not sku_df.empty:
        sku_sorted = sku_df.sort_values("so")
        fig3 = go.Figure()
        fig3.add_bar(
            y=sku_sorted["sku_code"], x=sku_sorted["so"], orientation="h",
            marker_color="#2563EB", customdata=sku_sorted[["sku_name", "si", "delta"]],
            hovertemplate="<b>%{customdata[0]}</b><br>Sell-out: %{x:,.0f}<br>"
                          "Sell-in: %{customdata[1]:,.0f}<br>Chênh lệch: %{customdata[2]:,.0f}<extra></extra>",
        )
        fig3.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                            yaxis_title="", xaxis_title="Sell-out", separators=",.")
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.caption("Không có dữ liệu SKU.")

# ============================== BẢNG SKU CHI TIẾT ==============================
st.divider()
st.markdown(f"###### Bảng chi tiết Top {top_n} SKU{period_suffix}")

if not sku_df.empty:
    table_df = sku_df.copy()
    table_df["ratio_pct"] = table_df.apply(
        lambda r: (r["so"] / r["si"] * 100) if r["si"] > 0 else None, axis=1
    )
    display_df = pd.DataFrame({
        "SKU": table_df["sku_code"],
        "Tên sản phẩm": table_df["sku_name"],
        "Sell-in": table_df["si"],
        "Sell-out": table_df["so"],
        "SO/SI (%)": table_df["ratio_pct"],
        "Chênh lệch SI-SO": table_df["delta"],
    })

    def _ratio_color(v):
        if pd.isna(v):
            return "background-color: #374151; color: #d1d5db"
        if v < 60:
            return "background-color: #fecaca; color: #7f1d1d"
        if v < 85:
            return "background-color: #fde68a; color: #78350f"
        return "background-color: #bbf7d0; color: #14532d"

    styled = (
        display_df.style
        .format({
            "Sell-in": format_vn,
            "Sell-out": format_vn,
            "Chênh lệch SI-SO": lambda v: format_vn(v, signed=True),
            "SO/SI (%)": lambda v: "—" if pd.isna(v) else f"{v:.1f}%",
        })
        .map(_ratio_color, subset=["SO/SI (%)"])
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.caption("Bấm vào tiêu đề cột để sắp xếp bảng.")
else:
    st.caption("Không có dữ liệu SKU.")

# ============================== EXPORT ==============================
st.divider()
st.markdown("###### Export")
e1, e2 = st.columns(2)
with e1:
    export_trend_df = trend.rename(columns={
        "period_label": "Kỳ", "si": "Sell-in", "so": "Sell-out",
    })[["Kỳ", "Sell-in", "Sell-out"]]
    st.download_button(
        "⬇️ Export xu hướng theo kỳ", data=export_df_to_excel(export_trend_df, sheet_name="Trend"),
        file_name=f"dealer_trend_{granularity}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
with e2:
    if not sku_df.empty:
        export_sku_df = sku_df.rename(columns={
            "sku_code": "SKU", "sku_name": "Tên sản phẩm", "si": "Sell-in",
            "so": "Sell-out", "delta": "Chênh lệch SI-SO",
        })
        st.download_button(
            "⬇️ Export SKU breakdown", data=export_df_to_excel(export_sku_df, sheet_name="SKU"),
            file_name="dealer_sku_breakdown.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.caption("Không có dữ liệu SKU để export.")
