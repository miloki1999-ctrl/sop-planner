import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

from datetime import date
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.auth import login, current_user, logout
from database.init_db import create_tables, seed_admin, seed_assumptions, seed_dealers
from database.connection import get_session
from database.models import ForecastVersion, ForecastDetail, SupplyPlan
from services.quick_analysis_service import run_quick_analysis, DATA_TYPE_LABELS
from services.dashboard_service import kpi_summary, trend_si_so_forecast, supply_risk_breakdown
from services.export_service import export_multi_sheet_excel

st.set_page_config(page_title="S&OP Planner", page_icon="📦", layout="wide")

# Ensure DB/tables exist on first run (idempotent)
create_tables()
seed_admin()
seed_assumptions()
seed_dealers()


def render_login():
    st.markdown(
        """
        <div style='text-align:center; padding-top: 60px;'>
            <h1>📦 S&OP Planner</h1>
            <p style='color:#666;'>Sales & Operations Planning — Internal System</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("login_form"):
            st.subheader("Đăng nhập")
            username = st.text_input("Tên đăng nhập")
            password = st.text_input("Mật khẩu", type="password")
            submitted = st.form_submit_button("Đăng nhập", use_container_width=True, type="primary")
            if submitted:
                if login(username, password):
                    st.success("Đăng nhập thành công!")
                    st.rerun()
                else:
                    st.error("Sai tên đăng nhập hoặc mật khẩu, hoặc tài khoản bị khóa.")

        with st.expander("Tài khoản mẫu (demo)"):
            st.code(
                "Admin:   admin / Admin@123\n"
                "Planner: planner1 / Planner@123\n"
                "Viewer:  viewer1 / Viewer@123",
                language="text",
            )


def render_quick_analysis():
    """Default landing flow after login — replaces the old multi-step wizard as
    the primary way to use the app: Upload -> chọn tháng -> Phân tích -> Dashboard -> Export.
    Upload Center / Forecast Engine / Supply Plan pages remain in the sidebar for
    granular control (custom update mode, method, filters, manual adjustment)."""
    user = current_user()

    with st.sidebar:
        st.markdown("### 📦 S&OP Planner")
        st.caption("Sales & Operations Planning")
        st.divider()
        st.markdown(f"**{user['full_name']}**")
        st.caption(user["role"])
        st.divider()
        st.markdown("##### Chế độ chi tiết")
        for key, label, icon in [
            ("dashboard", "Dashboard", "📊"), ("upload_center", "Upload Center", "⬆️"),
            ("forecast_engine", "Forecast Engine", "📈"), ("supply_plan", "Supply Plan", "🚚"),
            ("exception_report", "Exception Report", "⚠️"), ("version_history", "Version History", "🕘"),
            ("data_quality", "Data Quality", "✅"), ("master_data", "Master Data", "🗂️"),
            ("assumptions", "Assumptions", "⚙️"),
        ]:
            st.page_link(f"pages/{key}.py", label=f"{icon} {label}")
        st.divider()
        if st.button("🚪 Đăng xuất", use_container_width=True):
            logout()
            st.rerun()

    st.title("🔍 Phân tích dữ liệu nhanh")
    st.caption("Kéo file Excel vào → Chọn tháng cần Forecast → Nhấn Phân tích dữ liệu → Xem Dashboard → Tải Excel.")

    c1, c2 = st.columns([2, 1])
    with c1:
        uploaded_file = st.file_uploader(
            "📂 Kéo file Excel vào đây (gồm sheet MASTER_DATA, RAW_SI, RAW_SO, RAW_INVENTORY, RAW_PO)",
            type=["xlsx", "csv"],
        )
    with c2:
        today = date.today()
        year = st.selectbox("Năm Forecast", list(range(2023, 2027)), index=min(2, 3))
        month = st.selectbox("Tháng Forecast", list(range(1, 13)), index=today.month - 1)
    target_period = f"{year}-{month:02d}"

    if st.button("📎 Dùng Sample Data để thử nhanh"):
        sample_path = Path(__file__).resolve().parent / "sample_data" / "SOP_Sample_Upload.xlsx"
        if sample_path.exists():
            st.session_state["quick_file_bytes"] = sample_path.read_bytes()
            st.session_state["quick_file_name"] = sample_path.name
        else:
            st.error("Chưa có sample data. Chạy `python -m sample_data.generate_sample_data` trước.")

    if uploaded_file is not None:
        st.session_state["quick_file_bytes"] = uploaded_file.read()
        st.session_state["quick_file_name"] = uploaded_file.name

    file_ready = "quick_file_bytes" in st.session_state
    if file_ready:
        st.caption(f"📄 File sẵn sàng: **{st.session_state['quick_file_name']}**")

    if st.button("🔍 Phân tích dữ liệu", type="primary", disabled=not file_ready, use_container_width=True):
        with st.spinner("Đang Validate → Lưu Database → Chạy Forecast → Chạy Supply Plan..."):
            with get_session() as db:
                result = run_quick_analysis(
                    db, file_bytes=st.session_state["quick_file_bytes"],
                    file_name=st.session_state["quick_file_name"],
                    target_period=target_period, username=user["username"],
                )
        st.session_state["quick_analysis_result"] = result

    result = st.session_state.get("quick_analysis_result")
    if not result:
        st.info("Chưa có kết quả phân tích. Upload file và bấm 'Phân tích dữ liệu' ở trên.")
        return

    st.divider()

    if result.duplicate_file_warning:
        st.warning(f"⚠️ {result.duplicate_file_warning}")

    if not result.success:
        st.error(f"❌ {result.error_message or 'Có lỗi xảy ra trong quá trình phân tích.'}")
        return

    st.success(f"✅ Phân tích hoàn tất — Forecast Version: **{result.forecast_version_name}**")

    with st.expander("📋 Chi tiết Validation (bấm để xem)"):
        for dt, summary in result.validation_summary.items():
            valid_pct = round(100 * summary["valid"] / summary["total"], 1) if summary["total"] else 0
            icon = "✅" if summary["error"] == 0 else "⚠️"
            st.write(f"{icon} **{DATA_TYPE_LABELS.get(dt, dt)}**: {summary['valid']}/{summary['total']} dòng hợp lệ "
                     f"({valid_pct}%) — {summary['error']} lỗi, {summary['warning']} cảnh báo")
            if dt in result.error_reports:
                st.dataframe(result.error_reports[dt], use_container_width=True, height=150)

    # ---- DASHBOARD SECTION ----
    st.markdown("## 📊 Dashboard")
    with get_session() as db:
        version = db.get(ForecastVersion, result.forecast_version_id)
        kpi = kpi_summary(db, target_period, result.forecast_version_id)

    r1 = st.columns(6)
    r1[0].metric("Actual SI (MTD)", f"{kpi['actual_si']:,.0f}")
    r1[1].metric("Actual SO (MTD)", f"{kpi['actual_so']:,.0f}")
    r1[2].metric("Forecast SO", f"{kpi['forecast_so']:,.0f}")
    r1[3].metric("Plan SO", f"{kpi['plan_so']:,.0f}")
    r1[4].metric("Plan SI", f"{kpi['plan_si']:,.0f}")
    r1[5].metric("Suggested PO", f"{kpi['suggested_po']:,.0f}")

    r2 = st.columns(6)
    r2[0].metric("Inventory", f"{kpi['inventory']:,.0f}")
    r2[1].metric("Inbound (Confirmed)", f"{kpi['inbound']:,.0f}")
    r2[2].metric("Average DOS", f"{kpi['avg_dos']:,.0f} ngày")
    r2[3].metric("Forecast Accuracy", f"{kpi['forecast_accuracy']:.1f}%" if kpi['forecast_accuracy'] is not None else "N/A")
    r2[4].metric("MoM Growth (SO)", f"{kpi['mom_growth']:+.1f}%" if kpi['mom_growth'] is not None else "N/A")
    r2[5].metric("YoY Growth (SO)", f"{kpi['yoy_growth']:+.1f}%" if kpi['yoy_growth'] is not None else "N/A")

    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("###### SI vs SO vs Forecast (12 tháng)")
        with get_session() as db:
            trend = trend_si_so_forecast(db, target_period, 12, result.forecast_version_id)
        fig = go.Figure()
        fig.add_bar(x=trend["period"], y=trend["si"], name="SI", marker_color="#93C5FD")
        fig.add_bar(x=trend["period"], y=trend["so"], name="SO", marker_color="#1D4ED8")
        fig.add_trace(go.Scatter(x=trend["period"], y=trend["forecast"].replace(0, None),
                                  name="Forecast SO", mode="markers+lines", line=dict(color="#F59E0B", dash="dot")))
        fig.update_layout(barmode="group", height=320, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
        st.plotly_chart(fig, use_container_width=True)

    with cc2:
        st.markdown("###### Supply Risk Matrix (theo SKU)")
        with get_session() as db:
            risk = supply_risk_breakdown(db, result.forecast_version_id)
        RISK_COLOR = {"Critical": "#EF4444", "Reorder": "#F59E0B", "Healthy": "#10B981",
                      "Watch": "#FACC15", "Overstock": "#9CA3AF", "EOL": "#4B5563", "No Sales": "#D1D5DB"}
        if not risk.empty:
            fig2 = go.Figure(data=[go.Pie(
                labels=risk["risk"], values=risk["count"], hole=0.55,
                marker=dict(colors=[RISK_COLOR.get(r, "#999") for r in risk["risk"]]),
            )])
            fig2.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                                annotations=[dict(text=f"{risk['count'].sum()}<br>SKU", x=0.5, y=0.5, showarrow=False)])
            st.plotly_chart(fig2, use_container_width=True)

    st.caption(
        f"Xem chi tiết đầy đủ ở trang **Dashboard**, **Forecast Engine**, **Supply Plan**, "
        f"**Exception Report** trong sidebar (chọn version **{result.forecast_version_name}**)."
    )

    # ---- EXPORT SECTION ----
    st.divider()
    st.markdown("## ⬇️ Tải file Forecast / Plan về Excel")
    with get_session() as db:
        fc_details = db.query(ForecastDetail).filter_by(version_id=result.forecast_version_id).all()
        sp_rows = db.query(SupplyPlan).filter_by(version_id=result.forecast_version_id, scenario="Base").all()

        forecast_df = pd.DataFrame([{
            "Dealer": d.dealer, "Brand": d.brand, "SKU Code": d.sku_code, "Statistical": d.statistical_forecast,
            "Growth Factor": d.growth_factor, "Seasonal Factor": d.seasonal_factor,
            "Final Forecast SO": d.final_forecast_so, "Method": d.forecast_method,
        } for d in fc_details])

        supply_df = pd.DataFrame([{
            "Dealer": r.dealer, "SKU Code": r.sku_code, "Status": r.product_status,
            "Sellable Inv.": r.sellable_inventory, "Plan SO": r.plan_so, "Plan SI": r.plan_si,
            "Suggested PO": r.suggested_po_rounded, "DOS": r.dos, "Risk": r.supply_risk, "PO Status": r.po_status,
        } for r in sp_rows])

        po_only_df = supply_df[supply_df["Suggested PO"] > 0][["Dealer", "SKU Code", "Suggested PO", "PO Status"]] \
            if not supply_df.empty else supply_df

    excel_bytes = export_multi_sheet_excel({
        "Forecast Detail": forecast_df, "Supply Plan": supply_df, "Suggested PO": po_only_df,
    })
    st.download_button(
        "⬇️ Tải Forecast + Supply Plan + Suggested PO (Excel)", data=excel_bytes,
        file_name=f"sop_report_{target_period}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


if not current_user():
    render_login()
else:
    render_quick_analysis()
