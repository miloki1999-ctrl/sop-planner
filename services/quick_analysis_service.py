import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from datetime import date, datetime
import pandas as pd
import streamlit as st

from utils.auth import require_login, require_permission, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from database.models import Product, Dealer, ForecastVersion, ForecastDetail, AuditLog
from services.forecast_service import (
    run_forecast_engine, apply_manual_adjustment, compute_accuracy_for_version, classify_accuracy,
)
from services.export_service import export_df_to_excel

st.set_page_config(page_title="Forecast Engine", page_icon="📈", layout="wide")
require_login()
render_sidebar("forecast_engine")
user = current_user()

st.title("📈 Forecast Engine")
st.caption("Tính Forecast SO theo nhiều phương pháp, kết hợp Growth/Seasonality/Promotion/Coverage, cho phép chỉnh tay.")

tab_run, tab_results, tab_accuracy = st.tabs(["▶️ Chạy Forecast mới", "📋 Kết quả & Chỉnh tay", "🎯 Forecast Accuracy"])

# =============================================================================
# TAB 1 — RUN NEW FORECAST
# =============================================================================
with tab_run:
    require_permission("edit_forecast")
    st.subheader("Tham số chạy Forecast")

    with get_session() as db:
        all_dealers = [d.dealer_name for d in db.query(Dealer).filter_by(is_active=True).all()]
        all_brands = sorted({p.brand for p in db.query(Product.brand).distinct()})
        all_categories = sorted({p.category for p in db.query(Product.category).distinct()})

    c1, c2 = st.columns(2)
    with c1:
        cutoff_date = st.date_input("Data Cutoff Date", value=date(2024, 5, 31))
    with c2:
        target_year = st.number_input("Năm Forecast", min_value=2023, max_value=2030, value=cutoff_date.year)
        target_month = st.number_input("Tháng Forecast", min_value=1, max_value=12,
                                        value=(cutoff_date.month % 12) + 1)
    target_period = f"{int(target_year)}-{int(target_month):02d}"

    method = st.radio(
        "Phương pháp Forecast (Statistical Base)",
        ["Weighted Moving Average", "Average 3M", "Average 6M", "Run-rate"],
        horizontal=True,
        help="EOL / Phase-out / NPI SKU sẽ tự động dùng công thức riêng bất kể chọn phương pháp nào ở đây.",
    )

    st.markdown("##### Bộ lọc phạm vi (bỏ trống = chạy toàn bộ)")
    f1, f2, f3 = st.columns(3)
    with f1:
        dealer_filter = st.multiselect("Dealer", all_dealers)
    with f2:
        brand_filter = st.multiselect("Brand", all_brands)
    with f3:
        category_filter = st.multiselect("Category", all_categories)

    version_name = st.text_input(
        "Tên Version",
        value=f"{target_period} Draft {datetime.now().strftime('%H%M%S')}",
    )

    if st.button("▶️ Chạy Forecast Engine", type="primary"):
        with st.spinner("Đang tính Forecast cho tất cả Dealer × SKU..."):
            with get_session() as db:
                try:
                    version = run_forecast_engine(
                        db, version_name=version_name, target_period=target_period, cutoff_date=cutoff_date,
                        method=method, username=user["username"],
                        dealer_filter=dealer_filter or None, brand_filter=brand_filter or None,
                        category_filter=category_filter or None,
                    )
                    db.add(AuditLog(table_name="forecast_versions", record_ref=version.version_name,
                                     action="INSERT", forecast_version=version.version_name,
                                     performed_by=user["username"], reason="Run Forecast Engine"))
                    st.session_state["last_forecast_version_id"] = version.record_id
                    st.success(
                        f"✅ Đã tạo version **{version.version_name}** — "
                        f"Total Forecast SO: **{version.total_forecast_so:,.0f}**"
                    )
                except Exception as e:
                    # Quan trọng: rollback ngay tại đây. Nếu không, transaction vẫn ở
                    # trạng thái lỗi và lệnh commit tự động khi thoát khỏi "with
                    # get_session()" phía trên sẽ ném ra PendingRollbackError, làm
                    # crash toàn bộ trang thay vì chỉ hiện thông báo lỗi này.
                    db.rollback()
                    st.error(f"Lỗi khi chạy Forecast: {e}")

# =============================================================================
# TAB 2 — RESULTS & MANUAL ADJUSTMENT
# =============================================================================
with tab_results:
    with get_session() as db:
        versions = db.query(ForecastVersion).order_by(ForecastVersion.created_at.desc()).all()
        version_options = {f"{v.version_name} ({v.status}) — {v.data_period}": v.record_id for v in versions}

    if not version_options:
        st.info("Chưa có Forecast Version nào. Hãy chạy Forecast ở tab đầu tiên.")
    else:
        default_idx = 0
        if st.session_state.get("last_forecast_version_id"):
            ids = list(version_options.values())
            if st.session_state["last_forecast_version_id"] in ids:
                default_idx = ids.index(st.session_state["last_forecast_version_id"])

        selected_label = st.selectbox("Chọn Version", list(version_options.keys()), index=default_idx)
        vid = version_options[selected_label]

        with get_session() as db:
            version = db.get(ForecastVersion, vid)
            details = db.query(ForecastDetail).filter_by(version_id=vid).all()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Status", version.status)
            m2.metric("Total Forecast SO", f"{version.total_forecast_so:,.0f}")
            m3.metric("Số dòng", len(details))
            m4.metric("Data Cutoff", version.data_cutoff_date.strftime("%d/%m/%Y") if version.data_cutoff_date else "-")

            st.markdown("##### Bộ lọc kết quả")
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                f_dealer = st.multiselect("Dealer", sorted({d.dealer for d in details}), key="res_dealer")
            with fc2:
                f_brand = st.multiselect("Brand", sorted({d.brand for d in details}), key="res_brand")
            with fc3:
                f_sku = st.text_input("Tìm SKU", key="res_sku")

            rows = []
            for d in details:
                if f_dealer and d.dealer not in f_dealer:
                    continue
                if f_brand and d.brand not in f_brand:
                    continue
                if f_sku and f_sku.upper() not in d.sku_code.upper():
                    continue
                rows.append({
                    "id": d.record_id, "Dealer": d.dealer, "Brand": d.brand, "SKU Code": d.sku_code,
                    "Avg 3M": d.avg_3m, "Avg 6M": d.avg_6m, "Weighted": d.weighted_forecast,
                    "Statistical": d.statistical_forecast, "Growth Factor": d.growth_factor,
                    "Seasonal Factor": d.seasonal_factor, "Promotion Factor": d.promotion_factor,
                    "Coverage Factor": d.coverage_factor, "Manual Adj. Factor": d.manual_adjustment_factor,
                    "Final Forecast SO": d.final_forecast_so, "Method": d.forecast_method,
                    "Comment": d.forecast_comment or "",
                })

            if not rows:
                st.warning("Không có dòng nào khớp bộ lọc.")
            else:
                df = pd.DataFrame(rows)
                st.caption(f"{len(df)} dòng — chỉnh **Manual Adj. Factor** trực tiếp trong bảng rồi bấm Lưu (1.0 = không đổi, 1.1 = +10%, 0.9 = -10%).")
                edited = st.data_editor(
                    df, use_container_width=True, height=420, hide_index=True,
                    disabled=[c for c in df.columns if c not in ("Manual Adj. Factor",)],
                    key=f"editor_{vid}",
                    column_config={"id": None},
                )

                st.download_button(
                    "⬇️ Export Forecast Detail (Excel)",
                    data=export_df_to_excel(df.drop(columns=["id"]), sheet_name="Forecast Detail"),
                    file_name=f"forecast_detail_{version.data_period}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

                changed = edited[edited["Manual Adj. Factor"] != df["Manual Adj. Factor"]]
                if len(changed) > 0:
                    reason = st.text_input("Lý do chỉnh (áp dụng cho tất cả thay đổi ở lần lưu này)",
                                            value="Điều chỉnh theo thực tế thị trường")
                    if st.button(f"💾 Lưu {len(changed)} thay đổi", type="primary"):
                        with get_session() as db:
                            for _, row in changed.iterrows():
                                detail = db.get(ForecastDetail, int(row["id"]))
                                apply_manual_adjustment(db, detail, float(row["Manual Adj. Factor"]), reason, user["username"])
                            v = db.get(ForecastVersion, vid)
                            total_rows = db.query(ForecastDetail).filter_by(version_id=vid).all()
                            v.total_forecast_so = round(sum(d.final_forecast_so for d in total_rows), 1)
                        st.success("Đã lưu Manual Adjustment (tự động ghi vào Audit Log).")
                        st.rerun()

            st.divider()
            colv1, colv2 = st.columns(2)
            with colv1:
                if version.status == "Draft" and st.button("📝 Chuyển sang Revised"):
                    with get_session() as db:
                        v = db.get(ForecastVersion, vid)
                        v.status = "Revised"
                        db.add(AuditLog(table_name="forecast_versions", record_ref=v.version_name,
                                         action="UPDATE", forecast_version=v.version_name,
                                         performed_by=user["username"], reason="Status -> Revised"))
                    st.rerun()
            with colv2:
                if version.status in ("Draft", "Revised") and st.button(
                        "✅ Approve Version", disabled=(user["role"] not in ("Admin", "Planner"))):
                    with get_session() as db:
                        v = db.get(ForecastVersion, vid)
                        v.status = "Approved"
                        db.add(AuditLog(table_name="forecast_versions", record_ref=v.version_name,
                                         action="APPROVE", forecast_version=v.version_name,
                                         performed_by=user["username"]))
                    st.success("Đã Approve version này.")
                    st.rerun()

# =============================================================================
# TAB 3 — FORECAST ACCURACY
# =============================================================================
with tab_accuracy:
    st.subheader("Forecast Accuracy (backtest với dữ liệu SO thực tế đã có)")
    with get_session() as db:
        versions = db.query(ForecastVersion).order_by(ForecastVersion.created_at.desc()).all()
        version_options2 = {f"{v.version_name} — {v.data_period}": v.record_id for v in versions}

    if not version_options2:
        st.info("Chưa có Forecast Version nào.")
    else:
        selected2 = st.selectbox("Chọn Version để đánh giá Accuracy", list(version_options2.keys()), key="acc_version")
        vid2 = version_options2[selected2]

        if st.button("🎯 Tính Forecast Accuracy"):
            with get_session() as db:
                v = db.get(ForecastVersion, vid2)
                acc_df = compute_accuracy_for_version(db, v)

            if acc_df.empty:
                st.warning("Chưa có dữ liệu SO thực tế cho kỳ này để so sánh (SO thực tế phải đã được upload).")
            else:
                mape = (acc_df["abs_error"] / acc_df["actual_so"].replace(0, 1)).mean() * 100
                bias = acc_df["forecast_error"].sum() / acc_df["actual_so"].sum() * 100 if acc_df["actual_so"].sum() else 0
                avg_acc = acc_df["accuracy_pct"].mean()

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Avg Accuracy", f"{avg_acc:.1f}%", classify_accuracy(avg_acc))
                m2.metric("MAPE", f"{mape:.1f}%")
                m3.metric("Bias", f"{bias:+.1f}%")
                m4.metric("Số SKU đánh giá", len(acc_df))

                st.markdown("##### Theo Dealer")
                st.dataframe(
                    acc_df.groupby("dealer").agg(
                        Forecast=("final_forecast_so", "sum"), Actual=("actual_so", "sum"),
                        Accuracy=("accuracy_pct", "mean"),
                    ).round(1), use_container_width=True,
                )
                st.markdown("##### Theo Brand")
                st.dataframe(
                    acc_df.groupby("brand").agg(
                        Forecast=("final_forecast_so", "sum"), Actual=("actual_so", "sum"),
                        Accuracy=("accuracy_pct", "mean"),
                    ).round(1), use_container_width=True,
                )
                st.markdown("##### Chi tiết theo SKU")
                acc_display = acc_df.sort_values("accuracy_pct").round(1)
                st.dataframe(acc_display, use_container_width=True, height=300)

                st.download_button(
                    "⬇️ Export Forecast Accuracy (Excel)",
                    data=export_df_to_excel(acc_display, sheet_name="Forecast Accuracy"),
                    file_name=f"forecast_accuracy_{v.data_period}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
