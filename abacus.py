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

    # Global unresolved count (pending + needs_review, split parents excluded).
    # Shown persistently in the sidebar so a scoped "0 unconfirmed" in one
    # report can never create the illusion the whole ledger is clean.
    unc_count, unc_abs = queries.get_unconfirmed_count(conn)
    if unc_count > 0:
        st.sidebar.warning(
            f"⚠ **{unc_count}** transaction(s) unresolved "
            f"(${float(unc_abs):,.0f} abs) — pending or needs_review"
        )
    else:
        st.sidebar.success("✓ 0 unresolved transactions")
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

    # Anti-illusion prominent warning: any unresolved (pending or needs_review)
    # rows, regardless of month, so the reader knows the ledger isn't finished.
    unc_count, unc_abs = queries.get_unconfirmed_count(conn)
    if unc_count > 0:
        st.warning(
            f"**⚠ {unc_count}** transactions across the entire database are "
            f"NOT yet confirmed (status = pending or needs_review), totaling "
            f"**${float(unc_abs):,.2f}** in absolute value. Go to "
            f"**Normalize & Categorize** to work through them."
        )
    else:
        st.success(
            "✓ All transactions are confirmed. Nothing pending or needing review."
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
