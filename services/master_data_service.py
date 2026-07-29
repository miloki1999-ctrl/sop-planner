"""
Master Data service — spec section 18.7 (Product Master, Dealer Mapping)
plus User Management (spec section 21, folded into this page rather than
a separate nav item to keep the sidebar lean).
"""
import pandas as pd
from sqlalchemy.orm import Session
from database.models import Product, Dealer, User, AuditLog
from utils.security import hash_password

PRODUCT_STATUSES = ["NPI", "Active", "Seasonal", "Slow Moving", "Phase-out", "EOL", "Discontinued"]


def products_df(db: Session) -> pd.DataFrame:
    products = db.query(Product).order_by(Product.sku_code).all()
    return pd.DataFrame([{
        "id": p.record_id, "SKU Code": p.sku_code, "SKU Name": p.sku_name, "Brand": p.brand,
        "Category": p.category, "Product Status": p.product_status, "ABC": p.abc_classification,
        "Unit Cost": p.unit_cost, "Dealer Price": p.dealer_price, "SRP": p.srp,
        "Lead Time": p.lead_time, "MOQ": p.moq, "Order Multiple": p.order_multiple,
        "Safety Stock Days": p.safety_stock_days, "Target Stock Days": p.target_stock_days,
        "Default Growth Rate": p.default_growth_rate, "NPI Flag": p.npi_flag, "EOL Flag": p.eol_flag,
        "Replacement SKU": p.replacement_sku or "", "Main Dealer": p.main_dealer or "",
    } for p in products])


def save_product_edits(db: Session, original_df: pd.DataFrame, edited_df: pd.DataFrame, username: str) -> int:
    changed_count = 0
    editable_cols = [
        "Product Status", "Lead Time", "MOQ", "Order Multiple", "Safety Stock Days",
        "Target Stock Days", "Default Growth Rate", "Unit Cost", "Dealer Price", "SRP",
    ]
    field_map = {
        "Product Status": "product_status", "Lead Time": "lead_time", "MOQ": "moq",
        "Order Multiple": "order_multiple", "Safety Stock Days": "safety_stock_days",
        "Target Stock Days": "target_stock_days", "Default Growth Rate": "default_growth_rate",
        "Unit Cost": "unit_cost", "Dealer Price": "dealer_price", "SRP": "srp",
    }
    for idx in edited_df.index:
        row_changed = False
        for col in editable_cols:
            if edited_df.at[idx, col] != original_df.at[idx, col]:
                row_changed = True
                break
        if not row_changed:
            continue
        product = db.query(Product).filter_by(record_id=int(edited_df.at[idx, "id"])).first()
        if not product:
            continue
        for col in editable_cols:
            old_val = original_df.at[idx, col]
            new_val = edited_df.at[idx, col]
            if new_val != old_val:
                setattr(product, field_map[col], new_val)
                db.add(AuditLog(table_name="products", record_ref=product.sku_code, field_name=field_map[col],
                                 old_value=str(old_val), new_value=str(new_val), action="UPDATE",
                                 performed_by=username))
        changed_count += 1
    return changed_count


def dealers_df(db: Session) -> pd.DataFrame:
    dealers = db.query(Dealer).order_by(Dealer.dealer_code).all()
    return pd.DataFrame([{
        "id": d.record_id, "Dealer Code": d.dealer_code, "Dealer Name": d.dealer_name,
        "Dealer Group": d.dealer_group or "", "Channel": d.channel or "", "Region": d.region or "",
        "Active": d.is_active,
    } for d in dealers])


def add_dealer(db: Session, code, name, group, channel, region, username):
    existing = db.query(Dealer).filter_by(dealer_code=code).first()
    if existing:
        return False, "Dealer Code đã tồn tại."
    db.add(Dealer(dealer_code=code, dealer_name=name, dealer_group=group, channel=channel,
                   region=region, is_active=True, created_by=username))
    db.add(AuditLog(table_name="dealers", record_ref=code, action="INSERT", performed_by=username))
    return True, "Đã thêm Dealer."


def users_df(db: Session) -> pd.DataFrame:
    users = db.query(User).order_by(User.username).all()
    return pd.DataFrame([{
        "id": u.record_id, "Username": u.username, "Full Name": u.full_name, "Role": u.role,
        "Email": u.email or "", "Active": u.is_active,
        "Last Login": u.last_login.strftime("%d/%m/%Y %H:%M") if u.last_login else "",
    } for u in users])


def create_user(db: Session, username, password, full_name, role, email, created_by):
    existing = db.query(User).filter_by(username=username).first()
    if existing:
        return False, "Username đã tồn tại."
    db.add(User(username=username, password_hash=hash_password(password), full_name=full_name,
                role=role, email=email, is_active=True))
    db.add(AuditLog(table_name="users", record_ref=username, action="INSERT", performed_by=created_by))
    return True, "Đã tạo user."


def toggle_user_active(db: Session, user_id: int, active: bool, performed_by: str):
    u = db.query(User).filter_by(record_id=user_id).first()
    if u:
        u.is_active = active
        db.add(AuditLog(table_name="users", record_ref=u.username, action="UPDATE",
                         reason=f"is_active -> {active}", performed_by=performed_by))


def reset_password(db: Session, user_id: int, new_password: str, performed_by: str):
    u = db.query(User).filter_by(record_id=user_id).first()
    if u:
        u.password_hash = hash_password(new_password)
        db.add(AuditLog(table_name="users", record_ref=u.username, action="UPDATE",
                         reason="Password reset", performed_by=performed_by))
