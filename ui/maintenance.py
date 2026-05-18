"""Maintenance pages — CRUD for lookup tables, transaction editing, database admin."""

import streamlit as st
import pandas as pd
from datetime import date
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode

from db import queries
from db.schema import DB_PATH


def maintenance_page(conn):
    st.title("Maintenance")

    tabs = st.tabs([
        "Source Accounts", "Payee Normalization", "Payee Metadata",
        "Category Master", "Rename / Merge Payees", "Edit Transactions",
        "Pending Folder", "Database",
    ])

    with tabs[0]:
        _source_accounts(conn)
    with tabs[1]:
        _payee_normalization(conn)
    with tabs[2]:
        _payee_metadata(conn)
    with tabs[3]:
        _category_master(conn)
    with tabs[4]:
        _rename_merge_payees(conn)
    with tabs[5]:
        _edit_transactions(conn)
    with tabs[6]:
        _pending_folder(conn)
    with tabs[7]:
        _database_admin(conn)


# ---------------------------------------------------------------------------
# Source Accounts
# ---------------------------------------------------------------------------

def _source_accounts(conn):
    st.subheader("Source Accounts")
    st.caption(
        "Account types: `checking`/`credit_card` use the column-template ingest "
        "path; `venmo_detail`/`amazon_detail` route to enrichment. "
        "Use `replaced_by_prefix` to link a reissued account to its successor, "
        "and `discontinued_since` to silence missing-account warnings."
    )

    rows = queries.get_source_file_map(conn)
    if not rows:
        st.info("No source accounts configured yet.")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    # Ensure all expected columns exist even on partially-migrated DBs
    for col in ("discontinued_since", "replaced_by_prefix"):
        if col not in df.columns:
            df[col] = None
    df["discontinued_since"] = df["discontinued_since"].fillna("")
    df["replaced_by_prefix"] = df["replaced_by_prefix"].fillna("")
    df["nickname"] = df["nickname"].fillna("")
    df["account_type"] = df["account_type"].fillna("")

    display_cols = ["id", "source_prefix", "source_label", "nickname",
                    "account_type", "replaced_by_prefix", "discontinued_since"]

    from processing.enrich import enricher_account_types
    type_choices = sorted({"checking", "credit_card"} | enricher_account_types())

    gb = GridOptionsBuilder.from_dataframe(df[display_cols])
    gb.configure_default_column(resizable=True, sortable=True, editable=False)
    gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)
    gb.configure_column("id", width=50)
    gb.configure_column("source_prefix", width=130)
    gb.configure_column("source_label", width=130)
    gb.configure_column("nickname", editable=True, width=180)
    gb.configure_column("account_type", editable=True, width=130,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": type_choices})
    gb.configure_column("replaced_by_prefix", editable=True, width=150)
    gb.configure_column("discontinued_since", editable=True, width=140)

    grid_response = AgGrid(
        df[display_cols],
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.VALUE_CHANGED,
        fit_columns_on_grid_load=True,
        height=350,
    )

    if st.button("Save Changes", key="save_sources"):
        edited = grid_response["data"]
        for _, row in edited.iterrows():
            queries.upsert_source_file_map(
                conn, row["source_prefix"], row["source_label"],
                row["nickname"] or None, row["account_type"] or None,
            )
            # Update continuity columns directly (upsert_source_file_map
            # doesn't touch them)
            conn.execute(
                "UPDATE source_file_map SET replaced_by_prefix = ?, "
                "discontinued_since = ? WHERE source_prefix = ?",
                (
                    (row["replaced_by_prefix"] or None),
                    (row["discontinued_since"] or None),
                    row["source_prefix"],
                ),
            )
        conn.commit()
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

def _ai_near_miss_section(conn):
    """🤖 Check Normalizations — uses Claude to find near-miss duplicate payees
    and lets the user merge each group with one click.
    """
    import os
    payee_count = conn.execute(
        "SELECT COUNT(DISTINCT payee) AS c FROM transactions "
        "WHERE payee IS NOT NULL AND payee != ''"
    ).fetchone()["c"]

    if payee_count < 2:
        return  # nothing to check

    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))

    with st.expander(
        f"🤖 Check {payee_count} payees for near-miss duplicates (uses Claude)",
        expanded=False,
    ):
        st.caption(
            "Asks Claude to scan your distinct payees and flag groups that "
            "look like the same merchant under different spellings "
            "(e.g. \"Martha's\", \"Martha Bros\", \"Martha's Coffee\"). "
            "For each group you can pick a canonical name and click Merge — "
            "all variants get renamed across transactions, normalization rules, "
            "and payee_metadata in one step. Or click Leave Separate to dismiss "
            "the suggestion."
        )
        if not api_key_set:
            st.error(
                "**ANTHROPIC_API_KEY environment variable is not set.** Get a key "
                "at console.anthropic.com and set it as a system environment "
                "variable, then restart Streamlit."
            )
            return

        col_run, col_clear = st.columns([1, 1])
        if col_run.button("Check Normalizations", key="ai_near_miss_go",
                           type="primary"):
            with st.spinner(f"Asking Claude about {payee_count} payees..."):
                try:
                    from processing.ai_near_miss import find_near_miss_groups
                    groups, warnings = find_near_miss_groups(conn)
                    st.session_state.ai_near_miss_groups = [g.model_dump() for g in groups]
                    st.session_state.ai_near_miss_warnings = warnings
                    # Reset any per-group dismissals from previous runs
                    st.session_state.ai_near_miss_dismissed = set()
                    st.rerun()
                except RuntimeError as e:
                    st.error(f"AI check failed: {e}")
                except Exception as e:
                    st.error(f"Unexpected error: {type(e).__name__}: {e}")

        if col_clear.button("Clear Results", key="ai_near_miss_clear",
                             disabled=("ai_near_miss_groups" not in st.session_state)):
            st.session_state.pop("ai_near_miss_groups", None)
            st.session_state.pop("ai_near_miss_warnings", None)
            st.session_state.pop("ai_near_miss_dismissed", None)
            st.rerun()

        # Render results from the most recent run
        groups = st.session_state.get("ai_near_miss_groups")
        if groups is None:
            return

        warnings = st.session_state.get("ai_near_miss_warnings", [])
        dismissed = st.session_state.get("ai_near_miss_dismissed", set())
        active_groups = [g for i, g in enumerate(groups) if i not in dismissed]

        if not active_groups and not warnings:
            st.info("✅ No near-miss duplicates found by Claude.")
            return

        if active_groups:
            st.markdown(f"**{len(active_groups)} suggested group(s):**")

        # Distinct-payee counts so we can show "X transactions" per member
        counts = dict(conn.execute(
            "SELECT payee, COUNT(*) FROM transactions "
            "WHERE payee IS NOT NULL GROUP BY payee"
        ).fetchall())

        for i, g in enumerate(groups):
            if i in dismissed:
                continue
            members = g["members"]
            canonical_default = g["canonical"]
            confidence = g["confidence"]
            reasoning = g["reasoning"]

            with st.container(border=True):
                conf_color = "green" if confidence == "high" else "orange"
                st.markdown(
                    f"**Group {i+1}** — confidence: :{conf_color}[{confidence}]"
                )
                st.caption(reasoning)
                # Show members with counts
                for m in members:
                    cnt = counts.get(m, 0)
                    marker = "→ canonical" if m == canonical_default else ""
                    st.write(f"  - **{m}** ({cnt} txns) {marker}")

                # Let the user pick a different canonical if they prefer
                canonical_choice = st.selectbox(
                    "Merge all into:",
                    members,
                    index=members.index(canonical_default),
                    key=f"nm_canonical_{i}",
                )

                col_m, col_l, _ = st.columns([1, 1, 3])
                if col_m.button("Merge", type="primary", key=f"nm_merge_{i}"):
                    # Rename every non-canonical member to the chosen canonical
                    merged_count = 0
                    for m in members:
                        if m == canonical_choice:
                            continue
                        _execute_rename(conn, m, canonical_choice)
                        merged_count += 1
                    dismissed.add(i)
                    st.session_state.ai_near_miss_dismissed = dismissed
                    st.success(
                        f"Merged {merged_count} variant(s) into "
                        f"{canonical_choice!r}."
                    )
                    st.rerun()

                if col_l.button("Leave Separate", key=f"nm_leave_{i}"):
                    dismissed.add(i)
                    st.session_state.ai_near_miss_dismissed = dismissed
                    st.rerun()

        if warnings:
            with st.expander(
                f"⚠ {len(warnings)} validation warning(s) from Claude's response",
                expanded=False,
            ):
                st.caption(
                    "These groups were rejected before reaching you because "
                    "they didn't pass the validation checks (e.g. canonical "
                    "wasn't one of the members, or a member name didn't match "
                    "a real distinct payee). No data was modified."
                )
                for w in warnings:
                    st.write(f"- {w}")

    st.divider()


def _rename_merge_payees(conn):
    st.subheader("Rename / Merge Payees")
    st.caption(
        "Rename a payee (e.g. 'The Whitney' -> 'Whitney') or merge two payees into one "
        "(e.g. 'State Farm' + 'State Farm Insurance' -> 'State Farm Insurance'). "
        "Updates transactions, normalization rules, and payee metadata all at once."
    )

    # AI-assisted near-miss duplicate detection
    _ai_near_miss_section(conn)

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
    from ui._amount_style import amount_cell_style, amount_value_formatter
    gb.configure_column("Amount", width=90,
                        type=["numericColumn"],
                        valueFormatter=amount_value_formatter(),
                        cellStyle=amount_cell_style())
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
# Pending Folder
# ---------------------------------------------------------------------------

def _pending_folder(conn):
    st.subheader("Pending Folder")
    st.caption(
        "Enrichment files (Venmo, Amazon, ...) sit here until every record "
        "they contain has matched a Chase row. Files automatically retry on "
        "each Ingest run. Use the cleanup tool below to archive stale files "
        "manually — nothing moves automatically."
    )

    from processing.enrich import list_pending_status, cleanup_pending

    statuses = list_pending_status(conn)
    if not statuses:
        st.info("Pending folder is empty.")
        return

    df = pd.DataFrame(statuses)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("**Cleanup:** move pending files older than the threshold "
                "into `processed/` (they'll no longer be retried).")
    col1, col2 = st.columns([1, 3])
    threshold_days = col1.number_input(
        "Older than (days)",
        min_value=30, max_value=3650, value=180, step=30,
        key="pending_cleanup_days",
    )
    col2.caption(f"Default 180 days. Today: {date.today().isoformat()}.")

    if st.button("Move stale files to processed/", key="pending_cleanup"):
        moved = cleanup_pending(conn, int(threshold_days))
        if moved:
            st.success(f"Moved {len(moved)} file(s) to processed/:")
            for fname in moved:
                st.write(f"  - `{fname}`")
        else:
            st.info(f"No files older than {threshold_days} days.")
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
    st.subheader("Reset historical Venmo rows for enrichment")
    st.caption(
        "Migrates existing Chase Venmo rows from the old placeholder convention "
        "to the new design so enrichment can patch them: clears `overridden`, "
        "resets `category` to the default Cash → Deposited or Withdrawn, and "
        "moves them back to `pending` status. Only touches rows that have no "
        "user-customized data (no note, no payor, no tax flags). Safe to run "
        "more than once."
    )
    candidates = conn.execute(
        "SELECT COUNT(*) AS cnt FROM transactions "
        "WHERE via = 'Venmo' AND payee IS NULL "
        "AND note IS NULL AND payor IS NULL AND tax_flags IS NULL "
        "AND (overridden = 1 OR status != 'pending' OR category != 'Cash')"
    ).fetchone()["cnt"]
    if candidates:
        st.write(f"**{candidates}** historical Venmo row(s) eligible for reset.")
        if st.button("Reset eligible Venmo rows", key="reset_venmo"):
            cur = conn.execute(
                "UPDATE transactions "
                "SET overridden = 0, "
                "    category = 'Cash', "
                "    subcategory = 'Deposited or Withdrawn', "
                "    tax_flags = NULL, "
                "    status = 'pending' "
                "WHERE via = 'Venmo' AND payee IS NULL "
                "AND note IS NULL AND payor IS NULL AND tax_flags IS NULL "
                "AND (overridden = 1 OR status != 'pending' OR category != 'Cash')"
            )
            conn.commit()
            st.success(f"Reset {cur.rowcount} row(s). Drop the Venmo file in input/ to enrich them.")
            st.rerun()
    else:
        st.info("No historical Venmo rows need reset.")

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
