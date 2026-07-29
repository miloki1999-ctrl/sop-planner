import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from datetime import date
import pandas as pd
import plotly.express as px
import streamlit as st

from utils.auth import require_login, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from database.models import UploadHistory, RawSI, RawSO, Dealer, Product
from services.export_service import export_df_to_excel

st.set_page_config(page_title="Data Quality", page_icon="✅", layout="wide")
require_login()
render_sidebar("data_quality")
user = current_user()

st.title("✅ Data Quality")

tab_raw, tab_upload = st.tabs(["🔍 Xem chi tiết SI/SO", "📈 Chất lượng Upload"])

# =============================================================================
# TAB 1 — RAW SI/SO VIEWER
# =============================================================================
with tab_raw:
    st.caption("Xem trực tiếp dữ liệu SI/SO đã lưu trong database — lọc theo Dealer/SKU/khoảng ngày.")

    data_type = st.radio("Loại dữ liệu", ["Sell-In (SI)", "Sell-Out (SO)"], horizontal=True)
    model = RawSI if data_type == "Sell-In (SI)" else RawSO

    with get_session() as db:
        all_dealers = sorted({d.dealer_name for d in db.query(Dealer).all()})
        all_brands = sorted({p.brand for p in db.query(Product.brand).distinct()})
        min_date = db.query(model.txn_date).order_by(model.txn_date.asc()).first()
        max_date = db.query(model.txn_date).order_by(model.txn_date.desc()).first()

    if not min_date:
        st.info(f"Chưa có dữ liệu {data_type} nào trong database. Hãy Upload trước ở Upload Center.")
    else:
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            f_dealer = st.multiselect("Dealer", all_dealers, key="raw_dealer")
        with f2:
            f_brand = st.multiselect("Brand", all_brands, key="raw_brand")
        with f3:
            f_sku = st.text_input("Tìm SKU", key="raw_sku")
        with f4:
            date_range = st.date_input(
                "Khoảng ngày", value=(min_date[0], max_date[0]),
                min_value=min_date[0], max_value=max_date[0], key="raw_date",
            )

        with get_session() as db:
            q = db.query(model)
            if f_dealer:
                q = q.filter(model.dealer.in_(f_dealer))
            if f_brand:
                q = q.filter(model.brand.in_(f_brand))
            if f_sku:
                q = q.filter(model.sku_code.ilike(f"%{f_sku.upper()}%"))
            if isinstance(date_range, tuple) and len(date_range) == 2:
                q = q.filter(model.txn_date >= date_range[0], model.txn_date <= date_range[1])
            rows = q.order_by(model.txn_date.desc()).limit(5000).all()

        if not rows:
            st.warning("Không có dòng nào khớp bộ lọc.")
        else:
            if data_type == "Sell-In (SI)":
                table = pd.DataFrame([{
                    "Date": r.txn_date, "Dealer": r.dealer, "Channel": r.channel, "Region": r.region,
                    "Brand": r.brand, "Category": r.category, "SKU Code": r.sku_code, "SKU Name": r.sku_name,
                    "SI Quantity": r.si_quantity, "SI Revenue": r.si_revenue, "Unit Price": r.unit_price,
                    "PO Number": r.po_number, "Invoice Number": r.invoice_number,
                    "Data Period": r.data_period, "Source File": r.source_file,
                } for r in rows])
            else:
                table = pd.DataFrame([{
                    "Date": r.txn_date, "Dealer": r.dealer, "Store": r.store, "Channel": r.channel,
                    "Region": r.region, "Brand": r.brand, "Category": r.category, "SKU Code": r.sku_code,
                    "SKU Name": r.sku_name, "SO Quantity": r.so_quantity, "SO Revenue": r.so_revenue,
                    "Promotion": r.promotion, "Campaign": r.campaign,
                    "Data Period": r.data_period, "Source File": r.source_file,
                } for r in rows])

            m1, m2, m3 = st.columns(3)
            m1.metric("Số dòng hiển thị", f"{len(table):,}" + (" (giới hạn 5000)" if len(rows) == 5000 else ""))
            qty_col = "SI Quantity" if data_type == "Sell-In (SI)" else "SO Quantity"
            m2.metric(f"Tổng {qty_col}", f"{table[qty_col].sum():,.0f}")
            rev_col = "SI Revenue" if data_type == "Sell-In (SI)" else "SO Revenue"
            m3.metric(f"Tổng {rev_col}", f"{table[rev_col].sum():,.0f}")

            st.dataframe(table, use_container_width=True, height=480, hide_index=True)

            st.download_button(
                f"⬇️ Export {data_type} (Excel)",
                data=export_df_to_excel(table, sheet_name=data_type[:20]),
                file_name=f"{'raw_si' if data_type.startswith('Sell-In') else 'raw_so'}_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

# =============================================================================
# TAB 2 — UPLOAD QUALITY TRENDS (existing content)
# =============================================================================
with tab_upload:
    st.caption("Theo dõi chất lượng dữ liệu qua các lần upload — dựa trên toàn bộ Upload History.")

    with get_session() as db:
        history = db.query(UploadHistory).order_by(UploadHistory.uploaded_at.desc()).all()

    if not history:
        st.info("Chưa có lịch sử upload nào.")
    else:
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
            f_type = st.multiselect("Loại dữ liệu", sorted(df["Loại dữ liệu"].unique()), key="uh_type")
        with f2:
            f_user = st.multiselect("Người upload", sorted(df["Người upload"].unique()), key="uh_user")
        with f3:
            f_status = st.multiselect("Trạng thái", sorted(df["Trạng thái"].unique()), key="uh_status")

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
