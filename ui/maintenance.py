"""Maintenance pages — CRUD for lookup tables, transaction editing, database admin."""

import streamlit as st
import pandas as pd
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode

from db import queries
from db.schema import DB_PATH


def maintenance_page(conn):
    st.title("Maintenance")

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "Source Accounts", "Payee Normalization", "Payee Metadata",
        "Category Master", "Rename / Merge Payees", "Edit Transactions", "Database",
    ])

    with tab1:
        _source_accounts(conn)
    with tab2:
        _payee_normalization(conn)
    with tab3:
        _payee_metadata(conn)
    with tab4:
        _category_master(conn)
    with tab5:
        _rename_merge_payees(conn)
    with tab6:
        _edit_transactions(conn)
    with tab7:
        _database_admin(conn)


# ---------------------------------------------------------------------------
# Source Accounts
# ---------------------------------------------------------------------------

def _source_accounts(conn):
    st.subheader("Source Accounts")

    rows = queries.get_source_file_map(conn)
    if not rows:
        st.info("No source accounts configured yet.")
        return

    df = pd.DataFrame([dict(r) for r in rows])

    gb = GridOptionsBuilder.from_dataframe(df[["id", "source_prefix", "source_label", "nickname", "account_type"]])
    gb.configure_default_column(resizable=True, sortable=True, editable=False)
    gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)
    gb.configure_column("id", width=50)
    gb.configure_column("source_prefix", width=130)
    gb.configure_column("source_label", width=130)
    gb.configure_column("nickname", editable=True, width=200)
    gb.configure_column("account_type", editable=True, width=120,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": ["checking", "credit_card"]})

    grid_response = AgGrid(
        df[["id", "source_prefix", "source_label", "nickname", "account_type"]],
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.VALUE_CHANGED,
        fit_columns_on_grid_load=True,
        height=300,
    )

    if st.button("Save Changes", key="save_sources"):
        edited = grid_response["data"]
        for _, row in edited.iterrows():
            queries.upsert_source_file_map(
                conn, row["source_prefix"], row["source_label"],
                row["nickname"] or None, row["account_type"],
            )
        st.success("Saved.")
        st.rerun()


# ---------------------------------------------------------------------------
# Payee Normalization
# ---------------------------------------------------------------------------

def _payee_normalization(conn):
    st.subheader("Payee Normalization Rules")

    rows = queries.get_payee_normalizations(conn)
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])

        gb = GridOptionsBuilder.from_dataframe(df[["id", "search_pattern", "normalized_name", "payee_suffix"]])
        gb.configure_default_column(resizable=True, sortable=True, editable=False)
        gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)
        gb.configure_column("id", width=50)
        gb.configure_column("search_pattern", editable=True, width=200)
        gb.configure_column("normalized_name", editable=True, width=200)
        gb.configure_column("payee_suffix", editable=True, width=150)

        grid_response = AgGrid(
            df[["id", "search_pattern", "normalized_name", "payee_suffix"]],
            gridOptions=gb.build(),
            update_mode=GridUpdateMode.VALUE_CHANGED,
            fit_columns_on_grid_load=True,
            height=400,
        )

        col1, col2 = st.columns(2)
        if col1.button("Save Changes", key="save_norm"):
            edited = grid_response["data"]
            for _, row in edited.iterrows():
                queries.update_payee_normalization(
                    conn, int(row["id"]),
                    row["search_pattern"], row["normalized_name"],
                    row["payee_suffix"] if pd.notna(row["payee_suffix"]) and row["payee_suffix"] else None,
                )
            st.success("Saved.")
            st.rerun()

        if col2.button("Delete Selected", key="del_norm"):
            sel = grid_response.get("selected_rows")
            if sel is not None and len(sel) > 0:
                for _, row in sel.iterrows():
                    queries.delete_payee_normalization(conn, int(row["id"]))
                st.success("Deleted.")
                st.rerun()
    else:
        st.info("No normalization rules yet.")

    # Add new
    with st.expander("Add New Rule"):
        with st.form("add_norm"):
            pattern = st.text_input("Search Pattern")
            name = st.text_input("Normalized Name")
            suffix = st.text_input("Payee Suffix (optional)")
            col1, col2 = st.columns(2)
            save = col1.form_submit_button("Add")
            cancel = col2.form_submit_button("Cancel")
        if save and pattern and name:
            queries.insert_payee_normalization(conn, pattern, name, suffix or None)
            st.success("Added.")
            st.rerun()
        if cancel:
            st.rerun()

    # Apply rules to all existing transactions
    st.divider()
    st.caption("Re-run all normalization rules against every transaction in the database.")
    if st.button("Apply Rules to All Transactions", key="apply_norm_all"):
        all_rules = queries.get_payee_normalizations(conn)
        all_txns = conn.execute("SELECT id, description_raw FROM transactions").fetchall()
        updated = 0
        for txn in all_txns:
            raw = txn["description_raw"]
            raw_lower = raw.lower()
            for rule in all_rules:
                if rule["search_pattern"].lower() in raw_lower:
                    from processing.normalize import detect_via
                    via = detect_via(raw)
                    updates = {"payee": rule["normalized_name"]}
                    if via:
                        updates["via"] = via
                    queries.update_transaction(conn, txn["id"], **updates)
                    updated += 1
                    break
        st.success(f"Updated **{updated}** transaction(s).")


# ---------------------------------------------------------------------------
# Payee Metadata
# ---------------------------------------------------------------------------

def _payee_metadata(conn):
    st.subheader("Payee Metadata")

    rows = queries.get_payee_metadata(conn)
    category_names = queries.get_category_names(conn)
    payor_options = ["", "David", "Debra", "Both", "Unknown"]

    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        # Replace None with empty string for display
        for col in df.columns:
            df[col] = df[col].fillna("")

        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_default_column(resizable=True, sortable=True, editable=False)
        gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)
        gb.configure_column("id", width=50)
        gb.configure_column("normalized_name", width=180)
        gb.configure_column("category_override", editable=True, width=150,
                            cellEditor="agSelectCellEditor",
                            cellEditorParams={"values": [""] + category_names})
        gb.configure_column("subcategory_override", editable=True, width=150)
        tax_flag_values = [
            "", "Tax-reportable", "Reimbursable", "Capital Improvements",
            "Home Office", "Donations - Deductible", "Medical", "Business Expense",
            "Business Expense, Reimbursable",
            "Business Expense, Home Office",
        ]
        gb.configure_column("tax_flags_override", editable=True, width=160,
                            cellEditor="agSelectCellEditor",
                            cellEditorParams={"values": tax_flag_values})
        gb.configure_column("payor", editable=True, width=100,
                            cellEditor="agSelectCellEditor",
                            cellEditorParams={"values": payor_options})
        gb.configure_column("note", editable=True, width=150)

        grid_response = AgGrid(
            df,
            gridOptions=gb.build(),
            update_mode=GridUpdateMode.VALUE_CHANGED,
            fit_columns_on_grid_load=True,
            height=400,
        )

        col1, col2 = st.columns(2)
        if col1.button("Save Changes", key="save_meta"):
            edited = grid_response["data"]
            for _, row in edited.iterrows():
                queries.upsert_payee_metadata(
                    conn,
                    normalized_name=row["normalized_name"],
                    category_override=row["category_override"] or None,
                    subcategory_override=row["subcategory_override"] or None,
                    tax_flags_override=row["tax_flags_override"] or None,
                    payor=row["payor"] or None,
                    note=row["note"] or None,
                )
            st.success("Saved.")
            st.rerun()

        if col2.button("Delete Selected", key="del_meta"):
            sel = grid_response.get("selected_rows")
            if sel is not None and len(sel) > 0:
                for _, row in sel.iterrows():
                    queries.delete_payee_metadata(conn, int(row["id"]))
                st.success("Deleted.")
                st.rerun()
    else:
        st.info("No payee metadata yet.")

    with st.expander("Add New"):
        with st.form("add_meta"):
            name = st.text_input("Normalized Name")
            cat = st.selectbox("Category Override", [""] + category_names, key="meta_cat")
            subcat = st.text_input("Subcategory Override", key="meta_subcat")
            tax = st.text_input("Tax Flags Override", key="meta_tax")
            payor = st.selectbox("Payor", payor_options, key="meta_payor")
            note = st.text_input("Note", key="meta_note")
            col1, col2 = st.columns(2)
            save = col1.form_submit_button("Add")
            cancel = col2.form_submit_button("Cancel")
        if save and name:
            queries.upsert_payee_metadata(
                conn,
                normalized_name=name,
                category_override=cat or None,
                subcategory_override=subcat or None,
                tax_flags_override=tax or None,
                payor=payor or None,
                note=note or None,
            )
            st.success("Added.")
            st.rerun()
        if cancel:
            st.rerun()


# ---------------------------------------------------------------------------
# Category Master
# ---------------------------------------------------------------------------

def _category_master(conn):
    st.subheader("Category Master")

    rows = queries.get_categories(conn)
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        df["subcategory"] = df["subcategory"].fillna("")
        df["tax_flag_default"] = df["tax_flag_default"].fillna("")

        gb = GridOptionsBuilder.from_dataframe(df[["id", "category", "subcategory", "tax_flag_default"]])
        gb.configure_default_column(resizable=True, sortable=True, editable=False)
        gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)
        gb.configure_column("id", width=50)
        gb.configure_column("category", editable=True, width=180)
        gb.configure_column("subcategory", editable=True, width=180)
        tax_flag_values = [
            "", "Tax-reportable", "Reimbursable", "Capital Improvements",
            "Home Office", "Donations - Deductible", "Medical", "Business Expense",
            "Business Expense, Reimbursable",
            "Business Expense, Home Office",
        ]
        gb.configure_column("tax_flag_default", editable=True, width=200,
                            cellEditor="agSelectCellEditor",
                            cellEditorParams={"values": tax_flag_values})

        grid_response = AgGrid(
            df[["id", "category", "subcategory", "tax_flag_default"]],
            gridOptions=gb.build(),
            update_mode=GridUpdateMode.VALUE_CHANGED,
            fit_columns_on_grid_load=True,
            height=400,
        )

        st.warning("Editing category or subcategory names here does **not** cascade to existing transactions.")

        col1, col2 = st.columns(2)
        if col1.button("Save Changes", key="save_cats"):
            edited = grid_response["data"]
            original = df[["id", "category", "subcategory", "tax_flag_default"]]
            for _, row in edited.iterrows():
                orig = original[original["id"] == row["id"]].iloc[0]
                conn.execute(
                    "UPDATE categories SET category=?, subcategory=?, tax_flag_default=? WHERE id=?",
                    (row["category"], row["subcategory"] or None, row["tax_flag_default"] or None, int(row["id"])),
                )
            conn.commit()
            st.success("Saved.")
            st.rerun()

        if col2.button("Delete Selected", key="del_cat"):
            sel = grid_response.get("selected_rows")
            if sel is not None and len(sel) > 0:
                for _, row in sel.iterrows():
                    queries.delete_category(conn, int(row["id"]))
                st.success("Deleted.")
                st.rerun()

    with st.expander("Add New Category"):
        with st.form("add_cat"):
            cat = st.text_input("Category")
            subcat = st.text_input("Subcategory (optional)")
            tax_default = st.text_input("Tax Flag Default (optional)")
            col1, col2 = st.columns(2)
            save = col1.form_submit_button("Add")
            cancel = col2.form_submit_button("Cancel")
        if save and cat:
            queries.upsert_category(conn, cat, subcat or None, tax_default or None)
            st.success("Added.")
            st.rerun()
        if cancel:
            st.rerun()


# ---------------------------------------------------------------------------
# Rename / Merge Payees
# ---------------------------------------------------------------------------

def _rename_merge_payees(conn):
    st.subheader("Rename / Merge Payees")
    st.caption(
        "Rename a payee (e.g. 'The Whitney' -> 'Whitney') or merge two payees into one "
        "(e.g. 'State Farm' + 'State Farm Insurance' -> 'State Farm Insurance'). "
        "Updates transactions, normalization rules, and payee metadata all at once."
    )

    # Get all distinct payee names from transactions
    payee_rows = conn.execute(
        "SELECT payee, COUNT(*) as cnt FROM transactions "
        "WHERE payee IS NOT NULL GROUP BY payee ORDER BY payee"
    ).fetchall()

    if not payee_rows:
        st.info("No payees found.")
        return

    payee_list = [r["payee"] for r in payee_rows]
    payee_counts = {r["payee"]: r["cnt"] for r in payee_rows}

    # Show current payees
    st.dataframe(
        pd.DataFrame([{"Payee": p, "# Txns": payee_counts[p]} for p in payee_list]),
        use_container_width=True, hide_index=True, height=300,
    )

    st.divider()

    # Mode selection
    mode = st.radio("Action", ["Rename a payee", "Merge two payees"], horizontal=True, key="rm_mode")

    if mode == "Rename a payee":
        old_name = st.selectbox("Select payee to rename", payee_list, key="rename_old")
        new_name = st.text_input("New name", key="rename_new")

        if old_name:
            st.caption(f"'{old_name}' has {payee_counts.get(old_name, 0)} transaction(s)")

        col1, col2 = st.columns(2)
        if col1.button("Rename", type="primary", key="do_rename"):
            if not new_name or not new_name.strip():
                st.error("Enter a new name.")
            elif new_name.strip() == old_name:
                st.warning("New name is the same as the old name.")
            else:
                new = new_name.strip()
                _execute_rename(conn, old_name, new)
                st.success(f"Renamed '{old_name}' -> '{new}'")
                st.rerun()
        if col2.button("Cancel", key="cancel_rename"):
            st.rerun()

    else:  # Merge
        st.caption("Select two payees. All transactions from the second will be merged into the first (kept name).")
        keep_name = st.selectbox("Keep this payee name", payee_list, key="merge_keep")
        merge_name = st.selectbox("Merge this payee into it",
                                  [p for p in payee_list if p != keep_name], key="merge_from")

        if keep_name and merge_name:
            st.caption(
                f"'{keep_name}' ({payee_counts.get(keep_name, 0)} txns) + "
                f"'{merge_name}' ({payee_counts.get(merge_name, 0)} txns) "
                f"-> '{keep_name}'"
            )

        col1, col2 = st.columns(2)
        if col1.button("Merge", type="primary", key="do_merge"):
            if keep_name and merge_name:
                _execute_rename(conn, merge_name, keep_name)
                st.success(f"Merged '{merge_name}' into '{keep_name}'")
                st.rerun()
        if col2.button("Cancel", key="cancel_merge"):
            st.rerun()


def _execute_rename(conn, old_name: str, new_name: str):
    """Rename a payee across all tables."""
    # Update transactions
    count = conn.execute(
        "UPDATE transactions SET payee = ? WHERE payee = ?",
        (new_name, old_name),
    ).rowcount

    # Update normalization rules
    conn.execute(
        "UPDATE payee_normalization SET normalized_name = ? WHERE normalized_name = ?",
        (new_name, old_name),
    )

    # Update payee metadata - merge if target already exists
    old_meta = conn.execute(
        "SELECT * FROM payee_metadata WHERE normalized_name = ?", (old_name,)
    ).fetchone()
    new_meta = conn.execute(
        "SELECT * FROM payee_metadata WHERE normalized_name = ?", (new_name,)
    ).fetchone()

    if old_meta and not new_meta:
        # Just rename
        conn.execute(
            "UPDATE payee_metadata SET normalized_name = ? WHERE normalized_name = ?",
            (new_name, old_name),
        )
    elif old_meta and new_meta:
        # Keep the target's metadata, delete the old one
        conn.execute(
            "DELETE FROM payee_metadata WHERE normalized_name = ?",
            (old_name,),
        )

    conn.commit()


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

    category_names = queries.get_category_names(conn)
    payor_options = ["", "David", "Debra", "Both", "Unknown"]
    status_options = ["pending", "confirmed", "needs_review"]

    # Build editable grid
    data = []
    for r in rows:
        data.append({
            "id": r["id"],
            "Date": r["date"],
            "Source": r["source"],
            "Payee": r["payee"] or "",
            "Category": r["category"] or "",
            "Subcategory": r["subcategory"] or "",
            "Amount": float(r["amount"]) if r["amount"] else 0.0,
            "Tax Flags": r["tax_flags"] or "",
            "Payor": r["payor"] or "",
            "Note": r["note"] or "",
            "Status": r["status"],
            "Description": r["description_raw"],
        })

    df = pd.DataFrame(data)

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, sortable=True, editable=False)
    gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)
    gb.configure_column("id", width=50)
    gb.configure_column("Date", width=95)
    gb.configure_column("Source", width=110)
    gb.configure_column("Payee", editable=True, width=150)
    gb.configure_column("Category", editable=True, width=140,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": [""] + category_names})
    gb.configure_column("Subcategory", editable=True, width=140)
    gb.configure_column("Amount", width=90,
                        type=["numericColumn"],
                        valueFormatter=JsCode("function(params) { return '$' + params.value.toFixed(2); }"))
    tax_flag_values = [
        "", "Tax-reportable", "Reimbursable", "Capital Improvements",
        "Home Office", "Donations - Deductible", "Medical", "Business Expense",
        "Business Expense, Reimbursable",
        "Business Expense, Home Office",
    ]
    gb.configure_column("Tax Flags", editable=True, width=160,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": tax_flag_values})
    gb.configure_column("Payor", editable=True, width=90,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": payor_options})
    gb.configure_column("Note", editable=True, width=150)
    gb.configure_column("Status", editable=True, width=100,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": status_options})
    gb.configure_column("Description", width=200)

    # Paginate
    gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=50)

    grid_response = AgGrid(
        df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.VALUE_CHANGED,
        fit_columns_on_grid_load=True,
        height=500,
        allow_unsafe_jscode=True,
    )

    if st.button("Save Changes", key="save_txns"):
        edited = grid_response["data"]
        for _, row in edited.iterrows():
            queries.update_transaction(
                conn, int(row["id"]),
                payee=row["Payee"] or None,
                category=row["Category"] or None,
                subcategory=row["Subcategory"] or None,
                tax_flags=row["Tax Flags"] or None,
                payor=row["Payor"] or None,
                note=row["Note"] or None,
                status=row["Status"],
                overridden=1,
            )
        st.success("Saved.")
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
    col1, col2 = st.columns(2)
    if col1.button("Purge", type="primary"):
        if confirm == "DELETE ALL TRANSACTIONS":
            count = queries.purge_transactions(conn)
            st.success(f"Purged {count} transaction(s).")
            st.rerun()
        else:
            st.error("Confirmation phrase does not match.")
    if col2.button("Cancel", key="cancel_purge"):
        st.rerun()
