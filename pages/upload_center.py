import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import io
import pandas as pd
import streamlit as st

from utils.auth import require_login, require_permission, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from database.models import UploadHistory
from services.upload_service import (
    load_workbook, save_master_data, save_transactional, create_upload_history,
    infer_data_period,
)
from services.validation_service import validate_dataframe, compute_file_hash, check_duplicate_file

st.set_page_config(page_title="Upload Center", page_icon="⬆️", layout="wide")
require_login()
require_permission("upload")
render_sidebar("upload_center")
user = current_user()

st.title("⬆️ Upload Center")
st.caption("Upload file Excel (nhiều sheet: MASTER_DATA, RAW_SI, RAW_SO, RAW_INVENTORY, RAW_PO) hoặc từng file CSV riêng lẻ.")

DATA_TYPE_LABELS = {
    "MASTER_DATA": "Master Data (SKU / Product)",
    "RAW_SI": "Sell-In (SI)",
    "RAW_SO": "Sell-Out (SO)",
    "RAW_INVENTORY": "Inventory Snapshot",
    "RAW_PO": "Purchase Order (PO)",
}

# ---------------------------------------------------------------------------
# Step tracker in session_state (UI flow only — no business data stored here)
# ---------------------------------------------------------------------------
if "upload_step" not in st.session_state:
    st.session_state.upload_step = 1
if "upload_parsed" not in st.session_state:
    st.session_state.upload_parsed = None  # dict[data_type] -> df
if "upload_meta" not in st.session_state:
    st.session_state.upload_meta = {}

steps = ["1. Upload File", "2. Validation", "3. Preview & Update Mode", "4. Save"]
cols = st.columns(4)
for i, (col, label) in enumerate(zip(cols, steps), start=1):
    with col:
        if i < st.session_state.upload_step:
            st.success(label)
        elif i == st.session_state.upload_step:
            st.info(label)
        else:
            st.caption(label)

st.divider()

# =============================================================================
# STEP 1 — UPLOAD
# =============================================================================
if st.session_state.upload_step == 1:
    st.subheader("Bước 1 — Upload File")

    period_col1, period_col2 = st.columns(2)
    with period_col1:
        year = st.selectbox("Năm dữ liệu", options=list(range(2023, 2027)), index=2)
    with period_col2:
        month = st.selectbox("Tháng dữ liệu", options=list(range(1, 13)), index=4)
    default_period = f"{year}-{month:02d}"

    uploaded_file = st.file_uploader(
        "Chọn file Excel (.xlsx) hoặc CSV (.csv)",
        type=["xlsx", "csv"],
        help="File Excel có thể chứa nhiều sheet: MASTER_DATA, RAW_SI, RAW_SO, RAW_INVENTORY, RAW_PO.",
    )

    st.markdown("**Hoặc dùng file mẫu có sẵn để test:**")
    if st.button("📂 Dùng Sample Data (SOP_Sample_Upload.xlsx)"):
        sample_path = Path(__file__).resolve().parent.parent / "sample_data" / "SOP_Sample_Upload.xlsx"
        if sample_path.exists():
            file_bytes = sample_path.read_bytes()
            parsed = load_workbook(file_bytes, sample_path.name)
            st.session_state.upload_parsed = parsed
            st.session_state.upload_meta = {
                "file_name": sample_path.name,
                "file_hash": compute_file_hash(file_bytes),
                "data_period": default_period,
            }
            st.session_state.upload_step = 2
            st.rerun()
        else:
            st.error("Chưa có sample data. Hãy chạy `python -m sample_data.generate_sample_data` trước.")

    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        file_hash = compute_file_hash(file_bytes)

        with get_session() as db:
            dup = check_duplicate_file(db, file_hash)
        if dup:
            st.warning(
                f"⚠️ File này đã từng được upload thành công trước đó "
                f"(Upload ID: {dup.upload_id}, ngày {dup.uploaded_at:%d/%m/%Y %H:%M}). "
                f"Bạn có thể tiếp tục nếu vẫn muốn xử lý lại."
            )

        try:
            parsed = load_workbook(file_bytes, uploaded_file.name)
        except Exception as e:
            st.error(f"Không đọc được file: {e}")
            parsed = None

        if parsed:
            if "UNKNOWN" in parsed:
                st.error(
                    "Hệ thống không nhận diện được loại dữ liệu từ tên sheet hoặc cấu trúc cột. "
                    "Vui lòng kiểm tra lại tên sheet (MASTER_DATA / RAW_SI / RAW_SO / RAW_INVENTORY / RAW_PO) "
                    "hoặc tên cột."
                )
                st.dataframe(parsed["UNKNOWN"].head(10), use_container_width=True)
            else:
                detected = ", ".join(DATA_TYPE_LABELS.get(k, k) for k in parsed.keys())
                st.success(f"✅ Đã nhận diện: {detected}")
                if st.button("Tiếp tục →", type="primary"):
                    st.session_state.upload_parsed = parsed
                    st.session_state.upload_meta = {
                        "file_name": uploaded_file.name,
                        "file_hash": file_hash,
                        "data_period": default_period,
                    }
                    st.session_state.upload_step = 2
                    st.rerun()

# =============================================================================
# STEP 2 — VALIDATION
# =============================================================================
elif st.session_state.upload_step == 2:
    st.subheader("Bước 2 — Kiểm tra dữ liệu (Data Validation)")
    parsed = st.session_state.upload_parsed
    meta = st.session_state.upload_meta

    if not parsed:
        st.warning("Chưa có dữ liệu. Quay lại bước 1.")
        st.session_state.upload_step = 1
        st.rerun()

    validation_results = {}
    with get_session() as db:
        for data_type, df in parsed.items():
            validation_results[data_type] = validate_dataframe(data_type, df, db, meta.get("file_hash", ""))

    st.session_state.validation_results_cache = None  # can't pickle easily; recompute each render is fine (fast)

    tabs = st.tabs([DATA_TYPE_LABELS.get(k, k) for k in parsed.keys()])
    for tab, (data_type, vr) in zip(tabs, validation_results.items()):
        with tab:
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Tổng số dòng", vr.total_rows)
            m2.metric("Hợp lệ", vr.valid_rows)
            m3.metric("Lỗi", vr.error_rows)
            m4.metric("Cảnh báo", vr.warning_rows)
            m5.metric("Tỷ lệ hợp lệ", f"{vr.valid_pct}%")

            if vr.errors:
                st.error(f"❌ {len(vr.errors)} lỗi phát hiện — các dòng lỗi sẽ KHÔNG được lưu vào database.")
                err_df = pd.DataFrame(vr.errors)
                st.dataframe(err_df, use_container_width=True, height=200)

                buf = io.BytesIO()
                err_df.to_excel(buf, index=False, engine="openpyxl")
                st.download_button(
                    "⬇️ Tải Error Report", data=buf.getvalue(),
                    file_name=f"error_report_{data_type}_{meta['data_period']}.xlsx",
                    key=f"err_dl_{data_type}",
                )
            else:
                st.success("✅ Không có lỗi cấu trúc/dữ liệu.")

            if vr.warnings:
                st.warning(f"⚠️ {len(vr.warnings)} cảnh báo — các dòng này vẫn được lưu nhưng cần chú ý.")
                st.dataframe(pd.DataFrame(vr.warnings), use_container_width=True, height=150)

    col_back, col_next = st.columns(2)
    with col_back:
        if st.button("← Quay lại"):
            st.session_state.upload_step = 1
            st.rerun()
    with col_next:
        any_valid = any(vr.valid_rows > 0 for vr in validation_results.values())
        if st.button("Tiếp tục → Xem Preview", type="primary", disabled=not any_valid):
            st.session_state.validation_summary = {
                dt: dict(total=vr.total_rows, valid=vr.valid_rows, error=vr.error_rows, warn=vr.warning_rows)
                for dt, vr in validation_results.items()
            }
            st.session_state.upload_step = 3
            st.rerun()

# =============================================================================
# STEP 3 — PREVIEW & UPDATE MODE
# =============================================================================
elif st.session_state.upload_step == 3:
    st.subheader("Bước 3 — Preview & Chọn cách cập nhật")
    parsed = st.session_state.upload_parsed
    meta = st.session_state.upload_meta

    with get_session() as db:
        validation_results = {
            data_type: validate_dataframe(data_type, df, db, meta.get("file_hash", ""))
            for data_type, df in parsed.items()
        }

    update_modes = {}
    for data_type, vr in validation_results.items():
        st.markdown(f"##### {DATA_TYPE_LABELS.get(data_type, data_type)} — {vr.valid_rows} dòng hợp lệ")
        st.dataframe(vr.clean_df.head(20), use_container_width=True, height=220)

        if data_type == "MASTER_DATA":
            mode = st.radio(
                f"Cách cập nhật cho {data_type}", ["Preview only", "Update existing records"],
                horizontal=True, key=f"mode_{data_type}",
            )
        else:
            mode = st.radio(
                f"Cách cập nhật cho {data_type}",
                ["Preview only", "Append", "Replace selected period", "Update existing records"],
                horizontal=True, key=f"mode_{data_type}",
            )
        update_modes[data_type] = mode

        period = infer_data_period(vr.clean_df, data_type) if data_type != "MASTER_DATA" else meta["data_period"]
        st.caption(f"Kỳ dữ liệu tự nhận diện: **{period}**")
        st.divider()

    col_back, col_next = st.columns(2)
    with col_back:
        if st.button("← Quay lại"):
            st.session_state.upload_step = 2
            st.rerun()
    with col_next:
        real_modes = {k: v for k, v in update_modes.items() if v != "Preview only"}
        if st.button("Xác nhận lưu vào Database →", type="primary", disabled=not real_modes):
            st.session_state.upload_update_modes = update_modes
            st.session_state.upload_step = 4
            st.rerun()

# =============================================================================
# STEP 4 — SAVE
# =============================================================================
elif st.session_state.upload_step == 4:
    st.subheader("Bước 4 — Lưu vào Database")
    parsed = st.session_state.upload_parsed
    meta = st.session_state.upload_meta
    update_modes = st.session_state.get("upload_update_modes", {})

    if st.button("🔒 Xác nhận & Lưu", type="primary"):
        results_log = []
        with get_session() as db:
            for data_type, df in parsed.items():
                mode = update_modes.get(data_type, "Preview only")
                vr = validate_dataframe(data_type, df, db, meta.get("file_hash", ""))
                period = infer_data_period(vr.clean_df, data_type) if data_type != "MASTER_DATA" else meta["data_period"]

                if mode == "Preview only" or vr.valid_rows == 0:
                    status = "Skipped" if mode == "Preview only" else "Rejected"
                    create_upload_history(
                        db, file_name=meta["file_name"], file_hash=meta["file_hash"], username=user["username"],
                        data_type=data_type, data_period=period, total_rows=vr.total_rows,
                        valid_rows=vr.valid_rows, error_rows=vr.error_rows, warning_rows=vr.warning_rows,
                        status=status, update_mode=mode, notes="Preview only — not saved" if mode == "Preview only" else "No valid rows",
                    )
                    results_log.append((data_type, status, 0))
                    continue

                upload_id = create_upload_history(
                    db, file_name=meta["file_name"], file_hash=meta["file_hash"], username=user["username"],
                    data_type=data_type, data_period=period, total_rows=vr.total_rows,
                    valid_rows=vr.valid_rows, error_rows=vr.error_rows, warning_rows=vr.warning_rows,
                    status="Saved", update_mode=mode,
                )

                if data_type == "MASTER_DATA":
                    n = save_master_data(db, vr.clean_df, upload_id, user["username"], meta["file_name"])
                else:
                    n = save_transactional(db, data_type, vr.clean_df, upload_id, user["username"],
                                            meta["file_name"], mode, period)
                results_log.append((data_type, "Saved", n))

        st.success("✅ Đã lưu dữ liệu vào database thành công!")
        for dt, status, n in results_log:
            icon = "✅" if status == "Saved" else "⏭️"
            st.write(f"{icon} **{DATA_TYPE_LABELS.get(dt, dt)}**: {status} — {n} dòng")

        st.session_state.upload_step = 1
        st.session_state.upload_parsed = None
        st.session_state.upload_meta = {}
        if st.button("↩️ Upload file khác"):
            st.rerun()
    else:
        st.info("Nhấn nút bên dưới để xác nhận lưu dữ liệu vào database.")
        if st.button("← Quay lại chỉnh update mode"):
            st.session_state.upload_step = 3
            st.rerun()

# =============================================================================
# UPLOAD HISTORY (always visible at bottom)
# =============================================================================
st.divider()
st.subheader("📜 Upload History")
with get_session() as db:
    history = db.query(UploadHistory).order_by(UploadHistory.uploaded_at.desc()).limit(50).all()
    hist_rows = [{
        "Upload ID": h.upload_id, "File": h.file_name, "Loại": h.data_type, "Kỳ": h.data_period,
        "Người upload": h.uploaded_by, "Thời gian": h.uploaded_at.strftime("%d/%m/%Y %H:%M") if h.uploaded_at else "",
        "Tổng dòng": h.total_rows, "Hợp lệ": h.valid_rows, "Lỗi": h.error_rows, "Cảnh báo": h.warning_rows,
        "Trạng thái": h.status, "Cách cập nhật": h.update_mode,
    } for h in history]

if hist_rows:
    st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, height=300)
else:
    st.caption("Chưa có lịch sử upload nào.")
