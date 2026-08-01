import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.auth import require_login, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from database.models import UploadHistory

st.set_page_config(page_title="Data Quality", page_icon="✅", layout="wide")
require_login()
render_sidebar("data_quality")
user = current_user()

st.title("✅ Data Quality")
st.caption("Theo dõi tỷ lệ dữ liệu hợp lệ qua các lần upload — phát hiện sớm nguồn dữ liệu đang xuống cấp.")

DATA_TYPE_LABELS = {
    "MASTER_DATA": "Master Data",
    "RAW_SI": "Sell In",
    "RAW_SO": "Sell Out",
    "RAW_INVENTORY": "Inventory",
    "RAW_PO": "Purchase Order",
}

with get_session() as db:
    rows = (
        db.query(UploadHistory)
        .order_by(UploadHistory.uploaded_at.asc())
        .all()
    )
    history_df = pd.DataFrame([{
        "upload_id": r.upload_id,
        "file_name": r.file_name,
        "uploaded_by": r.uploaded_by,
        "uploaded_at": r.uploaded_at,
        "data_type": r.data_type,
        "data_period": r.data_period,
        "total_rows": r.total_rows,
        "valid_rows": r.valid_rows,
        "error_rows": r.error_rows,
        "warning_rows": r.warning_rows,
        "status": r.status,
    } for r in rows])

if history_df.empty:
    st.info("Chưa có lịch sử upload nào. Vào **Upload Center** hoặc **Phân tích nhanh** để upload dữ liệu trước.")
    st.stop()

history_df["valid_pct"] = (
    history_df["valid_rows"] / history_df["total_rows"].replace(0, pd.NA) * 100
).round(1)
history_df["data_type_label"] = history_df["data_type"].map(DATA_TYPE_LABELS).fillna(history_df["data_type"])

# ---------------- KPI tổng quan ----------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Tổng số lần upload", len(history_df))
c2.metric("Tổng dòng đã xử lý", f"{int(history_df['total_rows'].sum()):,}")
c3.metric("Tỷ lệ hợp lệ trung bình", f"{history_df['valid_pct'].mean():.1f}%")
c4.metric("Lần upload gần nhất", history_df["uploaded_at"].max().strftime("%d/%m/%Y %H:%M"))

st.divider()

# ---------------- Filter ----------------
type_options = ["Tất cả"] + sorted(history_df["data_type_label"].unique().tolist())
selected_type = st.selectbox("Lọc theo loại dữ liệu", type_options)
filtered = history_df if selected_type == "Tất cả" else history_df[history_df["data_type_label"] == selected_type]

# ---------------- Trend chart ----------------
st.markdown("### 📈 Xu hướng tỷ lệ dữ liệu hợp lệ theo thời gian")
fig = go.Figure()
for dt_label, grp in filtered.groupby("data_type_label"):
    grp = grp.sort_values("uploaded_at")
    fig.add_trace(go.Scatter(
        x=grp["uploaded_at"], y=grp["valid_pct"],
        mode="lines+markers", name=dt_label,
    ))
fig.update_layout(
    height=380, yaxis_title="% dòng hợp lệ", xaxis_title="Thời gian upload",
    margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"),
    yaxis=dict(range=[0, 100]),
)
st.plotly_chart(fig, use_container_width=True)

# ---------------- Error/Warning breakdown ----------------
st.markdown("### ⚠️ Số dòng lỗi / cảnh báo theo loại dữ liệu")
agg = filtered.groupby("data_type_label")[["valid_rows", "error_rows", "warning_rows"]].sum().reset_index()
fig2 = go.Figure()
fig2.add_bar(x=agg["data_type_label"], y=agg["valid_rows"], name="Hợp lệ", marker_color="#10B981")
fig2.add_bar(x=agg["data_type_label"], y=agg["warning_rows"], name="Cảnh báo", marker_color="#F59E0B")
fig2.add_bar(x=agg["data_type_label"], y=agg["error_rows"], name="Lỗi", marker_color="#EF4444")
fig2.update_layout(barmode="stack", height=340, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
st.plotly_chart(fig2, use_container_width=True)

# ---------------- Detail table ----------------
st.markdown("### 📋 Chi tiết từng lần upload")
display_df = filtered.sort_values("uploaded_at", ascending=False)[[
    "uploaded_at", "file_name", "data_type_label", "data_period", "uploaded_by",
    "total_rows", "valid_rows", "warning_rows", "error_rows", "valid_pct", "status",
]].rename(columns={
    "uploaded_at": "Thời gian", "file_name": "File", "data_type_label": "Loại dữ liệu",
    "data_period": "Kỳ dữ liệu", "uploaded_by": "Người upload", "total_rows": "Tổng dòng",
    "valid_rows": "Hợp lệ", "warning_rows": "Cảnh báo", "error_rows": "Lỗi",
    "valid_pct": "% Hợp lệ", "status": "Trạng thái",
})
st.dataframe(display_df, use_container_width=True, height=380)
