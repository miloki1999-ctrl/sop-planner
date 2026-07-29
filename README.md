# S&OP Planner — Sales & Operations Planning System

Hệ thống S&OP nội bộ cho ngành phân phối phụ kiện ICT. Upload file Excel/CSV
(SI, SO, Inventory, PO, Master Data) → hệ thống tự validate, lưu database,
tính Forecast, tạo Plan SO/SI, tính Suggested PO, và hiển thị Dashboard +
Exception Report.

> **Trạng thái: HOÀN THÀNH — cả 7 module chính đều hoạt động thật, không
> mockup.** Toàn bộ nút bấm, công thức, upload, forecast, supply plan,
> export Excel đều chạy trên dữ liệu thật trong database, đã test end-to-end.
>
> | Module | Trạng thái |
> |---|---|
> | Upload Center | ✅ Auto-detect sheet, validate đủ rule mục 6, 4 update mode, Upload History |
> | Forecast Engine | ✅ Avg 3M/6M, WMA, Run-rate, Growth/Seasonal/Promo/Coverage, EOL/Phase-out/NPI riêng, Manual Adjustment tự lưu, Forecast Accuracy (MAPE/Bias) |
> | Supply Plan | ✅ 4 scenario (Conservative/Base/Target/Stretch), Plan SI, DOS/Weeks of Cover, Suggested PO, Reorder Point, PO Status |
> | Exception Report | ✅ Đủ 12 loại exception theo spec, lọc + export |
> | Dashboard | ✅ 12 KPI + 6 biểu đồ Plotly, export Dealer/SKU Summary |
> | Version History | ✅ Draft→Revised→Approved→Locked, so sánh version, Audit Trail |
> | Master Data & Assumptions | ✅ Sửa Product/Dealer/User trực tiếp, sửa toàn bộ Assumptions (Growth/Seasonality/Promotion/Scenario/DOS Threshold/WMA/NPI) |
> | Data Quality | ✅ Trend chất lượng dữ liệu qua các lần upload |
>
> Xem mục **"9. Checklist kiểm thử nhanh"** ở cuối file này để test toàn bộ luồng.

---

## 1. Chạy local

```bash
cd app
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # chỉnh nếu cần
python -m database.init_db      # tạo bảng + seed admin/dealers/assumptions
python -m sample_data.generate_sample_data   # (tuỳ chọn) tạo file mẫu để test upload

streamlit run app.py
```

Mở trình duyệt tại `http://localhost:8501`.

### Tài khoản mẫu
| Username  | Password    | Role    |
|-----------|-------------|---------|
| admin     | Admin@123   | Admin   |
| planner1  | Planner@123 | Planner |
| viewer1   | Viewer@123  | Viewer  |

---

## 2. Cấu trúc project

```
app/
├── app.py                    # entrypoint: login + landing page
├── pages/                    # mỗi file = 1 module (Streamlit multipage)
│   ├── upload_center.py      # ✅ Upload + Validate + Save DB + History
│   ├── dashboard.py          # ✅ KPI + 6 biểu đồ Plotly
│   ├── forecast_engine.py    # ✅ Chạy Forecast, chỉnh tay, Accuracy
│   ├── supply_plan.py        # ✅ 4 scenario, Plan SI, Suggested PO
│   ├── exception_report.py   # ✅ 12 loại exception, export
│   ├── version_history.py    # ✅ Status flow, so sánh version, Audit Trail
│   ├── data_quality.py       # ✅ Trend chất lượng dữ liệu
│   ├── master_data.py        # ✅ Product/Dealer/User Management
│   └── assumptions.py        # ✅ Growth/Seasonality/Promotion/Scenario/NPI
├── components/
│   └── sidebar.py            # navigation dùng chung
├── services/                 # business logic — KHÔNG chứa Streamlit code
│   ├── upload_service.py     # đọc file, map cột, ghi DB theo update mode
│   ├── validation_service.py # toàn bộ rule kiểm tra dữ liệu
│   ├── forecast_service.py   # công thức Forecast (mục 10-12)
│   ├── supply_service.py     # công thức Supply Plan (mục 13-16)
│   ├── exception_service.py  # 12 loại exception (mục 18.5)
│   ├── dashboard_service.py  # KPI + chart data (mục 18.1)
│   ├── master_data_service.py# CRUD Product/Dealer/User
│   ├── assumptions_service.py# CRUD Assumptions
│   ├── export_service.py     # Excel export chuẩn (freeze header, filter, autofit)
│   └── audit_service.py      # Query audit log / manual adjustment (read-only)
├── database/
│   ├── models.py             # ⭐ SOURCE OF TRUTH cho toàn bộ data model
│   ├── connection.py         # engine/session dùng chung
│   ├── init_db.py            # tạo bảng + seed admin/dealer/assumptions
│   └── migrations/
├── utils/
│   ├── security.py           # hash/verify password (bcrypt trực tiếp)
│   └── auth.py                # login/logout/phân quyền
├── uploads/                  # file gốc được lưu lại (audit trail)
├── exports/                  # file Excel xuất ra
├── sample_data/
│   ├── generate_sample_data.py
│   └── SOP_Sample_Upload.xlsx
├── config/settings.py        # cấu hình tập trung, đọc từ .env
├── requirements.txt
├── .env.example
└── README.md
```

**Nguyên tắc kiến trúc bắt buộc giữ xuyên suốt:**
- Một database, một `models.py` duy nhất — mọi module (Forecast Engine,
  Supply Plan, Dashboard...) đọc/ghi qua cùng các bảng này, không tạo schema
  riêng.
- `services/` chứa toàn bộ business logic, không phụ thuộc Streamlit →
  có thể tái sử dụng nếu sau này cần thêm API (FastAPI) song song với UI.
- `pages/*.py` chỉ lo UI + gọi service, không chứa công thức nghiệp vụ.
- Không dùng `st.session_state` để lưu dữ liệu nghiệp vụ — chỉ dùng cho
  trạng thái UI tạm thời (VD: bước hiện tại của wizard upload).

---

## 3. Data Model tóm tắt

Xem chi tiết đầy đủ field trong `database/models.py`. Bảng chính:

| Bảng | Mục đích |
|---|---|
| `users` | Tài khoản, mật khẩu (bcrypt), role |
| `dealers` | Master data đại lý |
| `products` | Master data SKU (Product Status, Lead Time, MOQ, Safety Stock...) |
| `raw_si` / `raw_so` | Dữ liệu Sell-In / Sell-Out theo giao dịch |
| `inventory_snapshots` | Tồn kho theo ngày snapshot |
| `purchase_orders` | Đơn hàng nhập |
| `upload_history` | Lịch sử mỗi lần upload (upload_id, file_hash, số dòng lỗi/hợp lệ...) |
| `forecast_versions` / `forecast_detail` | Version control cho Forecast |
| `supply_plan` | Kết quả Plan SO/SI/Suggested PO theo scenario |
| `manual_adjustments` | Lịch sử chỉnh tay (old/new value, lý do, người chỉnh) |
| `approved_plans` | Approve/Lock plan |
| `assumptions` | Growth, Seasonality, Promotion Uplift, DOS Threshold... (chỉnh được qua UI) |
| `audit_logs` | Nhật ký toàn hệ thống, không cho Planner sửa/xoá |

Mọi bảng dữ liệu nghiệp vụ đều có: `upload_id`, `data_period`, `source_file`,
`created_at`, `created_by`, `updated_at`.

---

## 4. Luồng Upload Center (đã hoạt động đầy đủ)

1. Chọn kỳ dữ liệu (năm/tháng)
2. Upload file Excel (nhiều sheet) hoặc CSV, hoặc dùng nút "Dùng Sample Data"
3. Hệ thống auto-detect loại dữ liệu theo tên sheet hoặc cấu trúc cột
4. Validation chạy đầy đủ các rule ở spec mục 6: thiếu cột, sai kiểu dữ liệu,
   ngày không hợp lệ, SKU/Dealer chưa mapping, trùng dữ liệu, số âm, PO Date >
   ETA, EOL vẫn có PO, SO tăng bất thường >200%, thiếu Lead Time/Safety
   Stock/Target Stock Days, file đã từng upload
5. Hiển thị Preview + số liệu tổng/hợp lệ/lỗi/cảnh báo + tải Error Report
6. Chọn cách cập nhật: Preview only / Append / Replace selected period /
   Update existing records
7. Xác nhận → ghi thật vào database, tạo Upload ID, ghi Audit Log
8. Upload History hiển thị toàn bộ lịch sử, luôn có ở cuối trang

---

## 5. Nâng cấp SQLite → PostgreSQL

1. Cài `psycopg2-binary`: `pip install psycopg2-binary`
2. Tạo database PostgreSQL và user
3. Đổi `DATABASE_URL` trong `.env`:
   ```
   DATABASE_URL=postgresql+psycopg2://sop_user:password@host:5432/sop_planner
   ```
4. Chạy lại `python -m database.init_db` để tạo bảng trên Postgres
5. Không cần sửa `models.py`, `services/`, hay `pages/` — toàn bộ code dùng
   SQLAlchemy ORM nên portable sẵn.

---

## 6. Backup / Restore (SQLite — bản MVP)

**Backup:** copy file `sop_planner.db` sang nơi lưu trữ an toàn (nên có
timestamp, VD `sop_planner_20260101.db`).

**Restore:** dừng app → thay file `sop_planner.db` bằng bản backup → chạy
lại `streamlit run app.py`.

Với PostgreSQL, dùng `pg_dump` / `pg_restore` chuẩn.

---

## 7. Thêm Dealer mới

Hiện tại: sửa `DEFAULT_DEALERS` trong `database/init_db.py` rồi chạy lại, hoặc
insert trực tiếp qua trang **Master Data** (sẽ hoàn thiện UI ở bước tiếp theo —
schema `dealers` đã sẵn sàng).

## 8. Thêm SKU mới

Upload qua **Upload Center → MASTER_DATA** với update mode "Update existing
records" (SKU mới sẽ được insert, SKU đã có sẽ được cập nhật).

---

## 9. Checklist kiểm thử nhanh (toàn bộ luồng end-to-end)

**Setup**
- [ ] `python -m database.init_db` chạy không lỗi, tạo 3 user mẫu
- [ ] `python -m sample_data.generate_sample_data` tạo file mẫu 30 SKU / 24 tháng
- [ ] Đăng nhập bằng admin/Admin@123

**Upload Center**
- [ ] "Dùng Sample Data" → Validation hiển thị 0 lỗi cho cả 5 sheet
- [ ] Chọn update mode Append cho tất cả sheet → Save → Upload History có 5 dòng mới
- [ ] Upload lại đúng file đó → hệ thống cảnh báo "File đã từng upload"
- [ ] Sửa 1 dòng SO Quantity âm trong file mẫu → validation phải báo lỗi "Quantity âm"

**Forecast Engine**
- [ ] Chọn Cutoff Date + tháng Forecast → Chạy Forecast Engine → tạo Version Draft có Total Forecast SO > 0
- [ ] Tab Kết quả: sửa Manual Adj. Factor 1 dòng → Lưu → giá trị Final Forecast SO đổi theo đúng công thức
- [ ] Tab Accuracy: chọn version có actual SO đã có (VD tháng trước) → tính Accuracy ra MAPE/Bias hợp lý

**Supply Plan**
- [ ] Chọn Forecast Version vừa tạo → Tính lại Supply Plan → có 4 scenario x SKU x Dealer
- [ ] SKU stockout mẫu (DOS thấp) → PO Status = "Order Now"
- [ ] SKU overstock mẫu (DOS cao) → PO Status = "Overstock", Suggested PO = 0
- [ ] SKU EOL mẫu → PO Status = "EOL - No PO"

**Exception Report**
- [ ] Chọn version → thấy đủ các loại exception (Stockout, Overstock, SO giảm, Inbound trễ ETA...)
- [ ] Export Excel tải về được, đúng dữ liệu đã lọc

**Dashboard**
- [ ] Chọn version → 12 KPI card hiển thị số liệu khớp Forecast/Supply Plan vừa tạo
- [ ] 6 biểu đồ render không lỗi

**Version History**
- [ ] Chuyển version Draft → Revised → Approved → Locked, mỗi bước ghi Audit Log
- [ ] Tab So sánh: chọn 2 version, thấy % thay đổi Total Forecast/Plan SO/SI/PO
- [ ] Tab Audit Trail: thấy đầy đủ log Upload/Forecast/Approve/Lock

**Master Data & Assumptions**
- [ ] Sửa Lead Time 1 SKU → Lưu → chạy lại Forecast/Supply Plan thấy Suggested PO đổi theo
- [ ] Thêm Dealer mới → xuất hiện trong danh sách Dealer ở Forecast Engine
- [ ] (Admin) Tạo user mới, reset mật khẩu, khoá/mở khoá user
- [ ] Sửa Seasonality Q3 → chạy lại Forecast tháng Q3 → Seasonal Factor đổi theo

**Data Quality**
- [ ] Sau vài lần upload, biểu đồ tỷ lệ hợp lệ theo thời gian hiển thị đúng xu hướng
