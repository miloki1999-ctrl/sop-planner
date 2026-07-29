import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from utils.auth import require_login, require_permission, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from database.models import Product, Dealer
from services.assumptions_service import (
    assumptions_df, upsert_assumption, save_assumption_edits, ASSUMPTION_TYPES, TYPE_LABELS,
)

st.set_page_config(page_title="Assumptions", page_icon="⚙️", layout="wide")
require_login()
render_sidebar("assumptions")
user = current_user()

st.title("⚙️ Assumptions")
st.caption("Toàn bộ tham số dùng bởi Forecast Engine & Supply Plan. Sửa ở đây có hiệu lực ngay từ lần chạy Forecast tiếp theo — không cần sửa code.")

tab_objs = st.tabs([TYPE_LABELS[t] for t in ASSUMPTION_TYPES])

SCOPE_HINTS = {
    "Growth": "VD: 'Default', 'Brand:Belkin', 'Category:Charger', 'Dealer:CellphoneS', 'SKU:BEL-CHG-01|Dealer:CellphoneS'",
    "Seasonality": "Q1 / Q2 / Q3 / Q4",
    "Promotion": "'Default_Uplift' hoặc 'SKU:<mã SKU>'",
    "Scenario": "Conservative / Base / Target / Stretch",
    "DOS_Threshold": "critical / reorder / healthy / watch",
    "WMA_Weight": "M-1 / M-2 / M-3 (tổng nên = 1.0)",
    "NPI": "'Default_Ramp_Up_Rate' hoặc 'DeviceForecast:<SKU>', 'AttachRate:<SKU>', 'BrandShare:<SKU>', 'SKUShare:<SKU>', 'RampUp:<SKU>'",
    "EOL": "'Phase_Out_Reduction_Rate'",
}

for tab, atype in zip(tab_objs, ASSUMPTION_TYPES):
    with tab:
        st.caption(f"Scope Key gợi ý: {SCOPE_HINTS[atype]}")
        with get_session() as db:
            df = assumptions_df(db, atype)

        if df.empty:
            st.info("Chưa có dữ liệu cho loại này.")
        else:
            edited = st.data_editor(
                df, use_container_width=True, height=min(400, 60 + 35 * len(df)), hide_index=True,
                key=f"assum_editor_{atype}",
                column_config={"id": None, "Updated By": None, "Updated At": None},
                disabled=["Scope Key"],
            )
            changed = edited[edited["Value"] != df["Value"]]
            if len(changed) > 0 and st.button(f"💾 Lưu thay đổi", key=f"save_{atype}"):
                require_permission("edit_forecast")
                with get_session() as db:
                    n = save_assumption_edits(db, atype, df, edited, user["username"])
                st.success(f"Đã lưu {n} thay đổi.")
                st.rerun()

        with st.expander("➕ Thêm dòng mới"):
            c1, c2 = st.columns(2)
            with c1:
                new_key = st.text_input("Scope Key", key=f"newkey_{atype}")
            with c2:
                new_val = st.number_input("Value", value=0.0, step=0.01, format="%.3f", key=f"newval_{atype}")
            if st.button("Thêm", key=f"add_{atype}"):
                if not new_key:
                    st.error("Cần nhập Scope Key.")
                else:
                    with get_session() as db:
                        upsert_assumption(db, atype, new_key, new_val, user["username"])
                    st.success("Đã thêm.")
                    st.rerun()

        if atype == "NPI":
            st.divider()
            st.markdown("##### Cấu hình nhanh cho SKU đang NPI")
            with get_session() as db:
                npi_skus = [p.sku_code for p in db.query(Product).filter(
                    (Product.product_status == "NPI") | (Product.npi_flag == True)).all()]
            if npi_skus:
                sku_pick = st.selectbox("Chọn SKU NPI", npi_skus)
                c1, c2, c3, c4, c5 = st.columns(5)
                with c1:
                    dev = st.number_input("Device Forecast", min_value=0, value=0, key="dev")
                with c2:
                    attach = st.number_input("Attach Rate", min_value=0.0, max_value=1.0, value=0.0, step=0.01, key="attach")
                with c3:
                    bshare = st.number_input("Brand Share", min_value=0.0, max_value=1.0, value=1.0, step=0.01, key="bshare")
                with c4:
                    sshare = st.number_input("SKU Share", min_value=0.0, max_value=1.0, value=1.0, step=0.01, key="sshare")
                with c5:
                    ramp = st.number_input("Ramp-up Rate", min_value=0.0, max_value=1.0, value=0.6, step=0.01, key="ramp")
                if st.button("Lưu cấu hình NPI cho SKU này"):
                    with get_session() as db:
                        upsert_assumption(db, "NPI", f"DeviceForecast:{sku_pick}", dev, user["username"])
                        upsert_assumption(db, "NPI", f"AttachRate:{sku_pick}", attach, user["username"])
                        upsert_assumption(db, "NPI", f"BrandShare:{sku_pick}", bshare, user["username"])
                        upsert_assumption(db, "NPI", f"SKUShare:{sku_pick}", sshare, user["username"])
                        upsert_assumption(db, "NPI", f"RampUp:{sku_pick}", ramp, user["username"])
                    st.success(f"Đã lưu cấu hình NPI cho {sku_pick}.")
                    st.rerun()
            else:
                st.caption("Hiện không có SKU nào ở trạng thái NPI.")
