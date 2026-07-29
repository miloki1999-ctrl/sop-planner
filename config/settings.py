"""
Central application configuration.
Loaded once from .env (or environment variables) and shared across the whole app.
Nothing here should read Streamlit session_state — this module must stay
framework-agnostic so services/ and database/ can be reused (e.g. in a future
FastAPI layer) without dragging Streamlit in.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get(key: str, default: str = "") -> str:
    """Resolution order: Streamlit Cloud secrets (st.secrets) > .env / OS env > default.
    Wrapped in try/except because st.secrets raises when no secrets.toml exists
    (e.g. when running database/init_db.py or sample_data scripts from the CLI,
    outside a Streamlit runtime)."""
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.getenv(key, default)


class Settings:
    # --- Database ---
    DATABASE_URL: str = _get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'sop_planner.db'}")

    # --- App ---
    APP_SECRET_KEY: str = _get("APP_SECRET_KEY", "dev-secret-key")
    APP_ENV: str = _get("APP_ENV", "development")

    # --- Paths ---
    UPLOAD_DIR: Path = BASE_DIR / _get("UPLOAD_DIR", "./uploads").lstrip("./")
    EXPORT_DIR: Path = BASE_DIR / _get("EXPORT_DIR", "./exports").lstrip("./")
    SAMPLE_DATA_DIR: Path = BASE_DIR / "sample_data"

    # --- Bootstrap admin (first run only) ---
    DEFAULT_ADMIN_USERNAME: str = _get("DEFAULT_ADMIN_USERNAME", "admin")
    DEFAULT_ADMIN_PASSWORD: str = _get("DEFAULT_ADMIN_PASSWORD", "Admin@123")
    DEFAULT_ADMIN_FULLNAME: str = _get("DEFAULT_ADMIN_FULLNAME", "System Administrator")

    # --- Business assumption defaults (seed values only; live values are
    # stored in the `assumptions` table and are user-editable at runtime) ---
    DEFAULT_SEASONALITY = {"Q1": 1.10, "Q2": 1.15, "Q3": 1.30, "Q4": 1.35}
    DEFAULT_WEIGHTS_WMA = {"M-1": 0.5, "M-2": 0.3, "M-3": 0.2}
    DEFAULT_SCENARIO_FACTORS = {
        "Conservative": 0.90,
        "Base": 1.00,
        "Target": 1.10,
        "Stretch": 1.20,
    }
    DOS_THRESHOLDS = {
        "critical": 30,
        "reorder": 45,
        "healthy": 70,
        "watch": 95,
    }
    SO_SPIKE_THRESHOLD_PCT = 200  # flag SO growth above this %

    for d in (UPLOAD_DIR, EXPORT_DIR, SAMPLE_DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)


settings = Settings()
