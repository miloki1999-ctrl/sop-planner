import streamlit as st
from utils.auth import current_user, logout

NAV_ITEMS = [
    ("dashboard", "Dashboard", "📊"),
    ("upload_center", "Upload Center", "⬆️"),
    ("forecast_engine", "Forecast Engine", "📈"),
    ("supply_plan", "Supply Plan", "🚚"),
    ("exception_report", "Exception Report", "⚠️"),
    ("version_history", "Version History", "🕘"),
    ("data_quality", "Data Quality", "✅"),
    ("master_data", "Master Data", "🗂️"),
    ("assumptions", "Assumptions", "⚙️"),
]


def render_sidebar(active_key: str):
    with st.sidebar:
        st.markdown("### 📦 S&OP Planner")
        st.caption("Sales & Operations Planning")
        st.divider()

        user = current_user()
        if user:
            st.markdown(f"**{user['full_name']}**")
            st.caption(f"{user['role']}")
        st.divider()

        for key, label, icon in NAV_ITEMS:
            page_path = f"pages/{key}.py"
            if key == active_key:
                st.markdown(f"**{icon} {label}**  ← đang xem")
            else:
                try:
                    st.page_link(page_path, label=f"{icon} {label}")
                except Exception:
                    pass

        st.divider()
        if user and st.button("🚪 Đăng xuất", use_container_width=True):
            logout()
            st.rerun()
