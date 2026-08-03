"""
Authentication helpers built on the shared `users` table. Streamlit
session_state is used ONLY to hold the current-request identity (who is
logged in) — never as a data store. All real data still lives in the DB.
"""
import streamlit as st
from database.connection import get_session
from database.models import User, AuditLog
from utils.security import verify_password

ROLE_PERMISSIONS = {
    "Admin": {"upload", "edit_master", "delete", "approve", "unlock", "manage_users", "export", "edit_forecast"},
    "Planner": {"upload", "edit_forecast", "export"},
    "Viewer": {"export_allowed"},
}


def login(username: str, password: str) -> bool:
    with get_session() as db:
        user = db.query(User).filter_by(username=username, is_active=True).first()
        if not user or not verify_password(password, user.password_hash):
            return False
        st.session_state["auth_user"] = {
            "record_id": user.record_id, "username": user.username,
            "full_name": user.full_name, "role": user.role,
        }
        db.add(AuditLog(table_name="users", record_ref=username, action="LOGIN", performed_by=username))
        return True


def logout():
    st.session_state.pop("auth_user", None)


def current_user() -> dict | None:
    return st.session_state.get("auth_user")


def require_login():
    if not current_user():
        st.warning("Vui lòng đăng nhập để tiếp tục.")
        st.page_link("app.py", label="🔑 Quay lại trang đăng nhập")
        st.stop()


def has_permission(action: str) -> bool:
    user = current_user()
    if not user:
        return False
    return action in ROLE_PERMISSIONS.get(user["role"], set())


def require_permission(action: str):
    if not has_permission(action):
        st.error("Bạn không có quyền thực hiện thao tác này.")
        st.stop()
