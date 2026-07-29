"""
Audit service — spec section 20. Read-only query helpers over `audit_logs`
and `manual_adjustments`. No update/delete function exists here on purpose:
audit trail must not be editable by Planners (or anyone) per spec.
"""
import pandas as pd
from sqlalchemy.orm import Session
from database.models import AuditLog, ManualAdjustment


def get_recent_audit_logs(db: Session, limit: int = 100, table_name: str = None,
                           performed_by: str = None) -> pd.DataFrame:
    q = db.query(AuditLog).order_by(AuditLog.performed_at.desc())
    if table_name:
        q = q.filter(AuditLog.table_name == table_name)
    if performed_by:
        q = q.filter(AuditLog.performed_by == performed_by)
    rows = q.limit(limit).all()
    return pd.DataFrame([{
        "Thời gian": r.performed_at.strftime("%d/%m/%Y %H:%M") if r.performed_at else "",
        "Bảng": r.table_name, "Hành động": r.action, "Tham chiếu": r.record_ref,
        "Forecast Version": r.forecast_version, "Người thực hiện": r.performed_by,
        "Lý do": r.reason,
    } for r in rows])


def get_manual_adjustments(db: Session, version_id: int = None, limit: int = 200) -> pd.DataFrame:
    q = db.query(ManualAdjustment).order_by(ManualAdjustment.adjusted_at.desc())
    if version_id:
        q = q.filter(ManualAdjustment.version_id == version_id)
    rows = q.limit(limit).all()
    return pd.DataFrame([{
        "Thời gian": r.adjusted_at.strftime("%d/%m/%Y %H:%M") if r.adjusted_at else "",
        "Dealer": r.dealer, "SKU Code": r.sku_code, "Trường": r.field_name,
        "Giá trị cũ": r.old_value, "Giá trị mới": r.new_value, "Lý do": r.reason,
        "Người chỉnh": r.adjusted_by, "Trạng thái lưu": r.save_status,
    } for r in rows])
