import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import io
from datetime import date
import pandas as pd
import streamlit as st

from utils.auth import require_login, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from database.models import ForecastVersion, Product
from services.exception_service import get_all_exceptions

st.set_page_config(page_title="Exception Report", page_icon="⚠️", layout="wide")
require_login()
render_sidebar("exception_report")
user = current_user()

st.title("⚠️ Exception Report")
st.caption("Chỉ hiển thị các vấn đề cần xử lý — không phải toàn bộ dữ liệu.")

with get_session() as db:
    versions = db.query(ForecastVersion).order_by(ForecastVersion.created_at.desc()).all()
    version_options = {f"{v.version_name} — {v.data_period}": v.record_id for v in versions}

col1, col2 = st.columns([2, 1])
with col1:
    if version_options:
        selected = st.selectbox(
            "Forecast/Supply Plan Version (dùng cho check Stockout/Overstock/Accuracy/SO giảm)",
            list(version_options.keys()),
        )
        vid = version_options[selected]
    else:
        st.info("Chưa có Forecast Version — vẫn hiển thị được các exception không phụ thuộc version "
                "(Inbound trễ ETA, NPI thiếu Inventory, EOL còn PO, Inventory âm, Thiếu Mapping, Thiếu Master Data).")
        vid = None
with col2:
    cutoff_date = st.date_input("Ngày đánh giá (cutoff)", value=date.today())

with get_session() as db:
    df = get_all_exceptions(db, version_id=vid, cutoff_date=cutoff_date)
    all_dealers = sorted({d for d in df["dealer"] if d and d != "-"})
    all_skus = {p.sku_code: p for p in db.query(Product).all()}

st.divider()

f1, f2, f3 = st.columns(3)
with f1:
    f_type = st.multiselect("Loại Exception", sorted(df["exception_type"].unique()) if not df.empty else [])
with f2:
    f_dealer = st.multiselect("Dealer", all_dealers)
with f3:
    f_severity = st.multiselect("Mức độ", ["critical", "warning"])

filtered = df.copy()
if f_type:
    filtered = filtered[filtered["exception_type"].isin(f_type)]
if f_dealer:
    filtered = filtered[filtered["dealer"].isin(f_dealer)]
if f_severity:
    filtered = filtered[filtered["severity"].isin(f_severity)]

m1, m2, m3 = st.columns(3)
m1.metric("Tổng số Exception", len(filtered))
m2.metric("🔴 Critical", int((filtered["severity"] == "critical").sum()))
m3.metric("🟡 Warning", int((filtered["severity"] == "warning").sum()))

if filtered.empty:
    st.success("✅ Không có exception nào khớp bộ lọc hiện tại.")
else:
    display_df = filtered.copy()
    display_df["sku_name"] = display_df.apply(
        lambda r: all_skus[r["sku_code"]].sku_name if r["sku_code"] in all_skus else r["sku_name"], axis=1)
    display_df["Mức độ"] = display_df["severity"].map({"critical": "🔴 Critical", "warning": "🟡 Warning"})
    display_df = display_df.rename(columns={
        "exception_type": "Exception Type", "dealer": "Dealer", "sku_code": "SKU Code",
        "sku_name": "SKU Name", "dos": "DOS (Days)", "suggested_po": "Suggested PO",
        "stockout_date": "Stockout Date", "recommended_action": "Recommended Action",
    }).drop(columns=["severity"])
    col_order = ["Mức độ", "Exception Type", "Dealer", "SKU Code", "SKU Name", "DOS (Days)",
                 "Suggested PO", "Stockout Date", "Recommended Action"]
    display_df = display_df[col_order]

    st.dataframe(display_df, use_container_width=True, height=520, hide_index=True)

    buf = io.BytesIO()
    display_df.to_excel(buf, index=False, engine="openpyxl", sheet_name="Exception Report")
    st.download_button(
        "⬇️ Download Exception Report", data=buf.getvalue(),
        file_name=f"exception_report_{cutoff_date}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.divider()
st.markdown("##### Tổng hợp theo loại Exception")
if not df.empty:
    summary = df["exception_type"].value_counts().reset_index()
    summary.columns = ["Exception Type", "Số lượng"]
    st.dataframe(summary, use_container_width=True, hide_index=True)
