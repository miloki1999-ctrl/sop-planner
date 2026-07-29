"""
Validation service — implements every check in spec section 6 ("Data
Validation"). Runs BEFORE anything is written to the database. Rows that
fail hard checks are excluded from save; rows that only trigger warnings
are still saved but flagged for the user.

Public entrypoint: `validate_dataframe(data_type, df, db_session)` ->
ValidationResult (see dataclass below), consumed by pages/upload_center.py.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func

from database.models import Product, Dealer, RawSO, UploadHistory

# ---------------------------------------------------------------------------
# Expected schema per sheet — used for missing-column / unknown-column checks
# ---------------------------------------------------------------------------
REQUIRED_COLUMNS = {
    "MASTER_DATA": [
        "SKU Code", "SKU Name", "Brand", "Category", "Product Group", "Product Type",
        "Model Compatibility", "Color", "Launch Date", "EOL Date", "Product Status",
        "ABC Classification", "Unit Cost", "Dealer Price", "SRP", "Lead Time", "MOQ",
        "Order Multiple", "Safety Stock Days", "Target Stock Days", "Default Growth Rate",
        "Seasonality Group", "NPI Flag", "EOL Flag", "Replacement SKU", "Main Dealer", "Notes",
    ],
    "RAW_SI": [
        "Date", "Dealer", "Dealer Group", "Channel", "Region", "Brand", "Category",
        "SKU Code", "SKU Name", "SI Quantity", "SI Revenue", "Unit Price", "Promotion",
        "Campaign", "PO Number", "Invoice Number",
    ],
    "RAW_SO": [
        "Date", "Dealer", "Store", "Region", "Channel", "Brand", "Category", "SKU Code",
        "SKU Name", "SO Quantity", "SO Revenue", "Promotion", "Campaign", "NPI Period", "Note",
    ],
    "RAW_INVENTORY": [
        "Snapshot Date", "Dealer", "Store hoặc Warehouse", "Brand", "Category", "SKU Code",
        "SKU Name", "Available Inventory", "Reserved Inventory", "Damaged Inventory",
        "Sellable Inventory", "Inbound Quantity", "Inbound ETA", "Backorder", "Note",
    ],
    "RAW_PO": [
        "PO Date", "PO Number", "Supplier", "Brand", "SKU Code", "SKU Name", "PO Quantity",
        "Received Quantity", "Outstanding Quantity", "Expected Arrival Date",
        "Actual Arrival Date", "PO Status", "Unit Cost", "Total PO Value", "Note",
    ],
}

# Columns that must never be negative
NON_NEGATIVE_COLUMNS = {
    "RAW_SI": ["SI Quantity", "SI Revenue"],
    "RAW_SO": ["SO Quantity", "SO Revenue"],
    "RAW_INVENTORY": ["Available Inventory", "Reserved Inventory", "Damaged Inventory", "Sellable Inventory"],
    "RAW_PO": ["PO Quantity", "Received Quantity", "Outstanding Quantity"],
}

DATE_COLUMNS = {
    "MASTER_DATA": ["Launch Date", "EOL Date"],
    "RAW_SI": ["Date"],
    "RAW_SO": ["Date"],
    "RAW_INVENTORY": ["Snapshot Date", "Inbound ETA"],
    "RAW_PO": ["PO Date", "Expected Arrival Date", "Actual Arrival Date"],
}

DUP_KEYS = {
    "RAW_SI": ["Date", "Dealer", "SKU Code", "Invoice Number"],
    "RAW_SO": ["Date", "Dealer", "Store", "SKU Code"],
    "RAW_INVENTORY": ["Snapshot Date", "Dealer", "Store hoặc Warehouse", "SKU Code"],
    "RAW_PO": ["PO Number", "SKU Code"],
}


@dataclass
class ValidationResult:
    data_type: str
    total_rows: int = 0
    valid_rows: int = 0
    error_rows: int = 0
    warning_rows: int = 0
    errors: list = field(default_factory=list)        # list[dict(row, column, message)]
    warnings: list = field(default_factory=list)       # list[dict(row, column, message)]
    clean_df: pd.DataFrame = None                       # rows with zero hard errors
    error_df: pd.DataFrame = None                        # full rows that have >=1 hard error
    file_hash: str = ""

    @property
    def valid_pct(self) -> float:
        return round(100 * self.valid_rows / self.total_rows, 2) if self.total_rows else 0.0

    def add_error(self, row_idx, column, message):
        self.errors.append({"row": row_idx + 2, "column": column, "message": message})  # +2 = header + 1-index

    def add_warning(self, row_idx, column, message):
        self.warnings.append({"row": row_idx + 2, "column": column, "message": message})


def compute_file_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def check_duplicate_file(db: Session, file_hash: str):
    """Spec: 'File đã từng upload' check."""
    existing = db.query(UploadHistory).filter_by(file_hash=file_hash, status="Saved").first()
    return existing


def _to_date(val):
    if pd.isna(val) or val in ("", None):
        return None
    if isinstance(val, (date, datetime)):
        return val.date() if isinstance(val, datetime) else val
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return "INVALID"


def validate_dataframe(data_type: str, df: pd.DataFrame, db: Session, file_hash: str = "") -> ValidationResult:
    result = ValidationResult(data_type=data_type, file_hash=file_hash)
    result.total_rows = len(df)

    required = REQUIRED_COLUMNS.get(data_type, [])
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        # Hard stop — can't validate row-by-row without required columns
        for c in missing_cols:
            result.add_error(-2, c, f"Thiếu cột bắt buộc: '{c}'")
        result.error_rows = result.total_rows
        result.clean_df = df.iloc[0:0]
        result.error_df = df
        return result

    df = df.copy()
    df["_row_valid"] = True
    df["_row_warn"] = False

    # --- 1. Data type checks: dates ---
    for col in DATE_COLUMNS.get(data_type, []):
        if col not in df.columns:
            continue
        for idx, val in df[col].items():
            parsed = _to_date(val)
            if parsed == "INVALID":
                result.add_error(idx, col, f"Ngày không hợp lệ: '{val}'")
                df.at[idx, "_row_valid"] = False

    # --- 2. Non-negative checks ---
    for col in NON_NEGATIVE_COLUMNS.get(data_type, []):
        if col not in df.columns:
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        bad = numeric < 0
        for idx in df[bad.fillna(False)].index:
            label = "Quantity âm" if "Quantity" in col else ("Revenue âm" if "Revenue" in col else f"{col} âm")
            result.add_error(idx, col, label)
            df.at[idx, "_row_valid"] = False
        nan_mask = numeric.isna() & df[col].notna()
        for idx in df[nan_mask].index:
            result.add_error(idx, col, f"Sai kiểu dữ liệu (không phải số): '{df.at[idx, col]}'")
            df.at[idx, "_row_valid"] = False

    # --- 3. Duplicate rows within file (by natural key) ---
    dup_keys = DUP_KEYS.get(data_type)
    if dup_keys and all(k in df.columns for k in dup_keys):
        dup_mask = df.duplicated(subset=dup_keys, keep=False)
        for idx in df[dup_mask].index:
            result.add_error(idx, "+".join(dup_keys), "Dữ liệu trùng trong file (trùng khóa)")
            df.at[idx, "_row_valid"] = False

    # --- 4. SKU must exist in Master Data (for transactional sheets) ---
    if data_type in ("RAW_SI", "RAW_SO", "RAW_INVENTORY", "RAW_PO") and "SKU Code" in df.columns:
        known_skus = {r[0] for r in db.query(Product.sku_code).all()}
        for idx, sku in df["SKU Code"].items():
            if pd.isna(sku) or str(sku).strip() == "":
                result.add_error(idx, "SKU Code", "Thiếu SKU Code")
                df.at[idx, "_row_valid"] = False
            elif known_skus and str(sku).strip() not in known_skus:
                result.add_error(idx, "SKU Code", f"SKU '{sku}' không có trong Master Data")
                df.at[idx, "_row_valid"] = False

    # --- 5. Dealer must be mapped (for transactional sheets with Dealer col) ---
    if data_type in ("RAW_SI", "RAW_SO", "RAW_INVENTORY") and "Dealer" in df.columns:
        known_dealers = {r[0] for r in db.query(Dealer.dealer_name).all()}
        for idx, dealer in df["Dealer"].items():
            if pd.isna(dealer) or str(dealer).strip() == "":
                result.add_error(idx, "Dealer", "Thiếu Dealer")
                df.at[idx, "_row_valid"] = False
            elif known_dealers and str(dealer).strip() not in known_dealers:
                result.add_warning(idx, "Dealer", f"Dealer '{dealer}' chưa được mapping")
                df.at[idx, "_row_warn"] = True

    # --- 6. PO Date > ETA check ---
    if data_type == "RAW_PO" and {"PO Date", "Expected Arrival Date"}.issubset(df.columns):
        for idx, row in df.iterrows():
            po_d = _to_date(row["PO Date"])
            eta_d = _to_date(row["Expected Arrival Date"])
            if isinstance(po_d, date) and isinstance(eta_d, date) and po_d > eta_d:
                result.add_error(idx, "PO Date", "PO Date lớn hơn Expected Arrival Date (ETA)")
                df.at[idx, "_row_valid"] = False

    # --- 7. EOL SKU still has PO ---
    if data_type == "RAW_PO" and "SKU Code" in df.columns:
        eol_skus = {r[0] for r in db.query(Product.sku_code).filter(
            Product.product_status.in_(["EOL", "Discontinued"])).all()}
        for idx, sku in df["SKU Code"].items():
            if str(sku).strip() in eol_skus:
                result.add_warning(idx, "SKU Code", f"SKU '{sku}' đã EOL nhưng vẫn có PO")
                df.at[idx, "_row_warn"] = True

    # --- 8. SO tăng bất thường > 200% so với tháng trước (by SKU+Dealer) ---
    if data_type == "RAW_SO" and {"SKU Code", "Dealer", "SO Quantity", "Date"}.issubset(df.columns):
        for (sku, dealer), grp in df.groupby(["SKU Code", "Dealer"]):
            for idx, row in grp.iterrows():
                cur_date = _to_date(row["Date"])
                if not isinstance(cur_date, date):
                    continue
                prev_month_start = (cur_date.replace(day=1) - pd.DateOffset(months=1)).date()
                prev_qty = db.query(func.sum(RawSO.so_quantity)).filter(
                    RawSO.sku_code == str(sku).strip(),
                    RawSO.dealer == str(dealer).strip(),
                    RawSO.txn_date >= prev_month_start,
                    RawSO.txn_date < cur_date.replace(day=1),
                ).scalar() or 0
                cur_qty = pd.to_numeric(row["SO Quantity"], errors="coerce") or 0
                if prev_qty > 0 and cur_qty > prev_qty * 3:  # > 200% growth = more than 3x
                    result.add_warning(idx, "SO Quantity",
                                        f"SO tăng bất thường >200% so với tháng trước ({prev_qty} → {cur_qty})")
                    df.at[idx, "_row_warn"] = True

    # --- 9. Master Data specific checks: missing Lead Time / Safety Stock / Target Stock Days ---
    if data_type == "MASTER_DATA":
        for idx, row in df.iterrows():
            if pd.isna(row.get("Lead Time")):
                result.add_warning(idx, "Lead Time", "Thiếu Lead Time")
                df.at[idx, "_row_warn"] = True
            if pd.isna(row.get("Safety Stock Days")):
                result.add_warning(idx, "Safety Stock Days", "Thiếu Safety Stock Days")
                df.at[idx, "_row_warn"] = True
            if pd.isna(row.get("Target Stock Days")):
                result.add_warning(idx, "Target Stock Days", "Thiếu Target Stock Days")
                df.at[idx, "_row_warn"] = True
            sku = row.get("SKU Code")
            if pd.isna(sku) or str(sku).strip() == "":
                result.add_error(idx, "SKU Code", "Thiếu SKU Code")
                df.at[idx, "_row_valid"] = False

    result.error_rows = int((~df["_row_valid"]).sum())
    result.warning_rows = int(df["_row_warn"].sum())
    result.valid_rows = result.total_rows - result.error_rows

    result.clean_df = df[df["_row_valid"]].drop(columns=["_row_valid", "_row_warn"])
    result.error_df = df[~df["_row_valid"]].drop(columns=["_row_valid", "_row_warn"])
    return result
