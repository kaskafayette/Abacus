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

    pending_count = queries.get_pending_count(conn)
    if pending_count > 0:
        st.sidebar.warning(f"⚠ {pending_count} transactions pending review")

    page = st.sidebar.radio(
        "Navigate",
        ["Home", "Browse / Search", "Ingest", "Normalize & Categorize", "Reports", "Maintenance"],
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

    if pending_count > 0:
        st.warning(
            f"You have **{pending_count}** transactions awaiting review. "
            "Go to **Normalize & Categorize** in the sidebar to continue."
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
