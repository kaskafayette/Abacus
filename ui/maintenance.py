"""Maintenance pages — CRUD for lookup tables, transaction editing, database admin."""

import streamlit as st
import pandas as pd

from db import queries
from db.schema import DB_PATH


def maintenance_page(conn):
    st.title("Maintenance")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Payee Normalization", "Payee Metadata", "Category Master",
        "Edit Transactions", "Database",
    ])

    with tab1:
        _payee_normalization(conn)
    with tab2:
        _payee_metadata(conn)
    with tab3:
        _category_master(conn)
    with tab4:
        _edit_transactions(conn)
    with tab5:
        _database_admin(conn)


# ---------------------------------------------------------------------------
# Payee Normalization
# ---------------------------------------------------------------------------

def _payee_normalization(conn):
    st.subheader("Payee Normalization Rules")

    rows = queries.get_payee_normalizations(conn)
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        st.dataframe(
            df[["id", "search_pattern", "normalized_name", "payee_suffix"]],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No normalization rules yet.")

    # Add new
    with st.expander("Add New Rule"):
        with st.form("add_norm"):
            pattern = st.text_input("Search Pattern")
            name = st.text_input("Normalized Name")
            suffix = st.text_input("Payee Suffix (optional)")
            if st.form_submit_button("Add"):
                if pattern and name:
                    queries.insert_payee_normalization(conn, pattern, name, suffix or None)
                    st.success("Added.")
                    st.rerun()

    # Edit / Delete
    if rows:
        with st.expander("Edit / Delete"):
            row_id = st.number_input("Row ID to edit/delete", min_value=1, step=1, key="norm_id")
            col1, col2 = st.columns(2)
            with col1:
                with st.form("edit_norm"):
                    new_pattern = st.text_input("New Search Pattern", key="edit_norm_pat")
                    new_name = st.text_input("New Normalized Name", key="edit_norm_name")
                    new_suffix = st.text_input("New Payee Suffix", key="edit_norm_suf")
                    if st.form_submit_button("Update"):
                        queries.update_payee_normalization(
                            conn, row_id, new_pattern, new_name, new_suffix or None
                        )
                        st.success("Updated.")
                        st.rerun()
            with col2:
                if st.button("Delete", key="del_norm"):
                    queries.delete_payee_normalization(conn, row_id)
                    st.success("Deleted.")
                    st.rerun()


# ---------------------------------------------------------------------------
# Payee Metadata
# ---------------------------------------------------------------------------

def _payee_metadata(conn):
    st.subheader("Payee Metadata")

    rows = queries.get_payee_metadata(conn)
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No payee metadata yet.")

    category_names = queries.get_category_names(conn)
    payor_options = ["", "David", "Debra", "Both", "Unknown"]

    with st.expander("Add / Update Payee Metadata"):
        with st.form("upsert_meta"):
            name = st.text_input("Normalized Name")
            cat = st.selectbox("Category Override", [""] + category_names, key="meta_cat")
            subcat = st.text_input("Subcategory Override", key="meta_subcat")
            tax = st.text_input("Tax Flags Override", key="meta_tax")
            payor = st.selectbox("Payor", payor_options, key="meta_payor")
            note = st.text_input("Note", key="meta_note")
            if st.form_submit_button("Save"):
                if name:
                    queries.upsert_payee_metadata(
                        conn,
                        normalized_name=name,
                        category_override=cat or None,
                        subcategory_override=subcat or None,
                        tax_flags_override=tax or None,
                        payor=payor or None,
                        note=note or None,
                    )
                    st.success("Saved.")
                    st.rerun()

    if rows:
        with st.expander("Delete"):
            del_id = st.number_input("Row ID to delete", min_value=1, step=1, key="meta_del_id")
            if st.button("Delete", key="del_meta"):
                queries.delete_payee_metadata(conn, del_id)
                st.success("Deleted.")
                st.rerun()


# ---------------------------------------------------------------------------
# Category Master
# ---------------------------------------------------------------------------

def _category_master(conn):
    st.subheader("Category Master")

    rows = queries.get_categories(conn)
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        st.dataframe(
            df[["id", "category", "subcategory", "tax_flag_default"]],
            use_container_width=True, hide_index=True,
        )

    with st.expander("Add New Category"):
        with st.form("add_cat"):
            cat = st.text_input("Category")
            subcat = st.text_input("Subcategory (optional)")
            tax_default = st.text_input("Tax Flag Default (optional)")
            if st.form_submit_button("Add"):
                if cat:
                    queries.upsert_category(conn, cat, subcat or None, tax_default or None)
                    st.success("Added.")
                    st.rerun()

    if rows:
        st.warning(
            "Editing category or subcategory names here does **not** cascade to existing transactions."
        )
        with st.expander("Delete"):
            del_id = st.number_input("Category ID to delete", min_value=1, step=1, key="cat_del_id")
            if st.button("Delete", key="del_cat"):
                queries.delete_category(conn, del_id)
                st.success("Deleted.")
                st.rerun()


# ---------------------------------------------------------------------------
# Edit Transactions
# ---------------------------------------------------------------------------

def _edit_transactions(conn):
    st.subheader("Edit Transactions")

    col1, col2, col3 = st.columns(3)
    search = col1.text_input("Search (payee, description, note)")
    sources = queries.get_distinct_sources(conn)
    source_filter = col2.selectbox("Source", [""] + sources)
    status_filter = col3.selectbox("Status", ["", "pending", "confirmed", "needs_review"])

    col4, col5 = st.columns(2)
    start = col4.date_input("From date", value=None, key="txn_start")
    end = col5.date_input("To date", value=None, key="txn_end")

    rows = queries.get_transactions(
        conn,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
        source=source_filter or None,
        search=search or None,
        status=status_filter or None,
    )

    if not rows:
        st.info("No transactions match the filters.")
        return

    st.caption(f"{len(rows)} transaction(s)")

    # Paginate
    page_size = 50
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, key="txn_page")
    page_rows = rows[(page - 1) * page_size : page * page_size]

    df = pd.DataFrame([dict(r) for r in page_rows])
    st.dataframe(
        df[["id", "date", "source", "payee", "category", "subcategory", "amount", "status"]],
        use_container_width=True, hide_index=True,
    )

    # Edit single transaction
    with st.expander("Edit a Transaction"):
        txn_id = st.number_input("Transaction ID", min_value=1, step=1, key="edit_txn_id")
        category_names = queries.get_category_names(conn)
        payor_options = ["", "David", "Debra", "Both", "Unknown"]
        status_options = ["pending", "confirmed", "needs_review"]

        with st.form("edit_txn"):
            new_payee = st.text_input("Payee", key="edit_payee")
            new_cat = st.selectbox("Category", [""] + category_names, key="edit_cat")
            new_subcat = st.text_input("Subcategory", key="edit_subcat")
            new_tax = st.text_input("Tax Flags", key="edit_tax")
            new_payor = st.selectbox("Payor", payor_options, key="edit_payor")
            new_note = st.text_input("Note", key="edit_note")
            new_status = st.selectbox("Status", status_options, key="edit_status")

            if st.form_submit_button("Save Changes"):
                updates = {}
                if new_payee:
                    updates["payee"] = new_payee
                if new_cat:
                    updates["category"] = new_cat
                if new_subcat:
                    updates["subcategory"] = new_subcat
                if new_tax:
                    updates["tax_flags"] = new_tax
                if new_payor:
                    updates["payor"] = new_payor
                if new_note:
                    updates["note"] = new_note
                updates["status"] = new_status
                updates["overridden"] = 1

                queries.update_transaction(conn, txn_id, **updates)
                st.success(f"Transaction {txn_id} updated.")
                st.rerun()


# ---------------------------------------------------------------------------
# Database Admin
# ---------------------------------------------------------------------------

def _database_admin(conn):
    st.subheader("Database")

    st.write(f"**File:** `{DB_PATH}`")

    if DB_PATH.exists():
        size_mb = DB_PATH.stat().st_size / (1024 * 1024)
        st.write(f"**Size:** {size_mb:.2f} MB")

    counts = queries.get_table_counts(conn)
    df = pd.DataFrame(
        [{"Table": k, "Rows": v} for k, v in counts.items()]
    )
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Purge Transaction Data")
    st.warning(
        "This will **delete all transactions** but preserve all lookup tables "
        "(payee rules, categories, source maps, templates)."
    )

    confirm = st.text_input(
        'Type **DELETE ALL TRANSACTIONS** to confirm:',
        key="purge_confirm",
    )
    if st.button("Purge", type="primary"):
        if confirm == "DELETE ALL TRANSACTIONS":
            count = queries.purge_transactions(conn)
            st.success(f"Purged {count} transaction(s).")
            st.rerun()
        else:
            st.error("Confirmation phrase does not match.")
