import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from utils.auth import require_login, current_user
from components.sidebar import render_sidebar
from database.connection import get_session
from services.master_data_service import (
    products_df, save_product_edits, dealers_df, add_dealer, users_df,
    create_user, toggle_user_active, reset_password, PRODUCT_STATUSES,
)

st.set_page_config(page_title="Master Data", page_icon="🗂️", layout="wide")
require_login()
render_sidebar("master_data")
user = current_user()

st.title("🗂️ Master Data")

tabs = ["📦 Product Master", "🏬 Dealer Mapping"]
if user["role"] == "Admin":
    tabs.append("👤 User Management")
tab_objs = st.tabs(tabs)

# =============================================================================
# PRODUCT MASTER
# =============================================================================
with tab_objs[0]:
    st.caption("Chỉnh trực tiếp Product Status / Lead Time / MOQ / Safety Stock / Target Stock / Growth Rate / Giá — tự lưu vào Audit Log.")
    with get_session() as db:
        pdf = products_df(db)

    if pdf.empty:
        st.info("Chưa có Master Data. Upload qua Upload Center trước.")
    else:
        f1, f2, f3 = st.columns(3)
        with f1:
            f_brand = st.multiselect("Brand", sorted(pdf["Brand"].unique()))
        with f2:
            f_status = st.multiselect("Product Status", sorted(pdf["Product Status"].unique()))
        with f3:
            f_sku = st.text_input("Tìm SKU")

        view_df = pdf.copy()
        if f_brand:
            view_df = view_df[view_df["Brand"].isin(f_brand)]
        if f_status:
            view_df = view_df[view_df["Product Status"].isin(f_status)]
        if f_sku:
            view_df = view_df[view_df["SKU Code"].str.upper().str.contains(f_sku.upper())]

        edited = st.data_editor(
            view_df, use_container_width=True, height=460, hide_index=True,
            key="product_editor",
            column_config={
                "id": None,
                "Product Status": st.column_config.SelectboxColumn(options=PRODUCT_STATUSES),
            },
            disabled=["SKU Code", "SKU Name", "Brand", "Category", "ABC", "NPI Flag", "EOL Flag",
                      "Replacement SKU", "Main Dealer"],
        )

        if st.button("💾 Lưu thay đổi Master Data", type="primary"):
            with get_session() as db:
                n = save_product_edits(db, view_df.reset_index(drop=True), edited.reset_index(drop=True), user["username"])
            st.success(f"Đã lưu {n} SKU thay đổi.")
            st.rerun()

# =============================================================================
# DEALER MAPPING
# =============================================================================
with tab_objs[1]:
    with get_session() as db:
        ddf = dealers_df(db)
    st.dataframe(ddf.drop(columns=["id"]), use_container_width=True, hide_index=True)

    with st.expander("➕ Thêm Dealer mới"):
        c1, c2, c3 = st.columns(3)
        with c1:
            new_code = st.text_input("Dealer Code")
            new_name = st.text_input("Dealer Name")
        with c2:
            new_group = st.text_input("Dealer Group")
            new_channel = st.selectbox("Channel", ["KA", "MT", "GT"])
        with c3:
            new_region = st.text_input("Region")
        if st.button("Thêm Dealer"):
            if not new_code or not new_name:
                st.error("Cần nhập Dealer Code và Dealer Name.")
            else:
                with get_session() as db:
                    ok, msg = add_dealer(db, new_code, new_name, new_group, new_channel, new_region, user["username"])
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

# =============================================================================
# USER MANAGEMENT (Admin only)
# =============================================================================
if user["role"] == "Admin":
    with tab_objs[2]:
        with get_session() as db:
            udf = users_df(db)
        st.dataframe(udf.drop(columns=["id"]), use_container_width=True, hide_index=True)

        c1, c2 = st.columns(2)
        with c1:
            with st.expander("➕ Tạo user mới"):
                nu = st.text_input("Username", key="nu")
                np_ = st.text_input("Password", type="password", key="np")
                nf = st.text_input("Full Name", key="nf")
                nr = st.selectbox("Role", ["Admin", "Planner", "Viewer"], key="nr")
                ne = st.text_input("Email", key="ne")
                if st.button("Tạo user"):
                    with get_session() as db:
                        ok, msg = create_user(db, nu, np_, nf, nr, ne, user["username"])
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
        with c2:
            with st.expander("🔑 Reset mật khẩu / Khoá user"):
                if not udf.empty:
                    target = st.selectbox("Chọn user", udf["Username"].tolist())
                    target_id = int(udf[udf["Username"] == target]["id"].iloc[0])
                    new_pw = st.text_input("Mật khẩu mới", type="password", key="reset_pw")
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        if st.button("Reset mật khẩu") and new_pw:
                            with get_session() as db:
                                reset_password(db, target_id, new_pw, user["username"])
                            st.success("Đã reset mật khẩu.")
                    with cc2:
                        cur_active = bool(udf[udf["Username"] == target]["Active"].iloc[0])
                        label = "🚫 Khoá user" if cur_active else "✅ Mở khoá"
                        if st.button(label):
                            with get_session() as db:
                                toggle_user_active(db, target_id, not cur_active, user["username"])
                            st.rerun()
