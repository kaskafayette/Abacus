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

    # Query-param nav: the anti-illusion warning callouts (sidebar and home
    # page) embed their "Show me these" / "Show me the list" call-to-action
    # as an <a href="?nav=Diagnostics"> anchor INSIDE the colored oval,
    # because Streamlit's st.warning() doesn't allow a Streamlit button
    # in the same visual box. Clicking the anchor updates the URL, this
    # handler catches it, sets nav_page, wipes the qp so a refresh won't
    # keep re-routing, and reruns so the sidebar radio picks up the new
    # page. Must run BEFORE the sidebar radio is instantiated.
    _nav_qp = st.query_params.get("nav")
    if _nav_qp:
        st.session_state["nav_page"] = _nav_qp
        del st.query_params["nav"]
        st.rerun()

    # Pre-route flush: if the user was editing a Note in the Interactive
    # Category Summary and clicked a sidebar radio to leave, the cell blurs
    # (thanks to stopEditingWhenCellsLoseFocus) which commits it and fires a
    # Streamlit rerun. On that rerun the reports page will NOT re-render, so
    # the in-page save code would never run. Flushing here — before the
    # sidebar radio is read and the router picks the new page — guarantees
    # the pending edit lands in the DB either way.
    from ui.reports import flush_ics_note_edits
    flush_ics_note_edits(conn)

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
        # Custom callout so the "Show me these" call-to-action lives INSIDE
        # the yellow oval, not as a separate button below it. Clicking the
        # anchor routes through the query-param handler at the top of main().
        st.sidebar.markdown(
            f"""
<div style="
    background-color: rgba(255, 227, 18, 0.14);
    border-left: 4px solid #ffd400;
    padding: 0.6rem 0.75rem;
    border-radius: 0.35rem;
    font-size: 0.88rem;
    line-height: 1.35;
    color: inherit;
    margin-bottom: 0.5rem;
">
⚠ {' · '.join(parts)}
<div style="margin-top: 0.55rem;">
<a href="?nav=Diagnostics" target="_self" style="
    background-color: #ffd400;
    color: #262730;
    padding: 0.3rem 0.65rem;
    border-radius: 0.3rem;
    text-decoration: none;
    font-weight: 600;
    font-size: 0.82rem;
    display: inline-block;
">Show me these →</a>
</div>
</div>
""",
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.success("✓ 0 unresolved · 0 missing-subcat")
    # Kept for backward-compat callers that still reference pending_count.
    pending_count = queries.get_pending_count(conn)

    page = st.sidebar.radio(
        "Navigate",
        ["Home", "Diagnostics", "Browse / Search", "Ingest",
         "Normalize & Categorize", "Non-cash Donations", "Reports",
         "Maintenance"],
        key="nav_page",       # keyed so anti-illusion banners can jump here
        label_visibility="collapsed",
    )

    if page == "Home":
        _home_page(conn, pending_count)
    elif page == "Diagnostics":
        from ui.diagnostics import diagnostics_page
        diagnostics_page(conn)
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
        # HTML rather than markdown so we can embed the CTA anchor INSIDE the
        # same colored callout (Streamlit's st.warning() doesn't allow a
        # Streamlit button in the same visual box). The anchor routes through
        # the query-param handler at the top of main().
        li_parts = []
        if unc_count > 0:
            li_parts.append(
                f"<li><strong>{unc_count}</strong> transactions unresolved "
                f"(pending or needs_review), ${float(unc_abs):,.2f} absolute "
                f"value — work through them on <strong>Normalize &amp; "
                f"Categorize</strong>.</li>"
            )
        if ms_count > 0:
            li_parts.append(
                f"<li><strong>{ms_count}</strong> transactions have a "
                f"category but no subcategory where one is required, "
                f"${float(ms_abs):,.2f} absolute value — fix them on "
                f"<strong>Maintenance → Edit Transactions</strong> (they "
                f"look done but aren't fully classified).</li>"
            )
        st.markdown(
            f"""
<div style="
    background-color: rgba(255, 227, 18, 0.14);
    border-left: 4px solid #ffd400;
    padding: 1rem 1.1rem 1.1rem 1.1rem;
    border-radius: 0.5rem;
    margin-bottom: 1rem;
    color: inherit;
">
<div style="font-weight: 700; font-size: 1.02rem; margin-bottom: 0.4rem;">
⚠ Ledger is not clean:
</div>
<ul style="margin-top: 0.35rem; margin-bottom: 0.85rem; padding-left: 1.5rem;">
{''.join(li_parts)}
</ul>
<a href="?nav=Diagnostics" target="_self" style="
    background-color: #ff4b4b;
    color: white;
    padding: 0.55rem 1.1rem;
    border-radius: 0.5rem;
    text-decoration: none;
    font-weight: 600;
    font-size: 0.95rem;
    display: inline-block;
">▶ Show me the list with explanations</a>
</div>
""",
            unsafe_allow_html=True,
        )
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
