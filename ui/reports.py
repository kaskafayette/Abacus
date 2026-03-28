"""Reports page — ad-hoc reports and Excel export."""

import streamlit as st
from datetime import date

from db import queries
from processing.reports import generate_monthly_pdf, generate_excel_export


def reports_page(conn):
    st.title("Reports")

    tab1, tab2 = st.tabs(["Ad-Hoc Reports", "Excel Export"])

    with tab1:
        _adhoc_reports(conn)

    with tab2:
        _excel_export(conn)


def _adhoc_reports(conn):
    st.subheader("Run Ad-Hoc Report")

    col1, col2 = st.columns(2)
    start = col1.date_input("Start date", value=date.today().replace(day=1), key="adhoc_start")
    end = col2.date_input("End date", value=date.today(), key="adhoc_end")

    start_str = start.isoformat()
    end_str = end.isoformat()

    st.markdown("**Select reports:**")
    do_monthly = st.checkbox("Category Summary + Payee Summary + Detail + Tax Items", value=True, key="adhoc_monthly")

    if st.button("Generate", key="adhoc_gen"):
        generated = []
        if do_monthly:
            title = f"Ad-Hoc Report — {start_str} to {end_str}"
            path = generate_monthly_pdf(conn, start_str, end_str, title=title)
            generated.append(path)

        if generated:
            st.success("Reports generated:")
            for p in generated:
                st.write(f"  📄 `{p}`")
        else:
            st.info("No reports selected.")


def _excel_export(conn):
    st.subheader("Export to Excel")

    col1, col2 = st.columns(2)
    start = col1.date_input("Start date", value=date.today().replace(day=1), key="excel_start")
    end = col2.date_input("End date", value=date.today(), key="excel_end")

    if st.button("Export", key="excel_gen"):
        path = generate_excel_export(conn, start.isoformat(), end.isoformat())
        st.success(f"Exported: `{path}`")
