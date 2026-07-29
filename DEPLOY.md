# Deploy lên Streamlit Community Cloud (miễn phí)

Mục tiêu: có 1 URL cố định (VD `sop-planner-meko.streamlit.app`), mở là dùng
được luôn, không cần mở terminal hay cài Python trên máy mỗi lần.

⚠️ **Quan trọng:** Ổ đĩa của Streamlit Community Cloud là **tạm thời** — file
`sop_planner.db` (SQLite) sẽ **mất dữ liệu** mỗi khi app khởi động lại (redeploy,
app "ngủ" do không ai dùng >7 ngày, hoặc Streamlit cập nhật hạ tầng). Vì vậy
bản hướng dẫn này dùng **Postgres miễn phí (Neon.tech)** làm database thật —
kiến trúc code không đổi gì, chỉ đổi `DATABASE_URL`.

---

## Bước 1 — Tạo Postgres miễn phí trên Neon

1. Vào https://neon.tech → Sign up (free, không cần thẻ)
2. Tạo project mới, đặt tên `sop-planner`
3. Vào **Dashboard → Connection string**, copy chuỗi dạng:
   ```
   postgresql://user:password@ep-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
4. Đổi tiền tố `postgresql://` thành `postgresql+psycopg2://` (SQLAlchemy cần
   driver name), giữ nguyên phần còn lại. Kết quả:
   ```
   postgresql+psycopg2://user:password@ep-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```

*(Supabase cũng miễn phí và tương tự nếu bạn thích UI của họ hơn.)*

---

## Bước 2 — Đẩy code lên GitHub

```bash
cd app
git init
git add .
git commit -m "S&OP Planner - initial deploy"
git branch -M main
git remote add origin https://github.com/<your-username>/sop-planner.git
git push -u origin main
```

Kiểm tra `.gitignore` đã loại `.env`, `secrets.toml`, `*.db` — **không đẩy
mật khẩu thật lên GitHub công khai**. Nếu repo private thì vẫn nên giữ nguyên
tắc này cho an toàn.

---

## Bước 3 — Deploy trên Streamlit Community Cloud

1. Vào https://share.streamlit.io → Sign in bằng GitHub
2. **New app** → chọn repo `sop-planner`, branch `main`, main file path:
   `app.py` (nếu repo có thư mục con `app/`, path là `app/app.py` — kiểm tra
   theo cấu trúc bạn push lên)
3. Nhấn **Deploy**

App sẽ build lần đầu (~2-3 phút), lỗi thiếu secrets là bình thường ở lần đầu.

---

## Bước 4 — Cấu hình Secrets (thay cho file .env)

1. Trong app vừa tạo → **⋮ (Settings) → Secrets**
2. Dán nội dung sau (thay `<connection-string-neon>` bằng chuỗi ở Bước 1):

```toml
DATABASE_URL = "<connection-string-neon>"
APP_SECRET_KEY = "doi-chuoi-nay-thanh-random-string-that-dai"
APP_ENV = "production"
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "Admin@123-doi-lai-ngay-sau-khi-deploy"
DEFAULT_ADMIN_FULLNAME = "System Administrator"
```

3. **Save** → app tự khởi động lại và đọc secrets này thay vì `.env`
   (code đã được cấu hình sẵn để ưu tiên `st.secrets` khi chạy trên
   Streamlit Cloud — xem `config/settings.py`)

---

## Bước 5 — Khởi tạo database trên Postgres

App tự chạy `create_tables()`, `seed_admin()`, `seed_assumptions()`,
`seed_dealers()` mỗi lần khởi động (idempotent — chỉ insert nếu chưa có),
nên bạn **không cần SSH vào đâu cả**. Chỉ cần mở URL app lần đầu là DB được
khởi tạo tự động trên Neon.

---

## Bước 6 — Đổi mật khẩu Admin ngay sau khi deploy

Đăng nhập bằng `admin` / mật khẩu đã đặt ở Bước 4 → vào **Master Data /
User Management** (sẽ có ở bản hoàn thiện) để đổi mật khẩu, hoặc tạm thời đổi
`DEFAULT_ADMIN_PASSWORD` trong Secrets rồi xoá dòng user cũ trong Neon SQL
Editor nếu cần đổi ngay.

---

## Cập nhật code sau này

Mỗi lần bạn `git push` lên `main`, Streamlit Community Cloud **tự động
redeploy** — không cần thao tác gì thêm.

---

## Giới hạn của gói miễn phí cần biết

- App sẽ "ngủ" nếu không ai truy cập trong ~7 ngày — người dùng đầu tiên mở
  lại sẽ phải đợi ~30-60 giây để app "thức dậy". Dữ liệu **không mất** vì đã
  ở Postgres (Neon), chỉ riêng file SQLite mới mất khi dùng cách đó.
- Neon free tier: đủ dùng cho quy mô nội bộ vài chục người dùng, nếu công ty
  scale lớn hơn có thể nâng cấp gói trả phí của Neon mà không cần đổi code.
- Nếu cần bảo mật cao hơn / không phụ thuộc dịch vụ bên thứ 3 → chuyển sang
  VPS riêng (xem README.md mục "Nâng cấp SQLite → PostgreSQL" + có thể yêu
  cầu mình chuẩn bị Dockerfile khi cần).
