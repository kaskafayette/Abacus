"""Maintenance pages — CRUD for lookup tables, transaction editing, database admin."""

import streamlit as st
import pandas as pd
from datetime import date
from decimal import Decimal
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode

from db import queries
from db.schema import DB_PATH
from ui._amount_style import case_insensitive_comparator


def maintenance_page(conn):
    st.title("Maintenance")

    # Category Consistency used to be its own tab; it's now folded into
    # Payee Metadata since both are about a payee's canonical categorization.
    # Pending Folder used to be a tab too; removed once Venmo became a
    # first-class checking source — no enrichment sources currently use the
    # pending/ pipeline, so the tab would sit empty forever. Re-add here when
    # Amazon enrichment (Next Steps #1-3) or another enricher lands.
    tabs = st.tabs([
        "Source Accounts", "Payee Normalization", "Payee Metadata",
        "Category Master", "Rename / Merge Payees",
        "Edit Transactions", "Split Transaction", "Database",
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
        _split_transaction(conn)
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
    gb.configure_default_column(resizable=True, sortable=True, editable=False,
                                comparator=case_insensitive_comparator())
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
        allow_unsafe_jscode=True,   # required for the case-insensitive comparator
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

    st.caption(
        "**whole_word_only**: when ✓, the pattern only matches when it appears "
        "as a whole word (regex `\\b` boundaries). Use it for short patterns "
        "like `NEST` or `GAP` that would otherwise over-match inside "
        "`NESTLDOWN`, `GAPPY`, etc. Default off = substring match."
    )
    rows = queries.get_payee_normalizations(conn)
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        # Some rows on partially-migrated DBs may not have whole_word_only —
        # backfill for display so the grid shows the column.
        if "whole_word_only" not in df.columns:
            df["whole_word_only"] = 0
        df["whole_word_only"] = df["whole_word_only"].fillna(0).astype(int).astype(bool)

        cols_shown = ["id", "search_pattern", "normalized_name", "payee_suffix", "whole_word_only"]

        gb = GridOptionsBuilder.from_dataframe(df[cols_shown])
        gb.configure_default_column(resizable=True, sortable=True, editable=False,
                                comparator=case_insensitive_comparator())
        gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)
        gb.configure_column("id", width=50)
        gb.configure_column("search_pattern", editable=True, width=200)
        gb.configure_column("normalized_name", editable=True, width=200)
        gb.configure_column("payee_suffix", editable=True, width=150)
        gb.configure_column("whole_word_only", editable=True, width=140,
                            cellEditor="agCheckboxCellEditor",
                            cellRenderer="agCheckboxCellRenderer")

        grid_response = AgGrid(
            df[cols_shown],
            gridOptions=gb.build(),
            update_mode=GridUpdateMode.VALUE_CHANGED,
            fit_columns_on_grid_load=True,
            height=400,
            allow_unsafe_jscode=True,   # comparator JsCode
        )

        col1, col2 = st.columns(2)
        if col1.button("Save Changes", key="save_norm"):
            edited = grid_response["data"]
            for _, row in edited.iterrows():
                queries.update_payee_normalization(
                    conn, int(row["id"]),
                    row["search_pattern"], row["normalized_name"],
                    row["payee_suffix"] if pd.notna(row["payee_suffix"]) and row["payee_suffix"] else None,
                    whole_word_only=bool(row["whole_word_only"]),
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
            wwo = st.checkbox(
                "Whole word only (safer for short patterns like `NEST`, `GAP`)",
                value=False, key="add_norm_wwo",
            )
            col1, col2 = st.columns(2)
            save = col1.form_submit_button("Add")
            cancel = col2.form_submit_button("Cancel")
        if save and pattern and name:
            queries.insert_payee_normalization(
                conn, pattern, name, suffix or None, whole_word_only=wwo)
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
    import json
    st.subheader("Payee Metadata")
    st.caption(
        "Default categorization and payor for each normalized payee. New "
        "transactions matching a payee here inherit these values. This page "
        "also surfaces **data-quality issues** in the metadata itself, and "
        "shows **category consistency** across the actual transactions."
    )

    # --- Data-quality banner ---
    _render_metadata_data_quality(conn)

    st.divider()

    rows = queries.get_payee_metadata(conn)
    category_names = queries.get_category_names(conn)
    subcats_map, _ = _build_subcats_map(conn)
    payor_options = ["", "David", "Debra", "Both", "Unknown"]

    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        # Replace None with empty string for display
        for col in df.columns:
            df[col] = df[col].fillna("")

        # Cascading subcategory picker: Subcategory column shows only the subs
        # valid for the row's selected Category. Uses cellEditorSelector at the
        # top level of the column def (nested JsCode in cellEditorParams stays
        # a string on the JS side and the dropdown ends up empty).
        subcat_selector = JsCode(
            "function(params){ var m=" + json.dumps(subcats_map) + ";"
            " var c=params.data.category_override;"
            " var vals=(c && m[c])?m[c]:[''];"
            " return {component:'agSelectCellEditor', params:{values:vals}}; }"
        )

        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_default_column(resizable=True, sortable=True, editable=False,
                                comparator=case_insensitive_comparator())
        gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)
        gb.configure_column("id", width=50)
        gb.configure_column("normalized_name", width=180)
        gb.configure_column("category_override", editable=True, width=150,
                            cellEditor="agSelectCellEditor",
                            cellEditorParams={"values": [""] + category_names})
        gb.configure_column("subcategory_override", editable=True, width=150,
                            cellEditorSelector=subcat_selector)
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
            allow_unsafe_jscode=True,   # comparator + subcategory cascade JsCode
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

    # --- Category consistency (moved from its own tab) ---
    st.divider()
    _render_category_consistency(conn)


def _render_metadata_data_quality(conn):
    """Show a summary of missing/invalid category & subcategory issues on
    payee_metadata. Each issue class is a collapsed expander listing every
    offending row so the user can walk through fixes on the grid below.
    Rendered near the top of the Payee Metadata page."""
    issues = queries.check_payee_metadata_issues(conn)
    n_miss_cat = len(issues["missing_category"])
    n_miss_sub = len(issues["missing_subcategory"])
    n_bad_cat  = len(issues["invalid_category"])
    n_bad_sub  = len(issues["invalid_subcategory"])
    total = n_miss_cat + n_miss_sub + n_bad_cat + n_bad_sub

    if total == 0:
        st.success(
            "✓ **Data quality: clean.** Every row has a valid category and "
            "subcategory that match the categories master."
        )
        return

    st.warning(
        f"⚠ **{total} data-quality issue(s) on payee_metadata.** "
        f"Fix by editing the row(s) in the grid below."
    )

    def _list_expander(title: str, rows: list, cols: list[str]):
        if not rows:
            return
        with st.expander(title, expanded=False):
            df = pd.DataFrame(rows)
            display_cols = [c for c in cols if c in df.columns]
            st.dataframe(df[display_cols], use_container_width=True,
                          hide_index=True)

    _list_expander(
        f"**{n_miss_cat}** — Missing category (category field blank)",
        issues["missing_category"], ["id", "payee", "reason"])
    _list_expander(
        f"**{n_miss_sub}** — Missing subcategory (category has sub-choices)",
        issues["missing_subcategory"],
        ["id", "payee", "category", "reason"])
    _list_expander(
        f"**{n_bad_cat}** — Invalid category (not in categories master)",
        issues["invalid_category"],
        ["id", "payee", "category", "subcategory", "reason"])
    _list_expander(
        f"**{n_bad_sub}** — Invalid subcategory (not valid for this category)",
        issues["invalid_subcategory"],
        ["id", "payee", "category", "subcategory", "reason"])


def _render_category_consistency(conn):
    """Payees whose actual transactions span multiple (category, subcategory)
    combos — the previous 'Category Consistency' maintenance tab, moved here
    since it's fundamentally about the same subject as payee_metadata (a
    payee's canonical categorization)."""
    st.subheader("Category Consistency Across Transactions")
    st.caption(
        "Finds payees whose transactions are categorized differently across "
        "the database. For a single-purpose payee (e.g. Supercuts, Emmi "
        "Petersen) an inconsistency usually needs fixing. For payees that "
        "legitimately span categories (Amazon), click **Ignore** to silence "
        "the warning. Silenced payees can be un-silenced at the bottom."
    )

    conflicts = queries.get_multicat_payees(conn)
    silenced_rows = queries.list_silenced_multicat_payees(conn)
    silenced_names = {r["normalized_name"] for r in silenced_rows}
    active = {p: mix for p, mix in conflicts.items() if p not in silenced_names}

    if not active:
        st.success(
            f"✓ No inconsistent-category payees. "
            f"({len(silenced_names)} payee(s) silenced.)"
        )
    else:
        st.warning(
            f"**{len(active)}** payee(s) have inconsistent categorization."
        )
        for payee, mix in active.items():
            total_txns = sum(m["count"] for m in mix)
            total_amt = sum(m["total"] for m in mix)
            title = (f"**{payee}** — {total_txns} txns across "
                     f"{len(mix)} categorizations · "
                     f"{_fmt_amt_signed(total_amt)}")
            with st.expander(title, expanded=False):
                df = pd.DataFrame([
                    {
                        "Category":    m["category"] or "(uncategorized)",
                        "Subcategory": m["subcategory"] or "(none)",
                        "Count":       m["count"],
                        "Total":       _fmt_amt_signed(m["total"]),
                    }
                    for m in mix
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)

                options = [(m["category"], m["subcategory"] or None) for m in mix]
                labels = [f"{(c or '(uncategorized)')} / {(s or '(none)')}"
                          f"  ({m['count']} txns)"
                          for m, (c, s) in zip(mix, options)]
                choice_idx = st.radio(
                    "Pick the canonical categorization (applies to every "
                    f"{payee!r} transaction):",
                    range(len(options)),
                    format_func=lambda i: labels[i],
                    key=f"cc_choice_{payee}",
                )
                target_cat, target_subcat = options[choice_idx]

                col_apply, col_ignore, _ = st.columns([1.4, 1, 3])
                if col_apply.button(
                    f"Apply to all {total_txns} txns",
                    type="primary", key=f"cc_apply_{payee}",
                ):
                    if target_subcat:
                        r = conn.execute(
                            "SELECT tax_flag_default FROM categories "
                            "WHERE category=? AND subcategory=?",
                            (target_cat, target_subcat)).fetchone()
                    else:
                        r = conn.execute(
                            "SELECT tax_flag_default FROM categories "
                            "WHERE category=? AND subcategory IS NULL",
                            (target_cat,)).fetchone()
                    tax_flag = r["tax_flag_default"] if r else None

                    n = queries.apply_canonical_category_to_payee(
                        conn, payee, target_cat, target_subcat, tax_flag)
                    st.success(
                        f"Updated {n} transaction(s): all {payee!r} → "
                        f"{target_cat} / {target_subcat or '(none)'}. "
                        f"payee_metadata updated so future ingests default "
                        f"the same way."
                    )
                    st.rerun()

                if col_ignore.button("Ignore this payee",
                                     key=f"cc_ignore_{payee}"):
                    queries.silence_multicat_payee(
                        conn, payee,
                        note="Multi-category expected — silenced by user.")
                    st.success(f"Silenced {payee!r}. It will no longer show "
                               "up here unless un-silenced.")
                    st.rerun()

    st.divider()
    n_silenced = len(silenced_rows)
    if n_silenced == 0:
        st.caption("**0 silenced payee(s).** Click **Ignore this payee** "
                   "above to silence expected multi-category payees "
                   "(e.g. Amazon).")
    else:
        with st.expander(f"{n_silenced} silenced payee(s) — click to view / un-silence",
                          expanded=False):
            for r in silenced_rows:
                c1, c2, c3 = st.columns([3, 3, 1])
                c1.markdown(f"**{r['normalized_name']}**")
                when = str(r['silenced_at'])[:16]
                c2.caption(f"silenced {when}"
                           + (f" — {r['note']}" if r['note'] else ""))
                if c3.button("Un-silence",
                             key=f"cc_unsilence_{r['normalized_name']}"):
                    queries.unsilence_multicat_payee(conn, r["normalized_name"])
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
        gb.configure_default_column(resizable=True, sortable=True, editable=False,
                                comparator=case_insensitive_comparator())
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
            allow_unsafe_jscode=True,   # comparator JsCode
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

# ---------------------------------------------------------------------------
# Category Consistency Check
# ---------------------------------------------------------------------------

def _fmt_amt_signed(v: float) -> str:
    """Local dollar formatter that keeps the sign in front of the $ symbol."""
    if v > 0:
        return f"${v:,.2f}"
    if v < 0:
        return f"-${abs(v):,.2f}"
    return "$0.00"


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

    import json
    category_names = queries.get_category_names(conn)
    subcats_map, _ = _build_subcats_map(conn)
    payor_options = ["", "David", "Debra", "Both", "Unknown"]
    status_options = ["pending", "confirmed", "needs_review"]

    # Derive each row's split role so split rows can be visually fenced off and
    # locked from inline edits. Editing a split must happen in the Split
    # Transaction tab, where the leg/parent relationship is enforced.
    parent_ids = queries.get_parent_ids(conn)

    def _role(r):
        if r["split_parent_id"] is not None:
            return "leg"
        if r["id"] in parent_ids:
            return "parent"
        return ""

    role_labels = {"": "", "parent": "split (not counted)", "leg": "leg"}

    # Build editable grid
    data = []
    for r in rows:
        role = _role(r)
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
            "Split": role_labels[role],
            "split_role": role,
        })

    df = pd.DataFrame(data)

    n_split = sum(1 for d in data if d["split_role"])
    if n_split:
        st.info(
            f"{n_split} row(s) here are part of a split (greyed, locked). "
            "Edit them in the **Split Transaction** tab — the leg amounts must "
            "stay balanced against the parent, so they can't be edited inline.",
            icon="🔒",
        )

    # A cell is editable only when the row is NOT part of a split. AG Grid
    # accepts a function for `editable`, so split legs/parents are locked
    # automatically — the inline editor simply won't open on them.
    not_split_editable = JsCode(
        "function(params){ return !params.data.split_role; }"
    )
    # Grey + italicize split rows so they read as fenced-off.
    split_row_style = JsCode(
        "function(params){ if (params.data && params.data.split_role) "
        "{ return {'color': '#888', 'fontStyle': 'italic'}; } return null; }"
    )

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, sortable=True, editable=False,
                                comparator=case_insensitive_comparator())
    gb.configure_grid_options(
        singleClickEdit=True,
        stopEditingWhenCellsLoseFocus=True,
        getRowStyle=split_row_style,
        # Label the selection-checkbox column so it reads as "tick this to
        # split" rather than a blank column that looks like a multi-select.
        selectionColumnDef={"headerName": "split", "width": 70, "pinned": "left"},
    )
    gb.configure_column("id", width=50)
    gb.configure_column("split_role", hide=True)
    gb.configure_column("Date", width=95)
    gb.configure_column("Source", width=110)
    gb.configure_column("Split", width=110)
    gb.configure_column("Payee", editable=not_split_editable, width=150)
    gb.configure_column("Category", editable=not_split_editable, width=140,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": [""] + category_names})
    # Cascading subcategory picker — options narrow to the row's Category.
    subcat_selector = JsCode(
        "function(params){ var m=" + json.dumps(subcats_map) + ";"
        " var c=params.data.Category;"
        " var vals=(c && m[c])?m[c]:[''];"
        " return {component:'agSelectCellEditor', params:{values:vals}}; }"
    )
    gb.configure_column("Subcategory", editable=not_split_editable, width=140,
                        cellEditorSelector=subcat_selector)
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
    gb.configure_column("Tax Flags", editable=not_split_editable, width=160,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": tax_flag_values})
    gb.configure_column("Payor", editable=not_split_editable, width=90,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": payor_options})
    gb.configure_column("Note", editable=not_split_editable, width=150)
    gb.configure_column("Status", editable=not_split_editable, width=100,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": status_options})
    gb.configure_column("Description", width=200)

    # Paginate
    gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=50)
    # Far-left checkbox to pick one row to split.
    gb.configure_selection(selection_mode="single", use_checkbox=True)

    grid_response = AgGrid(
        df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.MODEL_CHANGED,
        fit_columns_on_grid_load=True,
        height=500,
        allow_unsafe_jscode=True,
    )

    if st.button("Save Changes", key="save_txns"):
        edited = grid_response["data"]
        skipped = 0
        missing_subcat_flagged: list[tuple[int, str, str]] = []  # (id, payee, cat)
        for _, row in edited.iterrows():
            # Never write split parents or legs from this grid — they're edited
            # only in the Split Transaction tab, with the balance enforced.
            if row.get("split_role"):
                skipped += 1
                continue
            cat = row["Category"] or None
            subcat = row["Subcategory"] or None
            # Save the field changes regardless — but if the row's category
            # requires a subcategory and one wasn't picked, flag it in the
            # warning list below so the user can go finish it (usually just
            # sloppiness, not something needing research).
            if cat and (not subcat) and queries.category_requires_subcategory(conn, cat):
                missing_subcat_flagged.append(
                    (int(row["id"]), row["Payee"] or "(no payee)", cat))
            queries.update_transaction(
                conn, int(row["id"]),
                payee=row["Payee"] or None,
                category=cat,
                subcategory=subcat,
                tax_flags=row["Tax Flags"] or None,
                payor=row["Payor"] or None,
                note=row["Note"] or None,
                status=row["Status"],
                overridden=1,
            )
        msg = "Saved."
        if skipped:
            msg += f" ({skipped} split row(s) left untouched — edit below or in Split Transaction.)"
        if missing_subcat_flagged:
            lines = "\n".join(
                f"- id={tid} — **{payee}** ({cat}): pick a subcategory."
                for tid, payee, cat in missing_subcat_flagged
            )
            st.error(
                "**⚠ Saved, but these rows still need a subcategory:**\n\n"
                + lines +
                "\n\nThe category has real subcategories defined; pick one for "
                "each row above and Save again. They also show up in the "
                "missing-subcategory count on Home / Sidebar until finished."
            )
        st.success(msg)
        st.rerun()

    # --- Split the selected transaction ---
    # Splitting needs the full filter power of this grid to find a row by amount
    # or id, so it's driven from here: tick a row, click Split, edit below.
    sel = grid_response.get("selected_rows")
    if sel is not None and len(sel) > 0:
        srow = sel.iloc[0]
        st.session_state["edit_selected_id"] = int(srow["id"])
        st.session_state["edit_selected_label"] = (
            f"#{int(srow['id'])} · {srow['Date']} · {srow['Source']} · "
            f"{srow['Payee'] or srow['Description']}"
        )

    st.divider()
    selected_id = st.session_state.get("edit_selected_id")
    if not selected_id:
        st.caption("Tick a row's checkbox (far left), then click Split to break "
                   "it into separately-categorized legs.")
    else:
        st.markdown(f"**Selected:** {st.session_state.get('edit_selected_label', '')}")
        if st.button("Split this transaction ↓", key="edit_split_btn"):
            st.session_state["edit_split_id"] = selected_id
            st.rerun()

    if st.session_state.get("edit_split_id"):
        st.divider()
        col_h, col_x = st.columns([4, 1])
        col_h.subheader("Split editor")
        if col_x.button("Close", key="edit_split_close"):
            st.session_state.pop("edit_split_id", None)
            st.rerun()
        _render_split_editor(conn, st.session_state["edit_split_id"],
                             key_prefix="editsplit")


# ---------------------------------------------------------------------------
# Split Transaction
# ---------------------------------------------------------------------------

def _split_cell(v):
    """Normalize a grid cell to a clean str or None."""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    return s or None


def _build_subcats_map(conn):
    """Return ({category: [subcategories]}, {(cat, subcat): tax_flag_default})."""
    subcats_map: dict[str, list[str]] = {}
    tax_defaults: dict[tuple, str] = {}
    for cat in queries.get_categories(conn):
        c, sc = cat["category"], cat["subcategory"]
        subcats_map.setdefault(c, [""])
        if sc:
            subcats_map[c].append(sc)
        if cat["tax_flag_default"]:
            tax_defaults[(c, sc)] = cat["tax_flag_default"]
    return subcats_map, tax_defaults


def _resolve_leg_tax_flags(tax_defaults, cat, subcat):
    """Default tax flag for a (category, subcategory), mirroring the Categorize tab."""
    if not cat:
        return None
    key = (cat, subcat if subcat else None)
    if key in tax_defaults:
        return tax_defaults[key]
    if (cat, None) in tax_defaults:
        return tax_defaults[(cat, None)]
    return None


_SPLIT_COLS = ["Amount", "Category", "Subcategory", "Payee", "Payor", "Check #", "Note"]


def _blank_leg_row():
    return {c: (None if c == "Amount" else "") for c in _SPLIT_COLS}


def _render_split_editor(conn, txn_id, key_prefix="split"):
    """Parent summary + legs editor + Create/Update/Unsplit for one split.

    `txn_id` may be a normal row (create a split), a split parent (edit), or a
    leg (resolves to its parent). Shared by the Split Transaction tab and the
    Edit Transactions "Split selected" action; `key_prefix` keeps widget keys
    distinct between the two call sites.

    Leg amounts are entered exactly as-is (the original keeps its sign); the only
    rule is that the legs must sum to the original amount.
    """
    import json
    from ui._amount_style import format_signed_amount

    category_names = queries.get_category_names(conn)
    payor_options = ["", "David", "Debra", "Both", "Unknown"]
    subcats_map, tax_defaults = _build_subcats_map(conn)

    parent = queries.get_transaction(conn, txn_id)
    if parent is None:
        st.error("Transaction not found.")
        return
    if parent["split_parent_id"] is not None:
        parent = queries.get_transaction(conn, parent["split_parent_id"])
    parent_id = parent["id"]

    existing_legs = queries.get_split_children(conn, parent_id)
    is_split = len(existing_legs) > 0
    parent_amt = Decimal(str(parent["amount"])).quantize(Decimal("0.01"))

    st.markdown(
        f"**Original (parent):** #{parent['id']} · {parent['date']} · "
        f"{parent['source']} · {format_signed_amount(float(parent_amt))} · "
        f"{parent['description_raw']}"
    )
    st.caption(
        f"Already split into {len(existing_legs)} leg(s) — edit below."
        if is_split else
        "Not split yet. Enter each leg's amount as-is (a deposit stays positive); "
        "the legs just have to sum to the original. Pick a Category and the "
        "Subcategory list narrows to that category."
    )

    # Working rows live in session_state so edits + add/remove survive reruns.
    state_key = f"{key_prefix}_legs_{parent_id}"
    if state_key not in st.session_state:
        if is_split:
            st.session_state[state_key] = [{
                "Amount": float(Decimal(str(l["amount"]))),
                "Category": l["category"] or "",
                "Subcategory": l["subcategory"] or "",
                "Payee": l["payee"] or "",
                "Payor": l["payor"] or "",
                "Check #": l["check_number"] or "",
                "Note": l["note"] or "",
            } for l in existing_legs]
        else:
            st.session_state[state_key] = [_blank_leg_row(), _blank_leg_row()]

    legs_rows = st.session_state[state_key]

    col_add, col_rm, _ = st.columns([1, 1, 4])
    if col_add.button("➕ Add leg", key=f"{key_prefix}_add_{parent_id}",
                      disabled=len(legs_rows) >= queries.MAX_SPLIT_LEGS):
        legs_rows.append(_blank_leg_row())
        st.session_state[state_key] = legs_rows
        st.rerun()
    if col_rm.button("➖ Remove leg", key=f"{key_prefix}_rm_{parent_id}",
                     disabled=len(legs_rows) <= 2):
        legs_rows.pop()
        st.session_state[state_key] = legs_rows
        st.rerun()

    df = pd.DataFrame(legs_rows, columns=_SPLIT_COLS)

    # Subcategory dropdown narrows to the row's selected Category (same cascade
    # as the Categorize tab). Uses cellEditorSelector at the top level of the
    # column def — st_aggrid only converts JsCode reliably at that position;
    # a JsCode passed as `cellEditorParams` (nested) stays a string on the JS
    # side and the dropdown ends up empty.
    subcat_selector = JsCode(
        "function(params){ var m=" + json.dumps(subcats_map) + ";"
        " var c=params.data.Category;"
        " var vals=(c && m[c])?m[c]:[''];"
        " return {component:'agSelectCellEditor', params:{values:vals}}; }"
    )
    from ui._amount_style import amount_cell_style, amount_value_formatter
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, editable=True)
    gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)
    gb.configure_column("Amount", width=110, type=["numericColumn"],
                        valueFormatter=amount_value_formatter(),
                        cellStyle=amount_cell_style())
    gb.configure_column("Category", width=160, cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": [""] + category_names})
    gb.configure_column("Subcategory", width=180,
                        cellEditorSelector=subcat_selector)
    gb.configure_column("Payee", width=160)
    gb.configure_column("Payor", width=100, cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": payor_options})
    gb.configure_column("Check #", width=100)
    gb.configure_column("Note", width=170)

    resp = AgGrid(
        df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.VALUE_CHANGED,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True,
        height=80 + 30 * len(df),
        # key includes row count so add/remove rebuilds; stable during edits.
        key=f"{key_prefix}_grid_{parent_id}_{len(df)}",
    )
    # Persist edits back so they survive the next rerun (add/remove, etc.)
    edited_records = resp["data"].to_dict("records")
    st.session_state[state_key] = edited_records

    # Live balance check — legs are summed exactly as entered (we don't touch
    # the sign) and must equal the original amount.
    legs_sum = Decimal("0.00")
    parsed = []
    for rec in edited_records:
        amt = rec.get("Amount")
        if amt is None or amt == "" or (isinstance(amt, float) and pd.isna(amt)):
            continue
        try:
            val = Decimal(str(amt)).quantize(Decimal("0.01"))
        except Exception:
            continue
        legs_sum += val
        parsed.append((rec, val))
    n_legs = len(parsed)
    remainder = parent_amt - legs_sum

    c1, c2, c3 = st.columns(3)
    c1.metric("Parent amount", format_signed_amount(float(parent_amt)))
    c2.metric("Legs total", format_signed_amount(float(legs_sum)))
    c3.metric("Remainder", format_signed_amount(float(remainder)))

    balanced = (remainder == 0)
    count_ok = (2 <= n_legs <= queries.MAX_SPLIT_LEGS)
    if not count_ok:
        st.warning(
            f"A split needs between 2 and {queries.MAX_SPLIT_LEGS} legs "
            f"with an amount (currently {n_legs})."
        )
    if balanced and count_ok:
        st.success("Balanced — legs sum exactly to the parent.")
    elif not balanced:
        st.error(
            f"Off by {format_signed_amount(float(remainder))}. "
            "Adjust the leg amounts until the remainder is $0.00."
        )

    col_save, col_cancel, col_unsplit, _ = st.columns([1, 1, 1, 2])

    if col_cancel.button("Cancel", key=f"{key_prefix}_cancel_{parent_id}"):
        # Discard in-progress legs; close the inline editor when on the Edit page.
        st.session_state.pop(state_key, None)
        if key_prefix == "editsplit":
            st.session_state.pop("edit_split_id", None)
        st.rerun()

    save_label = "Update split" if is_split else "Create split"
    if col_save.button(save_label, type="primary",
                       disabled=not (balanced and count_ok),
                       key=f"{key_prefix}_save_{parent_id}"):
        legs = []
        for rec, val in parsed:
            cat = _split_cell(rec.get("Category"))
            subcat = _split_cell(rec.get("Subcategory"))
            legs.append({
                "amount": str(val),
                "category": cat,
                "subcategory": subcat,
                "payee": _split_cell(rec.get("Payee")),
                "payor": _split_cell(rec.get("Payor")),
                "check_number": _split_cell(rec.get("Check #")),
                "note": _split_cell(rec.get("Note")),
                "tax_flags": _resolve_leg_tax_flags(tax_defaults, cat, subcat),
            })
        try:
            queries.replace_split_legs(conn, parent_id, legs, status="confirmed")
            st.session_state.pop(state_key, None)
            st.success(f"{save_label}: {len(legs)} leg(s) saved (status: confirmed).")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

    if is_split:
        if col_unsplit.button("Unsplit (remove all legs)",
                              key=f"{key_prefix}_unsplit_{parent_id}"):
            removed = queries.unsplit_transaction(conn, parent_id)
            st.session_state.pop(state_key, None)
            st.success(
                f"Removed {removed} leg(s). "
                f"#{parent['id']} is a normal transaction again."
            )
            st.rerun()


def _split_transaction(conn):
    from ui._amount_style import format_signed_amount

    st.subheader("Split Transaction")
    st.caption(
        "Break one transaction into several legs, each separately categorized — "
        "e.g. a single teller deposit that was really two checks. The original "
        "row is **never changed** (so it still matches your bank statement and "
        "survives re-ingest) but it's excluded from report totals; the legs "
        "carry the dollars and must sum **exactly** to it. Splits are for "
        "after-the-fact cleanup — they aren't part of the normal ingest run."
    )

    # --- Health view: existing splits + integrity ---
    parent_ids = queries.get_parent_ids(conn)
    broken = queries.check_split_integrity(conn)
    if parent_ids:
        broken_ids = {b["id"] for b in broken}
        title = f"Existing splits ({len(parent_ids)})"
        title += f" — ⚠ {len(broken)} unbalanced" if broken else " — all balanced ✓"
        with st.expander(title, expanded=bool(broken)):
            ref = []
            for pid in sorted(parent_ids):
                p = queries.get_transaction(conn, pid)
                legs = queries.get_split_children(conn, pid)
                legs_sum = sum(Decimal(str(l["amount"])) for l in legs)
                ref.append({
                    "Parent ID": pid,
                    "Date": p["date"],
                    "Source": p["source"],
                    "Description": (p["description_raw"] or "")[:50],
                    "Parent": format_signed_amount(float(p["amount"])),
                    "Legs": len(legs),
                    "Legs total": format_signed_amount(float(legs_sum)),
                    "Balanced": "—" if pid in broken_ids else "✓",
                })
            st.dataframe(pd.DataFrame(ref), use_container_width=True, hide_index=True)
            if broken:
                st.error(
                    "Unbalanced splits understate your reports silently — the "
                    "report MATCH checksum can't catch them. Open each one below "
                    "and fix the legs (or unsplit)."
                )

    st.divider()

    # --- Step 1: pick a transaction ---
    search = st.text_input(
        "Find the transaction to split / edit "
        "(search payee, description, note, category, or source)",
        key="split_search",
    )
    if not search:
        st.info("Type a search above to find the transaction.")
        return

    rows = queries.get_transactions(conn, search=search)
    if not rows:
        st.info("No transactions match that search.")
        return

    # Build candidate options. A leg resolves to its parent for editing; each
    # split appears once.
    MAX_OPTS = 200
    options: list[tuple[str, int]] = []
    seen: set[int] = set()
    for r in rows[:MAX_OPTS]:
        if r["split_parent_id"] is not None:
            target, role = r["split_parent_id"], "leg"
        elif r["id"] in parent_ids:
            target, role = r["id"], "split parent"
        else:
            target, role = r["id"], ""
        if target in seen:
            continue
        seen.add(target)
        p = queries.get_transaction(conn, target)
        tag = f" [{role}]" if role else ""
        label = (f"#{p['id']} | {p['date']} | {p['source']} | "
                 f"{format_signed_amount(float(p['amount']))} | "
                 f"{(p['payee'] or p['description_raw'] or '')[:40]}{tag}")
        options.append((label, target))

    if len(rows) > MAX_OPTS:
        st.caption(f"Showing first {MAX_OPTS} of {len(rows)} matches — refine your search.")

    choice = st.selectbox("Transaction", [o[0] for o in options], key="split_choice")
    if not choice:
        return
    parent_id = dict(options)[choice]

    st.divider()
    _render_split_editor(conn, parent_id, key_prefix="splittab")


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
