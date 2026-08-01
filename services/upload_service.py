"""
Upload service — handles:
  - Auto-detecting data type from a single-sheet CSV or multi-sheet Excel
  - Reading a file into a dict of {data_type: DataFrame}
  - Writing validated rows to the correct table under a chosen update mode
    (Preview only / Append / Replace selected period / Update existing records)
  - Recording an UploadHistory row for every attempt (spec section 9)

This is the only module that writes raw transactional/master rows to the
database — pages/upload_center.py should never touch the ORM directly.
"""
import io
import uuid
from datetime import datetime, date
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import delete

from database.models import (
    Product, Dealer, RawSI, RawSO, InventorySnapshot, PurchaseOrder,
    UploadHistory, AuditLog,
)
from services.validation_service import (
    validate_dataframe, compute_file_hash, check_duplicate_file, REQUIRED_COLUMNS,
)

SHEET_NAME_ALIASES = {
    "MASTER_DATA": "MASTER_DATA",
    "MASTERDATA": "MASTER_DATA",
    "MASTER DATA": "MASTER_DATA",
    "RAW_SI": "RAW_SI",
    "SI": "RAW_SI",
    "RAW_SO": "RAW_SO",
    "SO": "RAW_SO",
    "RAW_INVENTORY": "RAW_INVENTORY",
    "INVENTORY": "RAW_INVENTORY",
    "RAW_PO": "RAW_PO",
    "PO": "RAW_PO",
}

MODEL_MAP = {
    "RAW_SI": RawSI,
    "RAW_SO": RawSO,
    "RAW_INVENTORY": InventorySnapshot,
    "RAW_PO": PurchaseOrder,
}


def detect_data_type_from_columns(df: pd.DataFrame) -> str:
    """Fallback detection by column signature when sheet/file name doesn't match."""
    cols = set(df.columns)
    best_match, best_score = None, 0
    for dtype, required in REQUIRED_COLUMNS.items():
        score = len(cols & set(required))
        if score > best_score:
            best_match, best_score = dtype, score
    return best_match


def load_workbook(file_bytes: bytes, file_name: str) -> dict:
    """Returns {data_type: DataFrame} detected from an uploaded xlsx/csv."""
    result = {}
    if file_name.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(file_bytes))
        dtype = detect_data_type_from_columns(df)
        if dtype:
            result[dtype] = df
        else:
            result["UNKNOWN"] = df
        return result

    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    for sheet in xls.sheet_names:
        key = SHEET_NAME_ALIASES.get(sheet.strip().upper())
        df = xls.parse(sheet)
        if df.empty:
            continue
        if not key:
            key = detect_data_type_from_columns(df)
        if key:
            result[key] = df
    return result


# ---------------------------------------------------------------------------
# Row-level mapping: DataFrame columns (Vietnamese/spec labels) -> ORM fields
# ---------------------------------------------------------------------------
def _clean_date(v):
    if pd.isna(v) or v in ("", None):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None


def _clean_num(v, default=0.0):
    n = pd.to_numeric(v, errors="coerce")
    return float(n) if pd.notna(n) else default


def _clean_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "y", "npi", "eol")


def _clean_str(v, default=""):
    if pd.isna(v):
        return default
    return str(v).strip()


def df_row_to_master(row) -> dict:
    return dict(
        sku_code=_clean_str(row["SKU Code"]),
        sku_name=_clean_str(row.get("SKU Name")),
        brand=_clean_str(row.get("Brand")),
        category=_clean_str(row.get("Category")),
        product_group=_clean_str(row.get("Product Group")),
        product_type=_clean_str(row.get("Product Type")),
        model_compatibility=_clean_str(row.get("Model Compatibility")),
        color=_clean_str(row.get("Color")),
        launch_date=_clean_date(row.get("Launch Date")),
        eol_date=_clean_date(row.get("EOL Date")),
        product_status=_clean_str(row.get("Product Status"), "Active"),
        abc_classification=_clean_str(row.get("ABC Classification")),
        unit_cost=_clean_num(row.get("Unit Cost")),
        dealer_price=_clean_num(row.get("Dealer Price")),
        srp=_clean_num(row.get("SRP")),
        lead_time=int(_clean_num(row.get("Lead Time"), 30)),
        moq=int(_clean_num(row.get("MOQ"), 1)),
        order_multiple=int(_clean_num(row.get("Order Multiple"), 1)),
        safety_stock_days=int(_clean_num(row.get("Safety Stock Days"), 15)),
        target_stock_days=int(_clean_num(row.get("Target Stock Days"), 45)),
        default_growth_rate=_clean_num(row.get("Default Growth Rate"), 0.0),
        seasonality_group=_clean_str(row.get("Seasonality Group")),
        npi_flag=_clean_bool(row.get("NPI Flag")),
        eol_flag=_clean_bool(row.get("EOL Flag")),
        replacement_sku=_clean_str(row.get("Replacement SKU")),
        main_dealer=_clean_str(row.get("Main Dealer")),
        notes=_clean_str(row.get("Notes")),
    )


def df_row_to_si(row) -> dict:
    return dict(
        txn_date=_clean_date(row["Date"]), dealer=_clean_str(row["Dealer"]),
        dealer_group=_clean_str(row.get("Dealer Group")), channel=_clean_str(row.get("Channel")),
        region=_clean_str(row.get("Region")), brand=_clean_str(row.get("Brand")),
        category=_clean_str(row.get("Category")), sku_code=_clean_str(row["SKU Code"]),
        sku_name=_clean_str(row.get("SKU Name")), si_quantity=_clean_num(row.get("SI Quantity")),
        si_revenue=_clean_num(row.get("SI Revenue")), unit_price=_clean_num(row.get("Unit Price")),
        promotion=_clean_str(row.get("Promotion")), campaign=_clean_str(row.get("Campaign")),
        po_number=_clean_str(row.get("PO Number")), invoice_number=_clean_str(row.get("Invoice Number")),
    )


def df_row_to_so(row) -> dict:
    return dict(
        txn_date=_clean_date(row["Date"]), dealer=_clean_str(row["Dealer"]),
        store=_clean_str(row.get("Store")), region=_clean_str(row.get("Region")),
        channel=_clean_str(row.get("Channel")), brand=_clean_str(row.get("Brand")),
        category=_clean_str(row.get("Category")), sku_code=_clean_str(row["SKU Code"]),
        sku_name=_clean_str(row.get("SKU Name")), so_quantity=_clean_num(row.get("SO Quantity")),
        so_revenue=_clean_num(row.get("SO Revenue")), promotion=_clean_str(row.get("Promotion")),
        campaign=_clean_str(row.get("Campaign")), npi_period=_clean_str(row.get("NPI Period")),
        note=_clean_str(row.get("Note")),
    )


def df_row_to_inventory(row) -> dict:
    avail = _clean_num(row.get("Available Inventory"))
    reserved = _clean_num(row.get("Reserved Inventory"))
    damaged = _clean_num(row.get("Damaged Inventory"))
    sellable_input = row.get("Sellable Inventory")
    sellable = _clean_num(sellable_input) if pd.notna(sellable_input) else (avail - reserved - damaged)
    return dict(
        snapshot_date=_clean_date(row["Snapshot Date"]), dealer=_clean_str(row["Dealer"]),
        warehouse=_clean_str(row.get("Store hoặc Warehouse")), brand=_clean_str(row.get("Brand")),
        category=_clean_str(row.get("Category")), sku_code=_clean_str(row["SKU Code"]),
        sku_name=_clean_str(row.get("SKU Name")), available_inventory=avail,
        reserved_inventory=reserved, damaged_inventory=damaged, sellable_inventory=sellable,
        inbound_quantity=_clean_num(row.get("Inbound Quantity")),
        inbound_eta=_clean_date(row.get("Inbound ETA")), backorder=_clean_num(row.get("Backorder")),
        note=_clean_str(row.get("Note")),
    )


def df_row_to_po(row) -> dict:
    po_qty = _clean_num(row.get("PO Quantity"))
    recv_qty = _clean_num(row.get("Received Quantity"))
    outstanding_input = row.get("Outstanding Quantity")
    outstanding = _clean_num(outstanding_input) if pd.notna(outstanding_input) else (po_qty - recv_qty)
    return dict(
        po_date=_clean_date(row["PO Date"]), po_number=_clean_str(row["PO Number"]),
        supplier=_clean_str(row.get("Supplier")), brand=_clean_str(row.get("Brand")),
        sku_code=_clean_str(row["SKU Code"]), sku_name=_clean_str(row.get("SKU Name")),
        po_quantity=po_qty, received_quantity=recv_qty, outstanding_quantity=max(0, outstanding),
        expected_arrival_date=_clean_date(row.get("Expected Arrival Date")),
        actual_arrival_date=_clean_date(row.get("Actual Arrival Date")),
        po_status=_clean_str(row.get("PO Status")), unit_cost=_clean_num(row.get("Unit Cost")),
        total_po_value=_clean_num(row.get("Total PO Value")), note=_clean_str(row.get("Note")),
    )


ROW_MAPPERS = {
    "RAW_SI": df_row_to_si, "RAW_SO": df_row_to_so,
    "RAW_INVENTORY": df_row_to_inventory, "RAW_PO": df_row_to_po,
}


def infer_data_period(df: pd.DataFrame, data_type: str) -> str:
    date_col = {"RAW_SI": "Date", "RAW_SO": "Date", "RAW_INVENTORY": "Snapshot Date", "RAW_PO": "PO Date"}.get(data_type)
    if not date_col or date_col not in df.columns or df.empty:
        return datetime.utcnow().strftime("%Y-%m")
    dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
    if dates.empty:
        return datetime.utcnow().strftime("%Y-%m")
    return dates.dt.to_period("M").mode().iloc[0].strftime("%Y-%m")


def save_master_data(db: Session, clean_df: pd.DataFrame, upload_id: str, username: str, source_file: str) -> int:
    """Lưu Master Data. Gộp toàn bộ việc kiểm tra SKU đã tồn tại thành 1 câu
    truy vấn (thay vì 1 câu/dòng) — trên database ở xa (Neon/Postgres), mỗi
    câu truy vấn tốn ~50-150ms round-trip; với hàng nghìn dòng, kiểu cũ có
    thể mất nhiều phút. Cách này chỉ còn 1 round-trip cho toàn bộ danh sách."""
    rows_data = [df_row_to_master(row) for _, row in clean_df.iterrows()]
    sku_codes = [d["sku_code"] for d in rows_data]
    existing_map = {}
    if sku_codes:
        existing_map = {p.sku_code: p for p in db.query(Product).filter(Product.sku_code.in_(sku_codes)).all()}

    count = 0
    for data in rows_data:
        existing = existing_map.get(data["sku_code"])
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.upload_id = upload_id
            existing.source_file = source_file
        else:
            new_obj = Product(**data, upload_id=upload_id, source_file=source_file, created_by=username)
            db.add(new_obj)
            existing_map[data["sku_code"]] = new_obj  # phòng file có SKU trùng nhau trong cùng lần upload
        count += 1
    return count


_DATE_FIELD = {"RAW_SI": "txn_date", "RAW_SO": "txn_date", "RAW_INVENTORY": "snapshot_date", "RAW_PO": "po_date"}


def save_transactional(db: Session, data_type: str, clean_df: pd.DataFrame, upload_id: str,
                        username: str, source_file: str, update_mode: str, data_period: str) -> int:
    """Lưu dữ liệu giao dịch (SI/SO/Inventory/PO). Cùng lý do như
    save_master_data ở trên: gộp việc tìm bản ghi trùng thành 1 câu truy vấn
    duy nhất (theo khoảng ngày của cả lô dữ liệu, hoặc theo po_number với PO)
    thay vì 1 câu truy vấn cho từng dòng — tránh treo lâu khi DB ở xa."""
    model = MODEL_MAP[data_type]
    mapper = ROW_MAPPERS[data_type]
    date_field_name = _DATE_FIELD[data_type]
    date_col = getattr(model, date_field_name)

    if update_mode == "Replace selected period":
        y, m = map(int, data_period.split("-"))
        from calendar import monthrange
        start = date(y, m, 1)
        end = date(y, m, monthrange(y, m)[1])
        db.execute(delete(model).where(date_col >= start, date_col <= end))

    dup_keys = {
        "RAW_SI": ["txn_date", "dealer", "sku_code", "invoice_number"],
        "RAW_SO": ["txn_date", "dealer", "store", "sku_code"],
        "RAW_INVENTORY": ["snapshot_date", "dealer", "warehouse", "sku_code"],
        "RAW_PO": ["po_number", "sku_code"],
    }[data_type]

    rows_data = [mapper(row) for _, row in clean_df.iterrows()]

    existing_map = {}
    if update_mode == "Update existing records" and rows_data:
        if data_type == "RAW_PO":
            po_numbers = [d.get("po_number") for d in rows_data if d.get("po_number")]
            existing_records = db.query(model).filter(model.po_number.in_(po_numbers)).all() if po_numbers else []
        else:
            dates = [d.get(date_field_name) for d in rows_data if d.get(date_field_name)]
            existing_records = (
                db.query(model).filter(date_col >= min(dates), date_col <= max(dates)).all() if dates else []
            )
        for rec in existing_records:
            existing_map[tuple(getattr(rec, k) for k in dup_keys)] = rec

    count = 0
    for data in rows_data:
        record = None
        key = tuple(data.get(k) for k in dup_keys)
        if update_mode == "Update existing records":
            record = existing_map.get(key)
        if record:
            for k, v in data.items():
                setattr(record, k, v)
            record.upload_id = upload_id
            record.source_file = source_file
            record.data_period = data_period
        else:
            new_obj = model(**data, upload_id=upload_id, source_file=source_file,
                             created_by=username, data_period=data_period)
            db.add(new_obj)
            if update_mode == "Update existing records":
                existing_map[key] = new_obj
        count += 1
    return count


def create_upload_history(db: Session, *, file_name, file_hash, username, data_type,
                           data_period, total_rows, valid_rows, error_rows, warning_rows,
                           status, update_mode, notes="", error_report_path=None) -> str:
    upload_id = f"UP-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    rec = UploadHistory(
        upload_id=upload_id, file_name=file_name, file_hash=file_hash, uploaded_by=username,
        data_type=data_type, data_period=data_period, total_rows=total_rows, valid_rows=valid_rows,
        error_rows=error_rows, warning_rows=warning_rows, status=status, update_mode=update_mode,
        notes=notes, error_report_path=error_report_path,
    )
    db.add(rec)
    db.flush()
    db.add(AuditLog(table_name=data_type, record_ref=upload_id, action="UPLOAD",
                     reason=f"{update_mode} — {valid_rows}/{total_rows} valid rows",
                     performed_by=username))
    return upload_id
