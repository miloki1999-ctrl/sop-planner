import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from utils.auth import require_login, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from database.models import ForecastVersion, ApprovedPlan, AuditLog
from services.audit_service import get_recent_audit_logs, get_manual_adjustments

st.set_page_config(page_title="Version History", page_icon="🕘", layout="wide")
require_login()
render_sidebar("version_history")
user = current_user()

st.title("🕘 Version History")
st.caption("Không ghi đè phiên bản cũ — mỗi lần Approve/Lock tạo dấu vết riêng, có thể so sánh giữa các version.")

tab_list, tab_compare, tab_audit = st.tabs(["📋 Danh sách Version", "🔍 So sánh 2 Version", "📜 Audit Trail"])

STATUS_COLOR = {"Draft": "🔵", "Revised": "🟠", "Approved": "🟢", "Locked": "⚫"}

# =============================================================================
# TAB 1 — LIST + STATUS ACTIONS
# =============================================================================
with tab_list:
    with get_session() as db:
        versions = db.query(ForecastVersion).order_by(ForecastVersion.created_at.desc()).all()

    if not versions:
        st.info("Chưa có Forecast Version nào. Hãy chạy Forecast Engine trước.")
    else:
        rows = [{
            "Version Name": f"{STATUS_COLOR.get(v.status, '')} {v.version_name}",
            "Data Cutoff": v.data_cutoff_date.strftime("%d/%m/%Y") if v.data_cutoff_date else "-",
            "Created By": v.created_by, "Created Date": v.created_at.strftime("%d/%m/%Y %H:%M") if v.created_at else "-",
            "Status": v.status, "Total Forecast SO": v.total_forecast_so, "Total Plan SO": v.total_plan_so,
            "Total Plan SI": v.total_plan_si, "Total Suggested PO": v.total_suggested_po, "Notes": v.notes or "",
            "id": v.record_id,
        } for v in versions]
        df = pd.DataFrame(rows)
        st.dataframe(df.drop(columns=["id"]), use_container_width=True, height=320, hide_index=True)

        st.divider()
        st.markdown("##### Chuyển trạng thái Version")
        version_map = {f"{v.version_name} ({v.status})": v.record_id for v in versions}
        selected = st.selectbox("Chọn Version", list(version_map.keys()))
        vid = version_map[selected]

        with get_session() as db:
            v = db.get(ForecastVersion, vid)
            notes = st.text_area("Ghi chú", value=v.notes or "")

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                if v.status == "Draft" and st.button("📝 → Revised"):
                    v.status = "Revised"
                    v.notes = notes
                    db.add(AuditLog(table_name="forecast_versions", record_ref=v.version_name, action="UPDATE",
                                     forecast_version=v.version_name, performed_by=user["username"],
                                     reason="Status Draft -> Revised"))
                    st.rerun()
            with c2:
                if v.status in ("Draft", "Revised") and st.button("✅ Approve", disabled=user["role"] not in ("Admin", "Planner")):
                    v.status = "Approved"
                    v.notes = notes
                    db.add(ApprovedPlan(version_id=v.record_id, approved_by=user["username"], status="Approved"))
                    db.add(AuditLog(table_name="forecast_versions", record_ref=v.version_name, action="APPROVE",
                                     forecast_version=v.version_name, performed_by=user["username"]))
                    st.rerun()
            with c3:
                if v.status == "Approved" and st.button("🔒 Lock", disabled=user["role"] != "Admin"):
                    v.status = "Locked"
                    db.add(ApprovedPlan(version_id=v.record_id, approved_by=user["username"], status="Locked"))
                    db.add(AuditLog(table_name="forecast_versions", record_ref=v.version_name, action="LOCK",
                                     forecast_version=v.version_name, performed_by=user["username"]))
                    st.rerun()
            with c4:
                if v.status == "Locked" and st.button("🔓 Unlock", disabled=user["role"] != "Admin"):
                    v.status = "Approved"
                    db.add(ApprovedPlan(version_id=v.record_id, approved_by=user["username"], status="Unlocked"))
                    db.add(AuditLog(table_name="forecast_versions", record_ref=v.version_name, action="UNLOCK",
                                     forecast_version=v.version_name, performed_by=user["username"]))
                    st.rerun()
            if st.button("💾 Lưu ghi chú"):
                v.notes = notes
                st.success("Đã lưu ghi chú.")

        st.caption(
            "Draft: đang tính toán/chỉnh sửa · Revised: đã điều chỉnh · Approved: đã duyệt, dùng cho Supply Plan chính thức · "
            "Locked: khoá hoàn toàn, chỉ Admin unlock được."
        )

# =============================================================================
# TAB 2 — COMPARE
# =============================================================================
with tab_compare:
    with get_session() as db:
        versions2 = db.query(ForecastVersion).order_by(ForecastVersion.created_at.desc()).all()
    if len(versions2) < 2:
        st.info("Cần ít nhất 2 Version để so sánh.")
    else:
        vmap = {f"{v.version_name} ({v.status})": v.record_id for v in versions2}
        c1, c2 = st.columns(2)
        with c1:
            v1_label = st.selectbox("Version A (gốc)", list(vmap.keys()), index=1, key="cmp_a")
        with c2:
            v2_label = st.selectbox("Version B (mới)", list(vmap.keys()), index=0, key="cmp_b")

        with get_session() as db:
            v1 = db.get(ForecastVersion, vmap[v1_label])
            v2 = db.get(ForecastVersion, vmap[v2_label])

        def pct_delta(new, old):
            if not old:
                return "N/A"
            return f"{(new / old - 1) * 100:+.2f}%"

        st.markdown(f"##### Comparison ({v1.version_name} vs {v2.version_name})")
        cols = st.columns(4)
        metrics = [
            ("Total Forecast SO", v1.total_forecast_so, v2.total_forecast_so),
            ("Total Plan SO", v1.total_plan_so, v2.total_plan_so),
            ("Total Plan SI", v1.total_plan_si, v2.total_plan_si),
            ("Total Suggested PO", v1.total_suggested_po, v2.total_suggested_po),
        ]
        for col, (label, old, new) in zip(cols, metrics):
            col.metric(label, f"{new:,.0f}", pct_delta(new, old))

# =============================================================================
# TAB 3 — AUDIT TRAIL
# =============================================================================
with tab_audit:
    st.markdown("##### Audit Log (không thể chỉnh sửa/xoá)")
    with get_session() as db:
        audit_df = get_recent_audit_logs(db, limit=200)
    if audit_df.empty:
        st.caption("Chưa có audit log.")
    else:
        st.dataframe(audit_df, use_container_width=True, height=300, hide_index=True)

    st.markdown("##### Lịch sử Manual Adjustment")
    with get_session() as db:
        adj_df = get_manual_adjustments(db, limit=200)
    if adj_df.empty:
        st.caption("Chưa có manual adjustment nào.")
    else:
        st.dataframe(adj_df, use_container_width=True, height=300, hide_index=True)
