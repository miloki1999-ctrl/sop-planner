"""
Quick Analysis service — orchestrates the simplified default flow:
Upload file -> Validate -> Save DB -> Run Forecast -> Run Supply Plan.

This does NOT replace the granular services (upload_service, forecast_service,
supply_service) — it just chains them with sensible defaults so the new
default landing page can do everything in one click. Power users can still
go into Upload Center / Forecast Engine / Supply Plan individually for
fine-grained control (different update modes, method selection, filters...).

Default choices made here (documented, not hidden):
- Update mode for every sheet = "Update existing records" (upsert by natural
  key) so re-running Quick Analysis on a corrected file is always safe and
  idempotent — no duplicate accumulation.
- Forecast method = Weighted Moving Average (recommended default).
- Cutoff date = latest transaction date found in the uploaded RAW_SO sheet,
  falling back to today if RAW_SO wasn't in the file.
- Supply Plan runs all 4 scenarios (Conservative/Base/Target/Stretch).
"""
from dataclasses import dataclass, field
from datetime import date, datetime
import pandas as pd
from sqlalchemy.orm import Session

from services.upload_service import (
    load_workbook, save_master_data, save_transactional, create_upload_history, infer_data_period,
)
from services.validation_service import validate_dataframe, compute_file_hash, check_duplicate_file
from services.forecast_service import run_forecast_engine
from services.supply_service import run_supply_plan

DATA_TYPE_LABELS = {
    "MASTER_DATA": "Master Data", "RAW_SI": "Sell-In (SI)", "RAW_SO": "Sell-Out (SO)",
    "RAW_INVENTORY": "Inventory", "RAW_PO": "Purchase Order (PO)",
}


@dataclass
class QuickAnalysisResult:
    file_name: str
    detected_sheets: list = field(default_factory=list)
    unknown_sheet: bool = False
    validation_summary: dict = field(default_factory=dict)  # {data_type: {total, valid, error, warn}}
    error_reports: dict = field(default_factory=dict)  # {data_type: DataFrame of errors}
    saved_rows: dict = field(default_factory=dict)
    duplicate_file_warning: str = None
    forecast_version_id: int = None
    forecast_version_name: str = None
    supply_plan_rows: int = 0
    target_period: str = None
    success: bool = False
    error_message: str = None


def infer_cutoff_date(parsed: dict) -> date:
    if "RAW_SO" in parsed and not parsed["RAW_SO"].empty and "Date" in parsed["RAW_SO"].columns:
        dates = pd.to_datetime(parsed["RAW_SO"]["Date"], errors="coerce").dropna()
        if not dates.empty:
            return dates.max().date()
    return date.today()


def run_quick_analysis(db: Session, *, file_bytes: bytes, file_name: str, target_period: str,
                        username: str, method: str = "Weighted Moving Average") -> QuickAnalysisResult:
    result = QuickAnalysisResult(file_name=file_name, target_period=target_period)

    file_hash = compute_file_hash(file_bytes)
    dup = check_duplicate_file(db, file_hash)
    if dup:
        result.duplicate_file_warning = (
            f"File này đã từng upload lúc {dup.uploaded_at:%d/%m/%Y %H:%M} (Upload ID: {dup.upload_id}). "
            f"Vẫn tiếp tục xử lý lại."
        )

    try:
        parsed = load_workbook(file_bytes, file_name)
    except Exception as e:
        result.error_message = f"Không đọc được file: {e}"
        return result

    if "UNKNOWN" in parsed:
        result.unknown_sheet = True
        result.error_message = (
            "Không nhận diện được loại dữ liệu từ tên sheet/cấu trúc cột. "
            "Kiểm tra lại tên sheet: MASTER_DATA / RAW_SI / RAW_SO / RAW_INVENTORY / RAW_PO."
        )
        return result

    result.detected_sheets = list(parsed.keys())
    cutoff_date = infer_cutoff_date(parsed)

    # --- Validate + Save each sheet (MASTER_DATA first so SKU/Dealer checks work for the rest) ---
    order = ["MASTER_DATA", "RAW_SI", "RAW_SO", "RAW_INVENTORY", "RAW_PO"]
    for data_type in [d for d in order if d in parsed]:
        df = parsed[data_type]
        vr = validate_dataframe(data_type, df, db, file_hash)
        result.validation_summary[data_type] = dict(
            total=vr.total_rows, valid=vr.valid_rows, error=vr.error_rows, warning=vr.warning_rows,
        )
        if vr.errors:
            result.error_reports[data_type] = pd.DataFrame(vr.errors)

        period = infer_data_period(vr.clean_df, data_type) if data_type != "MASTER_DATA" else target_period

        if vr.valid_rows == 0:
            create_upload_history(
                db, file_name=file_name, file_hash=file_hash, username=username, data_type=data_type,
                data_period=period, total_rows=vr.total_rows, valid_rows=0, error_rows=vr.error_rows,
                warning_rows=vr.warning_rows, status="Rejected", update_mode="Update existing records",
                notes="Quick Analysis — no valid rows",
            )
            continue

        upload_id = create_upload_history(
            db, file_name=file_name, file_hash=file_hash, username=username, data_type=data_type,
            data_period=period, total_rows=vr.total_rows, valid_rows=vr.valid_rows, error_rows=vr.error_rows,
            warning_rows=vr.warning_rows, status="Saved", update_mode="Update existing records",
            notes="Quick Analysis Mode",
        )
        if data_type == "MASTER_DATA":
            n = save_master_data(db, vr.clean_df, upload_id, username, file_name)
        else:
            n = save_transactional(db, data_type, vr.clean_df, upload_id, username, file_name,
                                    "Update existing records", period)
        result.saved_rows[data_type] = n

    db.flush()

    # --- Run Forecast Engine (full scope, no filters) ---
    try:
        version = run_forecast_engine(
            db, version_name=f"{target_period} Quick Analysis {datetime.now().strftime('%H%M%S')}",
            target_period=target_period, cutoff_date=cutoff_date, method=method, username=username,
        )
        result.forecast_version_id = version.record_id
        result.forecast_version_name = version.version_name
    except Exception as e:
        result.error_message = f"Lỗi khi chạy Forecast Engine: {e}"
        return result

    # --- Run Supply Plan (all 4 scenarios) ---
    try:
        n = run_supply_plan(db, forecast_version_id=version.record_id, cutoff_date=cutoff_date, username=username)
        result.supply_plan_rows = n
    except Exception as e:
        result.error_message = f"Lỗi khi chạy Supply Plan: {e}"
        return result

    result.success = True
    return result
