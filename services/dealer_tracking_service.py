"""
Dealer Tracking data layer — SI/SO theo đại lý (CellphoneS, Minh Tuấn Mobile,
hoặc bất kỳ đại lý nào khác), xem theo Tuần / Tháng / Năm.

Pure query/aggregation functions; pages/dealer_tracking.py xử lý rendering
(Plotly charts, KPI cards). Tách riêng để test được không cần Streamlit,
theo đúng convention của dashboard_service.py.
"""
from datetime import date
import pandas as pd
from sqlalchemy.orm import Session

from database.models import RawSI, RawSO, Dealer


def list_dealers(db: Session) -> list[str]:
    """Danh sách tên đại lý có trong Master Data, dùng cho bộ lọc."""
    rows = db.query(Dealer.dealer_name).order_by(Dealer.dealer_name).all()
    return [r[0] for r in rows]


def _period_label(d: pd.Series, granularity: str) -> pd.DataFrame:
    """Trả về DataFrame gồm period_key (dùng để sort/group) và period_label (hiển thị)."""
    dt = pd.to_datetime(d)
    if granularity == "year":
        key = dt.dt.year.astype(str)
        label = key
    elif granularity == "month":
        key = dt.dt.year * 100 + dt.dt.month
        label = dt.dt.strftime("T%m/%Y")
    else:  # week — ISO week
        iso = dt.dt.isocalendar()
        key = iso["year"] * 100 + iso["week"]
        label = "Tuần " + iso["week"].astype(str) + "/" + iso["year"].astype(str)
    return pd.DataFrame({"period_key": key, "period_label": label})


def dealer_period_trend(db: Session, dealers: list[str], granularity: str,
                         start: date = None, end: date = None) -> pd.DataFrame:
    """SI/SO gom theo kỳ (tuần/tháng/năm), tổng hợp cho toàn bộ đại lý đã chọn."""
    si_q = db.query(RawSI.txn_date, RawSI.si_quantity).filter(RawSI.dealer.in_(dealers))
    so_q = db.query(RawSO.txn_date, RawSO.so_quantity).filter(RawSO.dealer.in_(dealers))
    if start:
        si_q = si_q.filter(RawSI.txn_date >= start)
        so_q = so_q.filter(RawSO.txn_date >= start)
    if end:
        si_q = si_q.filter(RawSI.txn_date <= end)
        so_q = so_q.filter(RawSO.txn_date <= end)

    si_df = pd.DataFrame(si_q.all(), columns=["txn_date", "si_quantity"])
    so_df = pd.DataFrame(so_q.all(), columns=["txn_date", "so_quantity"])

    if si_df.empty and so_df.empty:
        return pd.DataFrame(columns=["period_key", "period_label", "si", "so"])

    if not si_df.empty:
        si_df = pd.concat([si_df, _period_label(si_df["txn_date"], granularity)], axis=1)
        si_g = si_df.groupby(["period_key", "period_label"], as_index=False)["si_quantity"].sum()
        si_g = si_g.rename(columns={"si_quantity": "si"})
    else:
        si_g = pd.DataFrame(columns=["period_key", "period_label", "si"])

    if not so_df.empty:
        so_df = pd.concat([so_df, _period_label(so_df["txn_date"], granularity)], axis=1)
        so_g = so_df.groupby(["period_key", "period_label"], as_index=False)["so_quantity"].sum()
        so_g = so_g.rename(columns={"so_quantity": "so"})
    else:
        so_g = pd.DataFrame(columns=["period_key", "period_label", "so"])

    merged = pd.merge(si_g, so_g, on=["period_key", "period_label"], how="outer").fillna(0)
    merged = merged.sort_values("period_key").reset_index(drop=True)
    merged["si"] = merged["si"].astype(float)
    merged["so"] = merged["so"].astype(float)
    return merged


def dealer_breakdown(db: Session, dealers: list[str], start: date = None, end: date = None) -> pd.DataFrame:
    """SI/SO tổng theo từng đại lý riêng lẻ, trong khoảng thời gian đã chọn (hoặc toàn bộ)."""
    si_q = db.query(RawSI.dealer, RawSI.si_quantity).filter(RawSI.dealer.in_(dealers))
    so_q = db.query(RawSO.dealer, RawSO.so_quantity).filter(RawSO.dealer.in_(dealers))
    if start:
        si_q = si_q.filter(RawSI.txn_date >= start)
        so_q = so_q.filter(RawSO.txn_date >= start)
    if end:
        si_q = si_q.filter(RawSI.txn_date <= end)
        so_q = so_q.filter(RawSO.txn_date <= end)

    si_df = pd.DataFrame(si_q.all(), columns=["dealer", "si_quantity"])
    so_df = pd.DataFrame(so_q.all(), columns=["dealer", "so_quantity"])

    si_g = si_df.groupby("dealer", as_index=False)["si_quantity"].sum().rename(columns={"si_quantity": "si"}) \
        if not si_df.empty else pd.DataFrame(columns=["dealer", "si"])
    so_g = so_df.groupby("dealer", as_index=False)["so_quantity"].sum().rename(columns={"so_quantity": "so"}) \
        if not so_df.empty else pd.DataFrame(columns=["dealer", "so"])

    merged = pd.merge(si_g, so_g, on="dealer", how="outer").fillna(0)
    merged["si"] = merged["si"].astype(float)
    merged["so"] = merged["so"].astype(float)
    return merged.sort_values("si", ascending=False).reset_index(drop=True)


def dealer_sku_breakdown(db: Session, dealers: list[str], start: date = None, end: date = None,
                          top_n: int = 15, sort_by: str = "so") -> pd.DataFrame:
    """SI/SO theo SKU, trong phạm vi đại lý + khoảng thời gian đã chọn.

    sort_by="so" (mặc định, hành vi cũ): Top SKU theo Sell-out.
    sort_by="abs_delta": Top SKU theo |Sell-in - Sell-out| lớn nhất — dùng cho
    biểu đồ "SKU chênh lệch SI-SO lớn nhất" khi chỉ xem 1 đại lý.
    """
    si_q = db.query(RawSI.sku_code, RawSI.sku_name, RawSI.si_quantity).filter(RawSI.dealer.in_(dealers))
    so_q = db.query(RawSO.sku_code, RawSO.sku_name, RawSO.so_quantity).filter(RawSO.dealer.in_(dealers))
    if start:
        si_q = si_q.filter(RawSI.txn_date >= start)
        so_q = so_q.filter(RawSO.txn_date >= start)
    if end:
        si_q = si_q.filter(RawSI.txn_date <= end)
        so_q = so_q.filter(RawSO.txn_date <= end)

    si_df = pd.DataFrame(si_q.all(), columns=["sku_code", "sku_name", "si_quantity"])
    so_df = pd.DataFrame(so_q.all(), columns=["sku_code", "sku_name", "so_quantity"])

    si_g = si_df.groupby(["sku_code", "sku_name"], as_index=False)["si_quantity"].sum().rename(columns={"si_quantity": "si"}) \
        if not si_df.empty else pd.DataFrame(columns=["sku_code", "sku_name", "si"])
    so_g = so_df.groupby(["sku_code", "sku_name"], as_index=False)["so_quantity"].sum().rename(columns={"so_quantity": "so"}) \
        if not so_df.empty else pd.DataFrame(columns=["sku_code", "sku_name", "so"])

    merged = pd.merge(si_g, so_g, on=["sku_code", "sku_name"], how="outer").fillna(0)
    merged["si"] = merged["si"].astype(float)
    merged["so"] = merged["so"].astype(float)
    merged["delta"] = merged["si"] - merged["so"]

    if sort_by == "abs_delta":
        merged = merged.reindex(merged["delta"].abs().sort_values(ascending=False).index)
    else:
        merged = merged.sort_values("so", ascending=False)
    return merged.head(top_n).reset_index(drop=True)


def period_key_to_range(period_key, granularity: str):
    """Đổi period_key (int) về (start_date, end_date) thật, để lọc breakdown theo đúng 1 kỳ."""
    if period_key is None:
        return None, None
    period_key = int(period_key)
    if granularity == "year":
        y = period_key
        return date(y, 1, 1), date(y, 12, 31)
    if granularity == "month":
        y, m = divmod(period_key, 100)
        from calendar import monthrange
        return date(y, m, 1), date(y, m, monthrange(y, m)[1])
    # week (ISO)
    y, w = divmod(period_key, 100)
    return date.fromisocalendar(y, w, 1), date.fromisocalendar(y, w, 7)


def kpi_totals(trend_df: pd.DataFrame, period_key=None) -> dict:
    """SI/SO/ratio/delta — toàn bộ trend_df, hoặc chỉ 1 kỳ nếu period_key được truyền."""
    scope = trend_df if period_key is None else trend_df[trend_df["period_key"] == period_key]
    si = float(scope["si"].sum())
    so = float(scope["so"].sum())
    ratio = round((so / si) * 100, 1) if si > 0 else 0.0
    delta = si - so
    return dict(si=si, so=so, ratio=ratio, delta=delta)
