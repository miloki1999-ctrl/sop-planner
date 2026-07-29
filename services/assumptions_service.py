"""
Assumptions service — spec section 18.7 "Assumptions" tab. All values here
are read live by forecast_service.py / supply_service.py via
get_assumption(), so editing here changes engine behavior with no code
change needed.
"""
import pandas as pd
from sqlalchemy.orm import Session
from database.models import Assumption, AuditLog

ASSUMPTION_TYPES = [
    "Growth", "Seasonality", "Promotion", "Scenario", "DOS_Threshold", "WMA_Weight", "NPI", "EOL",
]

TYPE_LABELS = {
    "Growth": "Growth Rate (theo Dealer/Category/Brand/SKU+Dealer/Default)",
    "Seasonality": "Seasonality Index (theo Quý)",
    "Promotion": "Promotion Uplift",
    "Scenario": "Scenario Factor (Plan SO)",
    "DOS_Threshold": "DOS Threshold (ngày)",
    "WMA_Weight": "Weighted Moving Average — trọng số",
    "NPI": "NPI Assumptions (Device Forecast / Attach Rate / Share / Ramp-up)",
    "EOL": "EOL / Phase-out Reduction Rate",
}


def assumptions_df(db: Session, atype: str) -> pd.DataFrame:
    rows = db.query(Assumption).filter_by(assumption_type=atype).order_by(Assumption.scope_key).all()
    return pd.DataFrame([{
        "id": r.record_id, "Scope Key": r.scope_key, "Value": r.value,
        "Updated By": r.updated_by or "", "Updated At": r.updated_at.strftime("%d/%m/%Y %H:%M") if r.updated_at else "",
    } for r in rows])


def upsert_assumption(db: Session, atype: str, scope_key: str, value: float, username: str) -> bool:
    row = db.query(Assumption).filter_by(assumption_type=atype, scope_key=scope_key).first()
    if row:
        old = row.value
        row.value = value
        row.updated_by = username
        db.add(AuditLog(table_name="assumptions", record_ref=f"{atype}:{scope_key}", action="UPDATE",
                         old_value=str(old), new_value=str(value), performed_by=username))
    else:
        db.add(Assumption(assumption_type=atype, scope_key=scope_key, value=value, updated_by=username))
        db.add(AuditLog(table_name="assumptions", record_ref=f"{atype}:{scope_key}", action="INSERT",
                         new_value=str(value), performed_by=username))
    return True


def save_assumption_edits(db: Session, atype: str, original_df: pd.DataFrame, edited_df: pd.DataFrame, username: str) -> int:
    count = 0
    for idx in edited_df.index:
        if edited_df.at[idx, "Value"] != original_df.at[idx, "Value"]:
            row = db.query(Assumption).filter_by(record_id=int(edited_df.at[idx, "id"])).first()
            if row:
                old = row.value
                row.value = float(edited_df.at[idx, "Value"])
                row.updated_by = username
                db.add(AuditLog(table_name="assumptions", record_ref=f"{atype}:{row.scope_key}", action="UPDATE",
                                 old_value=str(old), new_value=str(row.value), performed_by=username))
                count += 1
    return count


def delete_assumption(db: Session, assumption_id: int, username: str):
    row = db.query(Assumption).filter_by(record_id=assumption_id).first()
    if row:
        db.add(AuditLog(table_name="assumptions", record_ref=f"{row.assumption_type}:{row.scope_key}",
                         action="DELETE", old_value=str(row.value), performed_by=username))
        db.delete(row)
