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
from processing.reports import generate_pending_items_pdf


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
        if st.button("Re-run All Normalization"):
            conn.execute(
                "UPDATE transactions SET payee = NULL, via = NULL "
                "WHERE status IN ('pending', 'needs_review')"
            )
            conn.commit()
            st.session_state.pop("norm_run", None)
            st.session_state.pop("norm_seeded", None)
            st.rerun()
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
    col_h1, col_h2, col_h3, col_h4, col_h5, col_h6, col_h7 = st.columns([3, 0.7, 0.8, 0.6, 2.5, 0.7, 0.7])
    col_h1.markdown("**Description**")
    col_h2.markdown("**Count**")
    col_h3.markdown("**Total**")
    col_h4.markdown("")
    col_h5.markdown("**Payee Name**")
    col_h6.markdown("")
    col_h7.markdown("")

    # Track which items have been accepted (in session state)
    if "norm_accepted" not in st.session_state:
        st.session_state.norm_accepted = {}

    for idx, item in enumerate(display_items):
        raw_key = item["raw"]
        already_accepted = raw_key in st.session_state.norm_accepted

        col_desc, col_count, col_total, col_copy, col_name, col_accept, col_undo = st.columns([3, 0.7, 0.8, 0.6, 2.5, 0.7, 0.7])

        if already_accepted:
            accepted_name = st.session_state.norm_accepted[raw_key]
            col_desc.markdown(f"~~{item['cleaned_desc']}~~")
            col_count.text(str(item["count"]))
            col_total.text(f"${abs(item['total']):,.0f}")
            col_name.markdown(f"**{accepted_name}**")
            if col_undo.button("Undo", key=f"undo_{idx}"):
                del st.session_state.norm_accepted[raw_key]
                st.rerun()
        else:
            col_desc.text(item["cleaned_desc"])
            col_count.text(str(item["count"]))
            col_total.text(f"${abs(item['total']):,.0f}")

            # Copy button puts cleaned description into the text field
            copy_key = f"copy_flag_{idx}"
            if col_copy.button("Copy", key=f"copy_{idx}"):
                st.session_state[f"payee_field_{idx}"] = item["cleaned_desc"]
                st.rerun()

            # Editable payee name field - pre-filled with auto-suggestion
            default_val = st.session_state.get(f"payee_field_{idx}", item["suggested_name"])
            payee_val = col_name.text_input(
                "name", key=f"payee_field_{idx}", value=default_val,
                label_visibility="collapsed",
            )

            if col_accept.button("Accept", key=f"accept_{idx}"):
                name = payee_val.strip() if payee_val else item["suggested_name"]
                st.session_state.norm_accepted[raw_key] = name
                st.rerun()

    st.divider()

    # Commit all accepted items
    accepted_count = len(st.session_state.get("norm_accepted", {}))
    remaining_count = len(display_items) - accepted_count

    col1, col2, col3 = st.columns([1, 1, 2])
    col3.caption(f"{accepted_count} accepted, {remaining_count} remaining")

    if col1.button("Commit All", type="primary", disabled=(accepted_count == 0)):
        accepted = st.session_state.norm_accepted
        for item in display_items:
            raw_key = item["raw"]
            if raw_key not in accepted:
                continue
            name = accepted[raw_key]
            via = item["via"] or None

            # Create a normalization rule for future months
            # Use the cleaned description as the search pattern
            pattern = item["cleaned_desc"]
            queries.insert_payee_normalization(conn, pattern, name)

            # Update all matching transactions
            for tid in item["ids"]:
                updates = {"payee": name}
                if via:
                    updates["via"] = via
                queries.update_transaction(conn, tid, **updates)

        st.session_state.pop("norm_accepted", None)
        st.session_state.pop("norm_run", None)
        st.success(f"Committed {accepted_count} payee(s).")
        st.rerun()

    if col2.button("Re-scan"):
        st.session_state.pop("norm_accepted", None)
        st.session_state.pop("norm_run", None)
        st.rerun()

    st.divider()

    if st.button("Re-run All Normalization"):
        conn.execute(
            "UPDATE transactions SET payee = NULL, via = NULL "
            "WHERE status IN ('pending', 'needs_review')"
        )
        conn.commit()
        st.session_state.pop("norm_accepted", None)
        st.session_state.pop("norm_run", None)
        st.session_state.pop("norm_seeded", None)
        st.rerun()


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

    pending = queries.get_pending_transactions(conn)
    if not pending:
        st.success("No pending transactions. All done!")
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

    if view_mode == "By Payee":
        grid_data = []
        for payee in sorted(payee_groups.keys()):
            txns = payee_groups[payee]
            total = sum(float(t["amount"]) for t in txns)
            latest_date = max(t["date"] for t in txns)
            grid_data.append({
                "Payee": payee,
                "# Txns": len(txns),
                "Total": round(total, 2),
                "Last Date": latest_date,
                "Category": txns[0]["category"] or "",
                "Subcategory": txns[0]["subcategory"] or "",
                "Tax Flags": txns[0]["tax_flags"] or "",
                "Payor": txns[0]["payor"] or "",
                "Note": txns[0]["note"] or "",
            })
    else:
        grid_data = []
        for t in sorted(pending, key=lambda x: (x["payee"] or "", x["date"])):
            grid_data.append({
                "Payee": t["payee"] or "(no payee)",
                "# Txns": 1,
                "Total": round(float(t["amount"]), 2),
                "Last Date": t["date"],
                "Category": t["category"] or "",
                "Subcategory": t["subcategory"] or "",
                "Tax Flags": t["tax_flags"] or "",
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
    gb.configure_column("Total", width=100,
                        type=["numericColumn"],
                        valueFormatter=JsCode("function(params) { return '$' + params.value.toFixed(2); }"))
    if "_id" in df.columns:
        gb.configure_column("_id", hide=True)

    # Editable columns with dropdowns
    gb.configure_column("Category", editable=True, width=160,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams={"values": [""] + category_names})
    gb.configure_column("Subcategory", editable=True, width=160,
                        cellEditor="agSelectCellEditor",
                        cellEditorParams=subcat_cell_editor_params)
    gb.configure_column("Tax Flags", editable=True, width=140)
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
