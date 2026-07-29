import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.express as px
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
st.caption("Theo dõi chất lượng dữ liệu qua các lần upload — dựa trên toàn bộ Upload History.")

with get_session() as db:
    history = db.query(UploadHistory).order_by(UploadHistory.uploaded_at.desc()).all()

if not history:
    st.info("Chưa có lịch sử upload nào.")
    st.stop()

df = pd.DataFrame([{
    "Upload ID": h.upload_id, "File": h.file_name, "Loại dữ liệu": h.data_type, "Kỳ": h.data_period,
    "Người upload": h.uploaded_by, "Thời gian": h.uploaded_at, "Tổng dòng": h.total_rows,
    "Hợp lệ": h.valid_rows, "Lỗi": h.error_rows, "Cảnh báo": h.warning_rows,
    "Tỷ lệ hợp lệ (%)": round(100 * h.valid_rows / h.total_rows, 1) if h.total_rows else 0,
    "Trạng thái": h.status, "Cách cập nhật": h.update_mode,
} for h in history])

m1, m2, m3, m4 = st.columns(4)
m1.metric("Tổng số lần upload", len(df))
m2.metric("Tổng dòng đã xử lý", f"{df['Tổng dòng'].sum():,.0f}")
m3.metric("Tỷ lệ hợp lệ trung bình", f"{df['Tỷ lệ hợp lệ (%)'].mean():.1f}%")
m4.metric("Tổng số dòng lỗi", f"{df['Lỗi'].sum():,.0f}")

st.divider()

c1, c2 = st.columns(2)
with c1:
    st.markdown("###### Tỷ lệ hợp lệ theo thời gian")
    trend_df = df.sort_values("Thời gian")
    fig = px.line(trend_df, x="Thời gian", y="Tỷ lệ hợp lệ (%)", color="Loại dữ liệu", markers=True)
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.markdown("###### Số dòng lỗi/cảnh báo theo loại dữ liệu")
    agg = df.groupby("Loại dữ liệu")[["Lỗi", "Cảnh báo"]].sum().reset_index()
    fig2 = px.bar(agg, x="Loại dữ liệu", y=["Lỗi", "Cảnh báo"], barmode="group",
                   color_discrete_map={"Lỗi": "#EF4444", "Cảnh báo": "#F59E0B"})
    fig2.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig2, use_container_width=True)

st.divider()
st.markdown("###### Chi tiết Upload History")

f1, f2, f3 = st.columns(3)
with f1:
    f_type = st.multiselect("Loại dữ liệu", sorted(df["Loại dữ liệu"].unique()))
with f2:
    f_user = st.multiselect("Người upload", sorted(df["Người upload"].unique()))
with f3:
    f_status = st.multiselect("Trạng thái", sorted(df["Trạng thái"].unique()))

filtered = df.copy()
if f_type:
    filtered = filtered[filtered["Loại dữ liệu"].isin(f_type)]
if f_user:
    filtered = filtered[filtered["Người upload"].isin(f_user)]
if f_status:
    filtered = filtered[filtered["Trạng thái"].isin(f_status)]

display = filtered.drop(columns=["Thời gian"]).copy()
display.insert(5, "Thời gian", filtered["Thời gian"].dt.strftime("%d/%m/%Y %H:%M"))
st.dataframe(display, use_container_width=True, height=400, hide_index=True)
