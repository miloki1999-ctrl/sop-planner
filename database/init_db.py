"""
Run this once to create all tables and seed the default Admin user plus
baseline Assumptions (seasonality, WMA weights, scenario factors, DOS
thresholds). Safe to re-run — it only inserts what's missing.

Usage:
    python -m database.init_db
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.security import hash_password
from database.models import Base, User, Assumption, Dealer
from database.connection import engine, get_session
from config.settings import settings

DEFAULT_DEALERS = [
    dict(dealer_code="A", dealer_name="CellphoneS", dealer_group="CellphoneS Group", channel="KA", region="South"),
    dict(dealer_code="B", dealer_name="Thế Giới Di Động", dealer_group="TGDD Group", channel="KA", region="South"),
    dict(dealer_code="C", dealer_name="Di Động Việt", dealer_group="Di Dong Viet", channel="MT", region="South"),
    dict(dealer_code="D", dealer_name="Minh Tuấn Mobile", dealer_group="Minh Tuan Group", channel="MT", region="North"),
    dict(dealer_code="E", dealer_name="Giá Kho", dealer_group="Gia Kho Group", channel="GT", region="North"),
]


def create_tables():
    Base.metadata.create_all(bind=engine)
    print(f"[init_db] Tables created/verified at {settings.DATABASE_URL}")


def seed_admin():
    with get_session() as db:
        existing = db.query(User).filter_by(username=settings.DEFAULT_ADMIN_USERNAME).first()
        if existing:
            print("[init_db] Admin user already exists, skipping.")
            return
        admin = User(
            username=settings.DEFAULT_ADMIN_USERNAME,
            password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
            full_name=settings.DEFAULT_ADMIN_FULLNAME,
            role="Admin",
            is_active=True,
        )
        db.add(admin)

        # Also seed a Planner and Viewer sample account for testing roles
        db.add(User(
            username="planner1",
            password_hash=hash_password("Planner@123"),
            full_name="Nguyễn Văn A",
            role="Planner",
            is_active=True,
        ))
        db.add(User(
            username="viewer1",
            password_hash=hash_password("Viewer@123"),
            full_name="Trần Thị B",
            role="Viewer",
            is_active=True,
        ))
        print(f"[init_db] Seeded users: {settings.DEFAULT_ADMIN_USERNAME}/{settings.DEFAULT_ADMIN_PASSWORD} (Admin), "
              f"planner1/Planner@123 (Planner), viewer1/Viewer@123 (Viewer)")


def seed_assumptions():
    with get_session() as db:
        if db.query(Assumption).count() > 0:
            print("[init_db] Assumptions already seeded, skipping.")
            return
        rows = []
        for q, v in settings.DEFAULT_SEASONALITY.items():
            rows.append(Assumption(assumption_type="Seasonality", scope_key=q, value=v, updated_by="system"))
        for m, v in settings.DEFAULT_WEIGHTS_WMA.items():
            rows.append(Assumption(assumption_type="WMA_Weight", scope_key=m, value=v, updated_by="system"))
        for s, v in settings.DEFAULT_SCENARIO_FACTORS.items():
            rows.append(Assumption(assumption_type="Scenario", scope_key=s, value=v, updated_by="system"))
        for k, v in settings.DOS_THRESHOLDS.items():
            rows.append(Assumption(assumption_type="DOS_Threshold", scope_key=k, value=v, updated_by="system"))
        rows.append(Assumption(assumption_type="Growth", scope_key="Default", value=0.05, updated_by="system"))
        rows.append(Assumption(assumption_type="Promotion", scope_key="Default_Uplift", value=0.15, updated_by="system"))
        rows.append(Assumption(assumption_type="EOL", scope_key="Phase_Out_Reduction_Rate", value=0.30, updated_by="system"))
        rows.append(Assumption(assumption_type="NPI", scope_key="Default_Ramp_Up_Rate", value=0.60, updated_by="system"))
        rows.append(Assumption(assumption_type="SO_Spike", scope_key="Threshold_Pct", value=200, updated_by="system"))
        db.add_all(rows)
        print(f"[init_db] Seeded {len(rows)} default assumption rows.")


def seed_dealers():
    with get_session() as db:
        if db.query(Dealer).count() > 0:
            print("[init_db] Dealers already seeded, skipping.")
            return
        for d in DEFAULT_DEALERS:
            db.add(Dealer(**d, created_by="system"))
        print(f"[init_db] Seeded {len(DEFAULT_DEALERS)} dealers.")


if __name__ == "__main__":
    create_tables()
    seed_admin()
    seed_assumptions()
    seed_dealers()
    print("[init_db] Done.")
