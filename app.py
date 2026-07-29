import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

import streamlit as st
from utils.auth import login, current_user, logout
from database.init_db import create_tables, seed_admin, seed_assumptions, seed_dealers

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


def render_home():
    user = current_user()
    st.title(f"Chào mừng, {user['full_name']} 👋")
    st.caption(f"Vai trò: {user['role']}")
    st.divider()

    st.markdown("#### Bắt đầu nhanh")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.page_link("pages/dashboard.py", label="📊 Xem Management Dashboard", use_container_width=True)
    with c2:
        st.page_link("pages/upload_center.py", label="⬆️ Upload dữ liệu SI/SO/Inventory/PO", use_container_width=True)
    with c3:
        st.page_link("pages/supply_plan.py", label="🚚 Xem Supply Plan", use_container_width=True)

    st.info(
        "Hệ thống hoạt động theo luồng: Upload Raw Data → Data Validation → Save Database → "
        "Historical Analysis → Forecast SO → Plan SO → Plan SI → Suggested PO → Dashboard → "
        "Exception Report → Manual Adjustment → Version Control → Export Excel."
    )


if not current_user():
    render_login()
else:
    render_home()
