"""Abacus — Streamlit entry point."""

import streamlit as st
from db.schema import init_db, DB_PATH
from db import queries

st.set_page_config(page_title="Abacus", layout="wide")


def main():
    # Initialize database
    if "conn" not in st.session_state:
        conn, is_new = init_db()
        st.session_state.conn = conn
        st.session_state.db_is_new = is_new

    conn = st.session_state.conn

    # Sidebar navigation
    st.sidebar.title("Abacus")
    st.sidebar.caption(f"Database: {DB_PATH.name}")

    # Anti-illusion: sidebar shows GLOBAL data-quality warnings —
    # unresolved (pending/needs_review) AND missing-subcategory rows.
    # Persistent across every page so a scoped "0 unconfirmed" can never
    # create the illusion the whole ledger is clean.
    unc_count, unc_abs = queries.get_unconfirmed_count(conn)
    ms_count, ms_abs = queries.get_missing_subcategory_count(conn)
    if unc_count > 0 or ms_count > 0:
        parts = []
        if unc_count:
            parts.append(f"{unc_count} unresolved (${float(unc_abs):,.0f})")
        if ms_count:
            parts.append(f"{ms_count} missing-subcat (${float(ms_abs):,.0f})")
        st.sidebar.warning("⚠ " + " · ".join(parts))
    else:
        st.sidebar.success("✓ 0 unresolved · 0 missing-subcat")
    # Kept for backward-compat callers that still reference pending_count.
    pending_count = queries.get_pending_count(conn)

    page = st.sidebar.radio(
        "Navigate",
        ["Home", "Browse / Search", "Ingest", "Normalize & Categorize",
         "Non-cash Donations", "Reports", "Maintenance"],
        label_visibility="collapsed",
    )

    if page == "Home":
        _home_page(conn, pending_count)
    elif page == "Browse / Search":
        from ui.browse import browse_page
        browse_page(conn)
    elif page == "Ingest":
        from ui.process import process_page
        process_page(conn)
    elif page == "Normalize & Categorize":
        from ui.normalize import normalize_page
        normalize_page(conn)
    elif page == "Non-cash Donations":
        from ui.donations import donations_page
        donations_page(conn)
    elif page == "Reports":
        from ui.reports import reports_page
        reports_page(conn)
    elif page == "Maintenance":
        from ui.maintenance import maintenance_page
        maintenance_page(conn)


def _home_page(conn, pending_count):
    st.title("Abacus")

    if st.session_state.get("db_is_new"):
        st.info(
            "A new database has been created. Populate lookup tables under "
            "**Maintenance** before processing files."
        )
    else:
        st.success(f"Using existing database: {DB_PATH.name}")

    # Anti-illusion: prominent warning about global data-quality issues.
    # Covers BOTH unresolved status AND missing-subcategory rows — either
    # class is a way the ledger could look done but not be.
    unc_count, unc_abs = queries.get_unconfirmed_count(conn)
    ms_count, ms_abs = queries.get_missing_subcategory_count(conn)
    if unc_count > 0 or ms_count > 0:
        parts = []
        if unc_count > 0:
            parts.append(
                f"- **{unc_count}** transactions unresolved (pending or "
                f"needs_review), ${float(unc_abs):,.2f} absolute value — "
                f"work through them on **Normalize & Categorize**."
            )
        if ms_count > 0:
            parts.append(
                f"- **{ms_count}** transactions have a category but no "
                f"subcategory where one is required, ${float(ms_abs):,.2f} "
                f"absolute value — fix them on **Maintenance → Edit "
                f"Transactions** (they look done but aren't fully classified)."
            )
        st.warning("**⚠ Ledger is not clean:**\n\n" + "\n".join(parts))
    else:
        st.success(
            "✓ Ledger is clean — every transaction is confirmed AND fully "
            "categorized (no missing subcategories)."
        )

    # Database stats
    st.subheader("Database Status")
    counts = queries.get_table_counts(conn)
    col1, col2, col3 = st.columns(3)
    col1.metric("Transactions", counts["transactions"])
    col2.metric("Payee Rules", counts["payee_normalization"])
    col3.metric("Categories", counts["categories"])

    col4, col5, col6 = st.columns(3)
    col4.metric("Payee Metadata", counts["payee_metadata"])
    col5.metric("Source Accounts", counts["source_file_map"])
    col6.metric("Processed Files", counts["processed_files"])


if __name__ == "__main__":
    main()
