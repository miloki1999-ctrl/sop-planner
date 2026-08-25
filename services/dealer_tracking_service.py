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
