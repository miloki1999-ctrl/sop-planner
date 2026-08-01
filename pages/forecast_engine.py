version_name = st.text_input(
        "Tên Version",
        value=f"{target_period} Draft {datetime.now().strftime('%H%M')}",
    )

    if st.button("▶️ Chạy Forecast Engine", type="primary"):
        with st.spinner("Đang tính Forecast cho tất cả Dealer × SKU..."):
            with get_session() as db:
                try:
                    version = run_forecast_engine(
                        db, version_name=version_name, target_period=target_period, cutoff_date=cutoff_date,
                        method=method, username=user["username"],
                        dealer_filter=dealer_filter or None, brand_filter=brand_filter or None,
                        category_filter=category_filter or None,
                    )
                    db.add(AuditLog(table_name="forecast_versions", record_ref=version.version_name,
                                     action="INSERT", forecast_version=version.version_name,
                                     performed_by=user["username"], reason="Run Forecast Engine"))
                    st.session_state["last_forecast_version_id"] = version.record_id
                    st.success(
                        f"✅ Đã tạo version **{version.version_name}** — "
                        f"Total Forecast SO: **{version.total_forecast_so:,.0f}**"
                    )
                except Exception as e:
                    st.error(f"Lỗi khi chạy Forecast: {e}")
