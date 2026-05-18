"""Normalize & Categorize — resumable workflow for pending transactions."""

import streamlit as st
import pandas as pd
from collections import Counter
from datetime import date

from db import queries
from processing.normalize import (
    normalize_transactions, apply_normalization_edits, apply_pattern_rule,
    detect_via, strip_via_prefix, seed_normalization_rules,
)
from processing.categorize import auto_categorize, apply_category_edits, save_payee_defaults
from processing.placeholders import (
    PLACEHOLDER_PAYEES, is_placeholder_payee,
    ENRICHMENT_VIAS, is_awaiting_enrichment,
)
from processing.reports import generate_pending_items_pdf


def _ai_classify_section(conn):
    """Render the 'Ask Claude' UI for AI-suggested categorization.

    Shows the count of pending rows that have a payee but no category, and
    a button that batches them off to the Anthropic API. Suggestions land
    on the transactions immediately with a [AI: ...] marker in the note;
    the user reviews them in the grid below and edits before Save Categorized.
    """
    import os
    candidates = conn.execute("""
        SELECT COUNT(DISTINCT payee) AS unique_payees,
               COUNT(*) AS rows
        FROM transactions
        WHERE status = 'pending'
          AND payee IS NOT NULL AND payee != ''
          AND category IS NULL
    """).fetchone()
    n_payees = candidates["unique_payees"] or 0
    n_rows = candidates["rows"] or 0

    if n_payees == 0:
        return  # nothing to ask Claude about

    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))

    with st.expander(
        f"🤖 Ask Claude to suggest categories for {n_payees} unmapped payee(s) "
        f"({n_rows} row(s))",
        expanded=False,
    ):
        st.caption(
            "Sends payee names (and any transaction notes) to Claude, which "
            "returns a suggested category and subcategory for each. Suggestions "
            "land on the transactions in the grid below with an [AI: ...] tag "
            "in the note so you know which ones to double-check. You then "
            "review and edit before clicking **Save Categorized**."
        )
        if not api_key_set:
            st.error(
                "**ANTHROPIC_API_KEY environment variable is not set.** Get a "
                "key at console.anthropic.com (a $5 balance covers years of "
                "typical use), set it as a system environment variable, and "
                "restart Streamlit. See the project memory for setup details."
            )
            return

        if st.button("Get AI suggestions", key="ai_classify_go"):
            with st.spinner(f"Asking Claude about {n_payees} payee(s)..."):
                try:
                    from processing.ai_classify import classify_pending_unmapped
                    result = classify_pending_unmapped(conn)
                    n_applied = result["applied"]
                    n_classified = result["payees_classified"]
                    warnings = result.get("warnings", [])

                    # Stash warnings for display after rerun
                    if warnings:
                        st.session_state.ai_classify_warnings = warnings

                    st.success(
                        f"Got suggestions for {n_classified} payee(s); applied "
                        f"to {n_applied} pending row(s). Review the grid below "
                        f"— each suggested row has an [AI: ...] tag in its note."
                    )
                    st.session_state.pop("cat_auto_done", None)
                    st.rerun()
                except RuntimeError as e:
                    st.error(f"AI classification failed: {e}")
                except Exception as e:
                    st.error(f"Unexpected error: {type(e).__name__}: {e}")

    # Show any warnings from the previous run (out-of-DB categories/subcategories
    # that were rejected or cleaned up before being applied).
    warnings = st.session_state.get("ai_classify_warnings")
    if warnings:
        with st.expander(
            f"⚠ {len(warnings)} validation warning(s) from the last AI run "
            "(click to expand)",
            expanded=False,
        ):
            st.caption(
                "These suggestions were rejected or cleaned up because they "
                "didn't match a valid category/subcategory in your database. "
                "No invalid values were written to any transaction."
            )
            for w in warnings:
                st.write(f"- {w}")
            if st.button("Dismiss warnings", key="ai_dismiss_warnings"):
                st.session_state.pop("ai_classify_warnings", None)
                st.rerun()


def _render_renormalize_guard(conn, key_prefix: str):
    """Render the typed-phrase guard for the destructive Re-run All Normalization
    action. The action clears payee and via on every pending/needs_review
    transaction — including any enrichment patches and in-progress normalization
    work — so the user must type 'NORMALIZE' to confirm.
    """
    with st.expander("Re-run All Normalization (destructive)"):
        st.warning(
            "⚠ **Destructive — only use if normalization is really broken.** "
            "This clears the payee and via fields on every pending/needs_review "
            "transaction. Any enrichment patches and in-progress normalization "
            "work will be lost. Normalization rules and the master payee list "
            "are untouched, but every pending row gets re-derived from scratch."
        )
        confirm = st.text_input(
            "Type **NORMALIZE** to confirm:",
            key=f"{key_prefix}_confirm",
        )
        col1, col2 = st.columns(2)
        if col1.button("Re-run All Normalization", type="primary",
                        key=f"{key_prefix}_go"):
            if confirm == "NORMALIZE":
                conn.execute(
                    "UPDATE transactions SET payee = NULL, via = NULL "
                    "WHERE status IN ('pending', 'needs_review')"
                )
                conn.commit()
                st.session_state.pop("norm_run", None)
                st.session_state.pop("norm_seeded", None)
                # Clear any in-field edits — fresh start
                for key in list(st.session_state.keys()):
                    if key.startswith("payee_field_"):
                        del st.session_state[key]
                st.success("Normalization cleared on all pending rows.")
                st.rerun()
            else:
                st.error("Confirmation phrase does not match — type **NORMALIZE** exactly.")
        if col2.button("Cancel", key=f"{key_prefix}_cancel"):
            st.session_state.pop(f"{key_prefix}_confirm", None)
            st.rerun()


def normalize_page(conn):
    st.title("Normalize & Categorize")

    pending_count = queries.get_pending_count(conn)
    if pending_count == 0:
        st.success("No pending transactions. Nothing to do.")
        return

    st.info(f"**{pending_count}** pending transaction(s)")

    # Tab-based workflow — user can switch freely
    tab1, tab2 = st.tabs(["Payee Normalization", "Category Assignment"])

    with tab1:
        _normalization(conn)

    with tab2:
        _categorization(conn)


# ---------------------------------------------------------------------------
# Payee Normalization
# ---------------------------------------------------------------------------

def _normalization(conn):
    st.subheader("Payee Normalization")

    # Seed starter rules on first ever run
    if "norm_seeded" not in st.session_state:
        seeded = seed_normalization_rules(conn)
        st.session_state.norm_seeded = True
        if seeded:
            st.info(f"Loaded {seeded} starter normalization rules.")

    # Run or re-run auto-matching
    def _run_matching():
        matched, unmatched = normalize_transactions(conn)
        st.session_state.norm_matched = matched
        st.session_state.norm_unmatched = unmatched
        st.session_state.norm_run = True

    if "norm_run" not in st.session_state:
        _run_matching()

    matched = st.session_state.norm_matched
    unmatched = st.session_state.norm_unmatched

    st.info(f"Auto-matched: **{matched}** transaction(s)")

    if not unmatched:
        st.success("All payees recognized. Switch to the **Category Assignment** tab to continue.")
        _render_renormalize_guard(conn, key_prefix="renorm_top")
        return

    # De-duplicate by cleaned description, gather stats
    desc_groups = {}
    for item in unmatched:
        desc = item["cleaned_desc"]
        if desc not in desc_groups:
            desc_groups[desc] = {
                "cleaned_desc": desc,
                "suggested_name": item["suggested_name"],
                "via": item["via"] or "",
                "count": 0,
                "total": 0.0,
                "ids": [],
                "raw": item["description_raw"],
            }
        desc_groups[desc]["count"] += 1
        desc_groups[desc]["total"] += float(item["amount"])
        desc_groups[desc]["ids"].append(item["id"])

    display_items = sorted(desc_groups.values(), key=lambda x: x["suggested_name"].lower())

    st.warning(f"**{len(display_items)}** unique description(s), **{len(unmatched)}** transaction(s) remaining")

    st.caption(
        "Review each item. The **Suggested Name** is auto-generated. "
        "Click **Accept** to use it as-is, or edit it first. "
        "Click **Copy** to copy the raw description into the edit field. "
        "When done, click **Commit All** at the bottom to save all accepted items as normalization rules."
    )

    # Header row
    col_h1, col_h2, col_h3, col_h4 = st.columns([3, 0.7, 0.8, 3.5])
    col_h1.markdown("**Description**")
    col_h2.markdown("**Count**")
    col_h3.markdown("**Total**")
    col_h4.markdown("**Payee Name**")

    for idx, item in enumerate(display_items):
        col_desc, col_count, col_total, col_name = st.columns([3, 0.7, 0.8, 3.5])

        total = item["total"]
        col_desc.text(item["cleaned_desc"])
        col_count.text(str(item["count"]))
        # Render credits (positive = money in) in green; debits stay default.
        if total > 0:
            col_total.markdown(f":green[\\${total:,.0f}]")
        elif total < 0:
            col_total.text(f"-${abs(total):,.0f}")
        else:
            col_total.text("$0")

        # Editable payee name field - pre-filled with auto-suggestion.
        # Clear this field to exclude the row from Commit All.
        default_val = st.session_state.get(f"payee_field_{idx}", item["suggested_name"])
        col_name.text_input(
            "name", key=f"payee_field_{idx}", value=default_val,
            label_visibility="collapsed",
        )

    st.divider()

    # Bottom row: Commit All + Re-scan + counter
    col_c, col_r, col_caption = st.columns([1, 1, 3])
    col_caption.caption(
        f"{len(display_items)} description(s). Commit All writes every row "
        "with a non-empty name; clear a field to skip that row."
    )

    if col_c.button("Commit All", type="primary",
                    disabled=(len(display_items) == 0)):
        committed = 0
        skipped = 0
        for idx, item in enumerate(display_items):
            field_val = st.session_state.get(f"payee_field_{idx}", item["suggested_name"])
            name = field_val.strip() if field_val else ""
            if not name:
                skipped += 1
                continue

            via = item["via"] or None
            # Create a normalization rule using the cleaned description as the pattern
            pattern = item["cleaned_desc"]
            queries.insert_payee_normalization(conn, pattern, name)
            for tid in item["ids"]:
                updates = {"payee": name}
                if via:
                    updates["via"] = via
                queries.update_transaction(conn, tid, **updates)
            committed += 1

        # Clear the in-field session state so a re-scan starts clean
        for idx in range(len(display_items)):
            st.session_state.pop(f"payee_field_{idx}", None)
        st.session_state.pop("norm_run", None)
        msg = f"Committed {committed} payee(s)."
        if skipped:
            msg += f" Skipped {skipped} row(s) with empty name."
        st.success(msg)
        st.rerun()

    if col_r.button("Re-scan",
                     help="Re-fetch unmatched descriptions from the DB while "
                          "preserving any in-progress edits in the name fields. "
                          "Use this if you changed normalization rules elsewhere "
                          "and want the list refreshed."):
        st.session_state.pop("norm_run", None)
        st.rerun()

    st.divider()

    _render_renormalize_guard(conn, key_prefix="renorm_bottom")


# ---------------------------------------------------------------------------
# Category Assignment
# ---------------------------------------------------------------------------

TAX_FLAG_OPTIONS = [
    "Tax-reportable",
    "Reimbursable",
    "Capital Improvements",
    "Home Office",
    "Donations – Deductible",
    "Medical",
    "Business Expense",
]


def _categorization(conn):
    st.subheader("Category Assignment")
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode

    # Auto-categorize on first visit
    if "cat_auto_done" not in st.session_state:
        auto_count = auto_categorize(conn)
        st.session_state.cat_auto_done = True
        if auto_count:
            st.info(f"Auto-categorized {auto_count} transaction(s) from payee metadata.")

    # AI-assist: classify any unmapped pending rows via Claude.
    _ai_classify_section(conn)

    # Awaiting-enrichment banner (rows whose payee will come from an enrichment
    # file — they're hidden from this tab until that file is applied).
    if ENRICHMENT_VIAS:
        awaiting_count = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM transactions "
            f"WHERE status = 'pending' AND payee IS NULL "
            f"AND via IN ({','.join('?' * len(ENRICHMENT_VIAS))})",
            tuple(ENRICHMENT_VIAS),
        ).fetchone()["cnt"]
        if awaiting_count:
            st.info(
                f"⏳ {awaiting_count} row(s) are awaiting enrichment "
                f"({', '.join(sorted(ENRICHMENT_VIAS))}). They're hidden from "
                f"this tab until the corresponding enrichment file fills in "
                f"the payee. Drop the file in `input/` and apply it on the "
                f"Ingest page."
            )

    # Placeholder-payee hint (Amazon — still uses 'Amazon' as a placeholder)
    if PLACEHOLDER_PAYEES:
        placeholder_count = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM transactions "
            f"WHERE status = 'pending' AND payee IN ({','.join('?' * len(PLACEHOLDER_PAYEES))})",
            tuple(PLACEHOLDER_PAYEES),
        ).fetchone()["cnt"]
        if placeholder_count:
            st.caption(
                f"ℹ️ {placeholder_count} row(s) have a placeholder payee "
                f"({', '.join(sorted(PLACEHOLDER_PAYEES))}). These are normally "
                f"filled in by enrichment files. The payee field is non-editable "
                f"here — to fill it in, drop the matching enrichment file in "
                f"input/ and apply it on the Ingest page."
            )

    pending = queries.get_pending_transactions(conn)
    # Hide rows that are waiting on enrichment — they shouldn't be categorized
    # manually since we don't know who the real payee is yet.
    pending = [t for t in pending if not is_awaiting_enrichment(t["payee"], t["via"])]

    if not pending:
        st.success("No pending transactions awaiting categorization. All done!")
        return

    unnormalized = [t for t in pending if not t["payee"]]
    if unnormalized:
        st.warning(
            f"{len(unnormalized)} transaction(s) still have no payee. "
            "Switch to the **Payee Normalization** tab to resolve them first."
        )

    # Build category/subcategory maps
    category_names = queries.get_category_names(conn)
    all_categories = queries.get_categories(conn)
    subcats_map = {}
    all_subcats = set()
    tax_defaults_map = {}
    for cat in all_categories:
        c = cat["category"]
        sc = cat["subcategory"]
        if c not in subcats_map:
            subcats_map[c] = [""]
        if sc:
            subcats_map[c].append(sc)
            all_subcats.add(sc)
        if cat["tax_flag_default"]:
            tax_defaults_map[(c, sc)] = cat["tax_flag_default"]

    payor_options = ["", "David", "Debra", "Both", "Unknown"]

    # Group pending by payee
    payee_groups = {}
    for t in pending:
        payee = t["payee"] or "(no payee)"
        if payee not in payee_groups:
            payee_groups[payee] = []
        payee_groups[payee].append(t)

    # Toggle: by payee (de-duped) or by transaction (full detail)
    view_mode = st.radio("View", ["By Payee", "By Transaction"], horizontal=True, key="cat_view_mode")

    def _resolve_tax_flags(cat, subcat, explicit_flags):
        """Return explicit flags if set, otherwise look up the default from category."""
        if explicit_flags:
            return explicit_flags
        if cat:
            key = (cat, subcat if subcat else None)
            if key in tax_defaults_map:
                return tax_defaults_map[key]
            if (cat, None) in tax_defaults_map:
                return tax_defaults_map[(cat, None)]
        return ""

    if view_mode == "By Payee":
        grid_data = []
        for payee in sorted(payee_groups.keys()):
            txns = payee_groups[payee]
            total = sum(float(t["amount"]) for t in txns)
            latest_date = max(t["date"] for t in txns)
            cat = txns[0]["category"] or ""
            subcat = txns[0]["subcategory"] or ""
            grid_data.append({
                "Payee": payee,
                "# Txns": len(txns),
                "Total": round(total, 2),
                "Last Date": latest_date,
                "Category": cat,
                "Subcategory": subcat,
                "Tax Flags": _resolve_tax_flags(cat, subcat, txns[0]["tax_flags"]),
                "Payor": txns[0]["payor"] or "",
                "Note": txns[0]["note"] or "",
            })
    else:
        grid_data = []
        for t in sorted(pending, key=lambda x: (x["payee"] or "", x["date"])):
            cat = t["category"] or ""
            subcat = t["subcategory"] or ""
            grid_data.append({
                "Payee": t["payee"] or "(no payee)",
                "# Txns": 1,
                "Total": round(float(t["amount"]), 2),
                "Last Date": t["date"],
                "Category": cat,
                "Subcategory": subcat,
                "Tax Flags": _resolve_tax_flags(cat, subcat, t["tax_flags"]),
                "Payor": t["payor"] or "",
                "Note": t["note"] or "",
                "_id": t["id"],
            })

    df = pd.DataFrame(grid_data)

    if view_mode == "By Payee":
        st.caption(
            f"{len(grid_data)} unique payee(s), {len(pending)} transaction(s) pending. "
            "Edit Category, Subcategory, Payor, and Note inline. Click **Save All** when done."
        )
    else:
        st.caption(
            f"{len(grid_data)} transaction(s) pending. "
            "Edit inline. Click **Save All** when done."
        )

    # JavaScript to dynamically filter subcategory based on category
    import json
    subcats_json = json.dumps(subcats_map)

    subcat_cell_editor_params = JsCode(f"""
        function(params) {{
            var subcatsMap = {subcats_json};
            var cat = params.data.Category;
            if (cat && subcatsMap[cat]) {{
                return {{ values: subcatsMap[cat] }};
            }}
            return {{ values: [''] }};
        }}
    """)

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, sortable=True, editable=False)
    gb.configure_grid_options(singleClickEdit=True, stopEditingWhenCellsLoseFocus=True)

    # Read-only columns
    gb.configure_column("Payee", width=180)
    if view_mode == "By Payee":
        gb.configure_column("# Txns", width=70)
    else:
        gb.configure_column("# Txns", hide=True)
    gb.configure_column("Last Date", width=100)
    from ui._amount_style import amount_cell_style, amount_value_formatter
    gb.configure_column("Total", width=100,
                        type=["numericColumn"],
                        valueFormatter=amount_value_formatter(),
                        cellStyle=amount_cell_style())
    if "_id" in df.columns:
        gb.configure_column("_id", hide=True)

    # Editable columns with dropdowns
    gb.configure_column("Category", editable=True, width=160,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": [""] + category_names})
    gb.configure_column("Subcategory", editable=True, width=160,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams=subcat_cell_editor_params)
    tax_flag_values = [
        "", "Tax-reportable", "Reimbursable", "Capital Improvements",
        "Home Office", "Donations - Deductible", "Medical", "Business Expense",
        "Business Expense, Reimbursable",
        "Business Expense, Home Office",
    ]
    gb.configure_column("Tax Flags", editable=True, width=160,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": tax_flag_values})
    gb.configure_column("Payor", editable=True, width=100,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": payor_options})
    gb.configure_column("Note", editable=True, width=150)

    grid_options = gb.build()

    grid_response = AgGrid(
        df,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.VALUE_CHANGED,
        fit_columns_on_grid_load=True,
        height=450,
        allow_unsafe_jscode=True,
    )

    edited_df = grid_response["data"]

    # Save / Skip buttons
    col1, col2, col3 = st.columns(3)

    if col1.button("Save Categorized", type="primary"):
        saved = 0
        for _, row in edited_df.iterrows():
            payee = row["Payee"]
            cat = row["Category"]
            subcat = row["Subcategory"]
            tax = row["Tax Flags"]
            payor = row["Payor"]
            note = row["Note"]

            if not cat:
                continue

            # Look up tax defaults if user didn't set flags manually
            if not tax and cat:
                key = (cat, subcat if subcat else None)
                if key in tax_defaults_map:
                    tax = tax_defaults_map[key]
                elif (cat, None) in tax_defaults_map:
                    tax = tax_defaults_map[(cat, None)]

            if view_mode == "By Transaction" and "_id" in row:
                # Update just this one transaction
                queries.update_transaction(
                    conn, int(row["_id"]),
                    category=cat,
                    subcategory=subcat or None,
                    tax_flags=tax or None,
                    payor=payor or None,
                    note=note or None,
                    status="confirmed",
                    overridden=1,
                )
            else:
                # Update all transactions for this payee
                txns = payee_groups.get(payee, [])
                for t in txns:
                    queries.update_transaction(
                        conn, t["id"],
                        category=cat,
                        subcategory=subcat or None,
                        tax_flags=tax or None,
                        payor=payor or None,
                        note=note or None,
                        status="confirmed",
                        overridden=1,
                    )

            # Save payee default
            if payee != "(no payee)":
                save_payee_defaults(conn, [{
                    "normalized_name": payee,
                    "category": cat,
                    "subcategory": subcat or None,
                    "tax_flags": tax or None,
                    "payor": payor or None,
                    "note": note or None,
                }])
            saved += 1

        st.session_state.pop("cat_auto_done", None)
        st.success(f"Saved {saved} item(s).")
        st.rerun()

    if col2.button("Set Rest to Needs Review"):
        for _, row in edited_df.iterrows():
            if not row["Category"]:
                txns = payee_groups.get(row["Payee"], [])
                for t in txns:
                    queries.update_transaction(conn, t["id"], status="needs_review")
        st.session_state.pop("cat_auto_done", None)
        st.rerun()

    _cat_footer(conn)


def _cat_footer(conn):
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Print Pending Items"):
            out = generate_pending_items_pdf(conn)
            if out:
                st.success(f"Saved: {out}")
    with col2:
        remaining = queries.get_pending_count(conn)
        st.caption(f"{remaining} pending overall")
