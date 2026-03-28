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

    st.warning(f"**{len(unmatched)}** unrecognized description(s) remaining")

    # --- Top: unmatched descriptions, de-duped, sorted, with one-off payee assignment ---
    st.markdown("#### Unmatched Descriptions")
    st.caption(
        "De-duplicated and sorted. For recurring payees, use **Add Rule** below. "
        "For one-offs (checks, withdrawals), type a payee name directly and click **Assign**."
    )

    desc_counts = Counter(item["cleaned_desc"] for item in unmatched)
    seen = set()
    display_items = []
    for item in unmatched:
        desc = item["cleaned_desc"]
        if desc in seen:
            continue
        seen.add(desc)
        display_items.append({
            "cleaned_desc": desc,
            "via": item["via"] or "",
            "count": desc_counts[desc],
            "source": item["source"],
            "ids": [i["id"] for i in unmatched if i["cleaned_desc"] == desc],
            "raw": item["description_raw"],
        })

    # Scrollable container with inline assign
    for idx, item in enumerate(display_items):
        col_desc, col_via, col_count, col_payee, col_btn = st.columns([4, 1, 0.5, 2, 1])
        col_desc.text(item["cleaned_desc"])
        col_via.text(item["via"])
        col_count.text(str(item["count"]))
        payee_val = col_payee.text_input(
            "Payee", key=f"oneoff_{idx}", label_visibility="collapsed",
            placeholder="Type payee name...",
        )
        if col_btn.button("Assign", key=f"assign_{idx}"):
            if payee_val and payee_val.strip():
                name = payee_val.strip()
                via = item["via"] or None
                for tid in item["ids"]:
                    updates = {"payee": name}
                    if via:
                        updates["via"] = via
                    queries.update_transaction(conn, tid, **updates)
                st.session_state.pop("norm_run", None)
                st.rerun()

    st.divider()

    # --- Add a reusable normalization rule ---
    st.markdown("#### Add Normalization Rule")
    st.caption(
        "For recurring payees: enter a search pattern (case-insensitive substring match) "
        "and the normalized name. All matching transactions update and the rule is saved for future months."
    )

    with st.form("norm_rule_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        pattern = col1.text_input("Search Pattern", placeholder="e.g. WHOLEFDS")
        name = col2.text_input("Normalized Name", placeholder="e.g. Whole Foods")
        submitted = st.form_submit_button("Apply Rule")

    if submitted and pattern and name:
        count = apply_pattern_rule(conn, pattern.strip(), name.strip())
        if count > 0:
            st.success(f"Matched **{count}** transaction(s) → **{name}**")
        else:
            st.warning(f"No pending transactions matched pattern '{pattern}'")
        st.session_state.pop("norm_run", None)
        st.rerun()

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Re-scan Unmatched"):
            st.session_state.pop("norm_run", None)
            st.rerun()
    with col2:
        if st.button("Re-run All Normalization"):
            # Clear payee/via on all pending transactions and re-apply rules
            conn.execute(
                "UPDATE transactions SET payee = NULL, via = NULL "
                "WHERE status IN ('pending', 'needs_review')"
            )
            conn.commit()
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

    # Check for unnormalized transactions
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
    tax_defaults_map = {}
    for cat in all_categories:
        c = cat["category"]
        sc = cat["subcategory"]
        if c not in subcats_map:
            subcats_map[c] = []
        if sc:
            subcats_map[c].append(sc)
        if cat["tax_flag_default"]:
            tax_defaults_map[(c, sc)] = cat["tax_flag_default"]

    payor_options = ["", "David", "Debra", "Both", "Unknown"]

    # Group pending transactions by payee
    payee_groups = {}
    for t in pending:
        payee = t["payee"] or "(no payee)"
        if payee not in payee_groups:
            payee_groups[payee] = []
        payee_groups[payee].append(t)

    sorted_payees = sorted(payee_groups.keys())

    st.caption(f"{len(sorted_payees)} unique payee(s), {len(pending)} transaction(s) pending")

    # --- Top: clickable payee table ---
    summary_data = []
    for payee in sorted_payees:
        txns = payee_groups[payee]
        total = sum(float(t["amount"]) for t in txns)
        summary_data.append({
            "Payee": payee,
            "# Txns": len(txns),
            "Total": total,
            "Category": txns[0]["category"] or "",
        })
    summary_df = pd.DataFrame(summary_data)

    selection = st.dataframe(
        summary_df,
        use_container_width=True,
        hide_index=True,
        height=350,
        column_config={
            "Total": st.column_config.NumberColumn(format="$%.2f"),
        },
        on_select="rerun",
        selection_mode="single-row",
        key="payee_table",
    )

    # Determine selected payee
    selected_rows = selection.get("selection", {}).get("rows", [])
    if not selected_rows:
        st.info("Click a payee row above to assign its category.")
        _cat_footer(conn)
        return

    selected_idx = selected_rows[0]
    if selected_idx >= len(sorted_payees):
        st.info("Click a payee row above to assign its category.")
        _cat_footer(conn)
        return

    payee_select = sorted_payees[selected_idx]
    txns = payee_groups[payee_select]

    # --- Bottom: selected payee detail + category form ---
    st.divider()
    st.markdown(f"#### {payee_select}")
    st.caption(f"{len(txns)} transaction(s)")

    # Show this payee's transactions
    txn_display = []
    for t in txns:
        txn_display.append({
            "Date": t["date"],
            "Source": t["source"],
            "Amount": float(t["amount"]),
            "Description": t["description_raw"],
            "Via": t["via"] or "",
        })
    st.dataframe(
        pd.DataFrame(txn_display),
        use_container_width=True,
        hide_index=True,
        height=min(180, 35 * len(txn_display) + 38),
        column_config={
            "Amount": st.column_config.NumberColumn(format="$%.2f"),
        },
    )

    # Use payee name in keys so widgets reset when selection changes
    pk = payee_select.replace(" ", "_")

    # Category / subcategory — outside form so subcategory updates dynamically
    col1, col2 = st.columns(2)
    category = col1.selectbox("Category", [""] + category_names, key=f"cat_{pk}")
    subcats = subcats_map.get(category, []) if category else []
    subcategory = col2.selectbox("Subcategory", [""] + subcats, key=f"subcat_{pk}")

    # Auto-populate tax flags from category defaults
    default_flags = []
    if category and (category, subcategory or None) in tax_defaults_map:
        default_flags = [f.strip() for f in tax_defaults_map[(category, subcategory or None)].split(",")]
    elif category and (category, None) in tax_defaults_map:
        default_flags = [f.strip() for f in tax_defaults_map[(category, None)].split(",")]

    tax_flags = st.multiselect(
        "Tax Flags",
        TAX_FLAG_OPTIONS,
        default=[f for f in default_flags if f in TAX_FLAG_OPTIONS],
        key=f"tax_{pk}",
    )

    col3, col4 = st.columns(2)
    payor = col3.selectbox("Payor", payor_options, key=f"payor_{pk}")
    note = col4.text_input("Note", key=f"note_{pk}")

    col_save, col_skip, col_cancel = st.columns(3)
    save = col_save.button("Apply to All", type="primary")
    skip = col_skip.button("Skip (Needs Review)")
    cancel = col_cancel.button("Cancel")

    if save and category:
        tax_str = ", ".join(tax_flags) if tax_flags else None
        for t in txns:
            queries.update_transaction(
                conn, t["id"],
                category=category,
                subcategory=subcategory or None,
                tax_flags=tax_str,
                payor=payor or None,
                note=note or None,
                status="confirmed",
                overridden=1,
            )
        # Save as payee default for future months
        if payee_select != "(no payee)":
            save_payee_defaults(conn, [{
                "normalized_name": payee_select,
                "category": category,
                "subcategory": subcategory or None,
                "tax_flags": tax_str,
                "payor": payor or None,
                "note": note or None,
            }])
        st.session_state.pop("cat_auto_done", None)
        st.rerun()
    elif save and not category:
        st.error("Please select a category.")
    elif skip:
        for t in txns:
            queries.update_transaction(conn, t["id"], status="needs_review")
        st.session_state.pop("cat_auto_done", None)
        st.rerun()
    elif cancel:
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
