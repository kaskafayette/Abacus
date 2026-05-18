"""Reports page -monthly reports, ad-hoc reports, and Excel export."""

import calendar
import streamlit as st
from datetime import date

from db import queries
from processing.reports import (
    generate_monthly_pdf, generate_excel_export, OUTPUT_DIR,
    MODE_CHOICES, MODE_LABELS, MODE_FINALIZED, MODE_DRAFT, MODE_ALL,
)


def _get_date_range(conn):
    """Get the min/max transaction dates from the database for sensible defaults."""
    row = conn.execute(
        "SELECT MIN(date) as min_date, MAX(date) as max_date FROM transactions"
    ).fetchone()
    if row and row["min_date"] and row["max_date"]:
        return (
            date.fromisoformat(row["min_date"]),
            date.fromisoformat(row["max_date"]),
        )
    if st.session_state.get("period_start") and st.session_state.get("period_end"):
        return st.session_state["period_start"], st.session_state["period_end"]
    return date.today().replace(day=1), date.today()


def _scope_selector(key: str) -> str:
    """Render the report-scope selectbox. Returns the mode constant."""
    label = st.selectbox(
        "Scope",
        MODE_CHOICES,
        format_func=lambda m: MODE_LABELS[m],
        index=MODE_CHOICES.index(MODE_FINALIZED),
        key=key,
        help=(
            "Finalized only: status='confirmed' rows (used for tax filings, "
            "final reports). Draft only: status in {'pending','needs_review'} "
            "(used to see what's outstanding). All transactions: everything "
            "in the period regardless of status."
        ),
    )
    return label


def _count_in_scope(conn, start_str: str, end_str: str, mode: str) -> int:
    """Count rows in range that match the mode filter."""
    if mode == MODE_FINALIZED:
        sc = "status = 'confirmed'"
    elif mode == MODE_DRAFT:
        sc = "status IN ('pending', 'needs_review')"
    else:
        sc = "1=1"
    row = conn.execute(
        f"SELECT COUNT(*) as cnt FROM transactions "
        f"WHERE date >= ? AND date <= ? AND {sc}",
        (start_str, end_str),
    ).fetchone()
    return row["cnt"]


def reports_page(conn):
    st.title("Reports")

    tab1, tab2, tab3 = st.tabs(["Monthly Report", "Ad-Hoc Report", "Excel Export"])

    with tab1:
        _monthly_report(conn)

    with tab2:
        _adhoc_reports(conn)

    with tab3:
        _excel_export(conn)


def _monthly_report(conn):
    st.subheader("Monthly Report")
    st.caption(
        "Generates a single PDF with four sections:\n"
        "1. **Category Summary** -month and YTD totals by category/subcategory\n"
        "2. **Payee Summary** -month totals grouped by payee\n"
        "3. **Transaction Detail** -every transaction for the month\n"
        "4. **Tax Items** -flagged transactions grouped by tax category, with month and YTD subtotals\n\n"
        "Output is saved to the `output/` folder."
    )

    default_start, default_end = _get_date_range(conn)

    # Default to last full month (e.g. if today is April 4, default to March)
    today = date.today()
    if today.month == 1:
        last_full_month = 12
        last_full_year = today.year - 1
    else:
        last_full_month = today.month - 1
        last_full_year = today.year

    col1, col2 = st.columns(2)
    years = list(range(default_start.year, today.year + 1))
    default_year_idx = years.index(last_full_year) if last_full_year in years else len(years) - 1
    selected_year = col1.selectbox("Year", years, index=default_year_idx, key="rpt_year")
    months = list(range(1, 13))
    default_month_idx = last_full_month - 1 if selected_year == last_full_year else 11
    selected_month = col2.selectbox(
        "Month", months,
        format_func=lambda m: calendar.month_name[m],
        index=default_month_idx,
        key="rpt_month",
    )

    # Compute month range
    last_day = calendar.monthrange(selected_year, selected_month)[1]
    month_start = date(selected_year, selected_month, 1)
    month_end = date(selected_year, selected_month, last_day)

    month_start_str = month_start.isoformat()
    month_end_str = month_end.isoformat()

    # Scope selector
    mode = _scope_selector("rpt_mode")

    # Transaction count for chosen scope
    month_count = _count_in_scope(conn, month_start_str, month_end_str, mode)
    ytd_start_str = f"{selected_year}-01-01"
    ytd_count = _count_in_scope(conn, ytd_start_str, month_end_str, mode)

    st.info(
        f"**{calendar.month_name[selected_month]} {selected_year}** ({MODE_LABELS[mode]}): "
        f"{month_count} transaction(s) for the month, {ytd_count} YTD."
    )

    st.markdown("**Select sections to include:**")
    do_cat = st.checkbox("Category Summary (Month & YTD)", value=True, key="rpt_cat")
    do_payee = st.checkbox("Payee Summary (Month)", value=True, key="rpt_payee")
    do_detail = st.checkbox("Transaction Detail (Month)", value=True, key="rpt_detail")
    do_tax = st.checkbox("Tax Items (Month & YTD)", value=True, key="rpt_tax")

    if st.button("Generate Monthly Report", type="primary", key="gen_monthly"):
        if month_count == 0:
            st.warning(f"No transactions in scope ({MODE_LABELS[mode]}) for this month. Nothing to report.")
            return

        sections = []
        if do_cat:
            sections.append("category_summary")
        if do_payee:
            sections.append("payee_summary")
        if do_detail:
            sections.append("transaction_detail")
        if do_tax:
            sections.append("tax_items")

        if not sections:
            st.warning("No sections selected.")
            return

        try:
            title = f"Monthly Report - {calendar.month_name[selected_month]} {selected_year}"
            path = generate_monthly_pdf(conn, month_start_str, month_end_str,
                                        title=title, sections=sections, mode=mode)
            st.success(f"Report generated: `{path}`")
        except Exception as e:
            st.error(f"Report generation failed: {e}")


def _adhoc_reports(conn):
    st.subheader("Ad-Hoc Report")
    st.caption("Generate a report for any date range.")

    default_start, default_end = _get_date_range(conn)

    col1, col2 = st.columns(2)
    start = col1.date_input("Start date", value=default_start, key="adhoc_start")
    end = col2.date_input("End date", value=default_end, key="adhoc_end")

    if start > end:
        st.error("Start date must be before end date.")
        return

    start_str = start.isoformat()
    end_str = end.isoformat()

    mode = _scope_selector("adhoc_mode")
    txn_count = _count_in_scope(conn, start_str, end_str, mode)
    st.caption(f"{txn_count} transaction(s) in scope ({MODE_LABELS[mode]})")

    if st.button("Generate", key="adhoc_gen"):
        if txn_count == 0:
            st.warning(f"No transactions in scope ({MODE_LABELS[mode]}) in the selected range.")
            return
        try:
            title = f"Ad-Hoc Report -{start_str} to {end_str}"
            path = generate_monthly_pdf(conn, start_str, end_str, title=title, mode=mode)
            st.success(f"Report generated: `{path}`")
        except Exception as e:
            st.error(f"Report generation failed: {e}")


def _excel_export(conn):
    st.subheader("Export to Excel")

    default_start, default_end = _get_date_range(conn)

    col1, col2 = st.columns(2)
    start = col1.date_input("Start date", value=default_start, key="excel_start")
    end = col2.date_input("End date", value=default_end, key="excel_end")

    if start > end:
        st.error("Start date must be before end date.")
        return

    start_str = start.isoformat()
    end_str = end.isoformat()

    mode = _scope_selector("excel_mode")
    txn_count = _count_in_scope(conn, start_str, end_str, mode)
    st.caption(f"{txn_count} transaction(s) in scope ({MODE_LABELS[mode]})")

    if st.button("Export", key="excel_gen"):
        if txn_count == 0:
            st.warning(f"No transactions in scope ({MODE_LABELS[mode]}) in the selected range.")
            return
        try:
            path = generate_excel_export(conn, start_str, end_str, mode=mode)
            st.success(f"Exported: `{path}`")
        except Exception as e:
            st.error(f"Export failed: {e}")
