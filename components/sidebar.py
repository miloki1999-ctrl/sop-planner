import streamlit as st
from utils.auth import current_user, logout

NAV_ITEMS = [
    ("app", "Phân tích nhanh", "🔍"),
    ("dashboard", "Dashboard", "📊"),
    ("dealer_tracking", "Dealer Tracking", "🏬"),
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
    # Ẩn menu điều hướng tự động của Streamlit (danh sách file trong pages/)
    # để chỉ còn lại menu tự vẽ bên dưới — tránh bị 2 menu chồng nhau.
    st.markdown(
        "<style>[data-testid='stSidebarNav']{display:none;}</style>",
        unsafe_allow_html=True,
    )
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
            page_path = "app.py" if key == "app" else f"pages/{key}.py"
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
