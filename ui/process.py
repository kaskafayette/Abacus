"""Process Latest — stepped workflow UI (Steps 1–5)."""

import streamlit as st
import pandas as pd
from datetime import date, datetime
from pathlib import Path

from db import queries
from processing.ingest import (
    INPUT_DIR, validate_all_files, read_csv_headers, parse_filename,
    ingest_file, FileValidationResult,
)
from processing.normalize import (
    normalize_transactions, apply_normalization_edits, apply_pattern_rule,
    detect_via, strip_via_prefix, seed_normalization_rules,
)
from processing.categorize import auto_categorize, apply_category_edits, save_payee_defaults
from processing.reports import (
    generate_monthly_pdf, generate_pending_items_pdf, generate_excel_export,
)

STEPS = ["File Validation", "Template Setup", "Payee Normalization",
         "Category Assignment", "Generate Reports"]


def process_page(conn):
    st.title("Process Latest")

    # Initialize step state
    if "process_step" not in st.session_state:
        st.session_state.process_step = 1

    # Check for resume
    if st.session_state.get("resume_pending"):
        st.session_state.process_step = 4
        st.session_state.pop("resume_pending", None)

    step = st.session_state.process_step

    # Progress bar
    st.progress(step / len(STEPS))
    st.caption(f"Step {step} of {len(STEPS)}: {STEPS[step - 1]}")

    if step == 1:
        _step1_validation(conn)
    elif step == 2:
        _step2_template(conn)
    elif step == 3:
        _step3_normalization(conn)
    elif step == 4:
        _step4_categorization(conn)
    elif step == 5:
        _step5_reports(conn)


# ---------------------------------------------------------------------------
# Step 1 — File Validation
# ---------------------------------------------------------------------------

def _step1_validation(conn):
    st.subheader("Step 1 — File Validation")

    st.info(
        "Files must be named: **`<account-prefix> MM-DD-YYYY to MM-DD-YYYY.csv`**\n\n"
        "Example: `Chase5616 01-01-2026 to 01-31-2026.csv`\n\n"
        "The prefix must match a known account or a new one will be set up. "
        "Extra spaces between prefix and date are OK."
    )

    col1, col2 = st.columns(2)
    period_start = col1.date_input("Processing period start", value=date.today().replace(day=1))
    period_end = col2.date_input("Processing period end", value=date.today())

    if st.button("Validate Files"):
        INPUT_DIR.mkdir(exist_ok=True)
        results = validate_all_files(conn, period_start, period_end)
        st.session_state.validation_results = results
        st.session_state.period_start = period_start
        st.session_state.period_end = period_end

    results = st.session_state.get("validation_results", [])

    if not results:
        csv_files = [f for f in INPUT_DIR.iterdir() if f.suffix.lower() == ".csv"]
        if not csv_files:
            st.warning("No CSV files found in the input folder.")
        return

    # Display results table
    error_count = sum(len(r.errors) for r in results)
    needs_template = sum(1 for r in results if r.needs_template)

    if error_count:
        st.error(f"{error_count} error(s) found across {len(results)} file(s).")
    elif needs_template:
        st.warning(f"{needs_template} file(s) need template setup.")
    else:
        st.success("All files validated successfully.")

    for r in results:
        prefix = r.parsed["prefix"] if r.parsed else "?"
        date_range = (
            f"{r.parsed['start_date']} – {r.parsed['end_date']}"
            if r.parsed else "?"
        )
        status = "OK" if r.ok else ("Needs Template" if r.needs_template else "Error")
        icon = "✅" if r.ok else ("🔧" if r.needs_template else "❌")

        with st.expander(f"{icon} {r.filename} — {status}"):
            st.write(f"**Prefix:** {prefix}")
            st.write(f"**Date range:** {date_range}")
            for err in r.errors:
                st.error(err)
            for warn in r.warnings:
                st.warning(warn)

            if r.needs_template:
                st.info("This prefix needs a column template. It will be set up in Step 2.")
                # Inline label entry for the source
                label = st.text_input(
                    f"Source label for '{prefix}'",
                    value=prefix,
                    key=f"label_{prefix}",
                )
                if st.button(f"Save label for {prefix}", key=f"save_label_{prefix}"):
                    queries.upsert_source_file_map(conn, prefix, label)
                    st.success(f"Saved: {prefix} → {label}")

    # Proceed button
    all_ok = all(r.ok or r.needs_template for r in results) and error_count == 0
    if all_ok and results:
        if needs_template:
            if st.button("Proceed to Template Setup →"):
                st.session_state.process_step = 2
                st.rerun()
        else:
            if st.button("Proceed to Ingestion →"):
                _run_ingestion(conn, results)


def _run_ingestion(conn, results: list[FileValidationResult]):
    """Ingest all validated files."""
    ingested = 0
    total_txns = 0
    for r in results:
        if not r.parsed or r.errors:
            continue
        filepath = INPUT_DIR / r.filename
        if not filepath.exists():
            continue
        template = r.template or queries.get_column_template(conn, r.parsed["prefix"])
        if not template:
            st.error(f"No template for {r.parsed['prefix']} — skipping.")
            continue
        try:
            txns = ingest_file(conn, filepath, template)
            ingested += 1
            total_txns += len(txns)
        except ValueError as e:
            st.error(str(e))
            return

    st.success(f"Ingested {ingested} file(s), {total_txns} transaction(s).")
    st.session_state.process_step = 3
    st.rerun()


# ---------------------------------------------------------------------------
# Step 2 — Template Setup
# ---------------------------------------------------------------------------

def _step2_template(conn):
    st.subheader("Step 2 — Template Setup (new accounts)")

    results = st.session_state.get("validation_results", [])
    new_accounts = [r for r in results if r.needs_template and r.parsed]

    if not new_accounts:
        st.info("No new accounts to set up. Moving on.")
        st.session_state.process_step = 3
        st.rerun()
        return

    all_saved = True
    for r in new_accounts:
        prefix = r.parsed["prefix"]
        filepath = INPUT_DIR / r.filename

        st.markdown(f"### {prefix}")

        # Show CSV headers
        headers = read_csv_headers(filepath)
        st.write("**CSV columns detected:**", ", ".join(f"`{h}`" for h in headers))

        # Preview first few rows using our cleaned reader
        try:
            from processing.ingest import read_csv_rows
            _, all_rows = read_csv_rows(filepath)
            preview_rows = all_rows[:10]
            if preview_rows:
                df_preview = pd.DataFrame(preview_rows, columns=headers)
                st.dataframe(df_preview, use_container_width=True, height=300)
        except Exception:
            pass

        with st.form(f"template_{prefix}"):
            date_col = st.selectbox("Post date column", headers, key=f"date_{prefix}")
            date_fmt = st.text_input(
                "Date format (strptime)", value="%m/%d/%Y", key=f"datefmt_{prefix}"
            )

            check_col = st.selectbox(
                "Check number column (or None)",
                [None] + headers, key=f"check_{prefix}"
            )

            amount_mode = st.radio(
                "Amount mode", ["single", "split"], key=f"amtmode_{prefix}"
            )

            if amount_mode == "single":
                amt_col = st.selectbox("Amount column", headers, key=f"amt_{prefix}")
                sign_conv = st.radio(
                    "Sign convention", ["negative_is_debit", "positive_is_debit"],
                    key=f"sign_{prefix}"
                )
                debit_col = None
                credit_col = None
            else:
                amt_col = None
                sign_conv = None
                debit_col = st.selectbox("Debit column", headers, key=f"debit_{prefix}")
                credit_col = st.selectbox("Credit column", headers, key=f"credit_{prefix}")

            desc_col = st.selectbox("Description column", headers, key=f"desc_{prefix}")

            cat_raw_col = st.selectbox(
                "Category column from source (or None)",
                [None] + headers, key=f"catraw_{prefix}"
            )

            card_col = st.selectbox(
                "Card/sub-account column (or None)",
                [None] + headers, key=f"card_{prefix}"
            )

            submitted = st.form_submit_button("Save Template")
            if submitted:
                # Save source label if not already set
                if not queries.get_source_label(conn, prefix):
                    queries.upsert_source_file_map(conn, prefix, prefix)

                queries.save_column_template(
                    conn,
                    source_prefix=prefix,
                    date_column=date_col,
                    check_number_column=check_col,
                    date_format=date_fmt,
                    amount_mode=amount_mode,
                    amount_column=amt_col,
                    debit_column=debit_col,
                    credit_column=credit_col,
                    description_column=desc_col,
                    category_raw_column=cat_raw_col,
                    sign_convention=sign_conv,
                    card_column=card_col,
                )

                # If card column, set up sub-sources
                if card_col:
                    _setup_card_subsources(conn, filepath, prefix, card_col)

                st.success(f"Template saved for {prefix}")
                r.needs_template = False
                r.template = queries.get_column_template(conn, prefix)

        if r.needs_template:
            all_saved = False

    if all_saved:
        if st.button("Proceed to Ingestion →"):
            _run_ingestion(conn, results)


def _setup_card_subsources(conn, filepath, prefix, card_col):
    """Detect distinct card values and create source_file_map entries."""
    _, rows = __import__("processing.ingest", fromlist=["read_csv_rows"]).read_csv_rows(filepath)
    card_values = sorted(set(row.get(card_col, "").strip() for row in rows if row.get(card_col, "").strip()))

    for card_val in card_values:
        sub_prefix = f"{prefix}-{card_val}"
        if not queries.get_source_label(conn, sub_prefix):
            queries.upsert_source_file_map(conn, sub_prefix, f"{prefix} Card {card_val}")
            st.info(f"Created source: {sub_prefix} → {prefix} Card {card_val}")


# ---------------------------------------------------------------------------
# Step 3 — Payee Normalization
# ---------------------------------------------------------------------------

def _step3_normalization(conn):
    st.subheader("Step 3 — Payee Normalization")

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
        st.success("All payees recognized. No new rules needed.")
        if st.button("Proceed to Category Assignment →"):
            st.session_state.process_step = 4
            st.session_state.pop("norm_run", None)
            st.session_state.pop("norm_seeded", None)
            st.rerun()
        return

    st.warning(f"**{len(unmatched)}** unrecognized description(s) remaining")

    # --- Top half: unmatched descriptions, sorted alphabetically, VIA stripped ---
    st.markdown("#### Unmatched Descriptions")
    st.caption("VIA prefixes stripped. Sorted alphabetically. Add rules below to clear items from this list.")

    display_data = []
    for item in unmatched:
        display_data.append({
            "Description (cleaned)": item["cleaned_desc"],
            "Via": item["via"] or "",
            "Amount": float(item["amount"]),
            "Date": item["date"],
            "Source": item["source"],
        })

    df = pd.DataFrame(display_data)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=min(400, 35 * len(display_data) + 38),
    )

    # --- Bottom half: add a normalization rule ---
    st.markdown("#### Add Normalization Rule")
    st.caption(
        "Enter a search pattern (case-insensitive substring match against the raw description) "
        "and the normalized payee name. Hit **Apply Rule** to match all transactions "
        "containing that pattern — matched items will disappear from the list above."
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
        # Re-run matching to refresh the list
        st.session_state.pop("norm_run", None)
        st.rerun()

    st.divider()

    # Proceed when ready (even if some remain — user may want to handle in category step)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Re-scan Unmatched"):
            st.session_state.pop("norm_run", None)
            st.rerun()
    with col2:
        if st.button("Proceed to Category Assignment →"):
            st.session_state.process_step = 4
            st.session_state.pop("norm_run", None)
            st.session_state.pop("norm_seeded", None)
            st.rerun()


# ---------------------------------------------------------------------------
# Step 4 — Category Assignment
# ---------------------------------------------------------------------------

def _step4_categorization(conn):
    st.subheader("Step 4 — Category Assignment")

    # Auto-categorize
    if "cat_auto_done" not in st.session_state:
        auto_count = auto_categorize(conn)
        st.session_state.cat_auto_done = True
        if auto_count:
            st.info(f"Auto-categorized {auto_count} transaction(s) from payee metadata.")

    pending = queries.get_pending_transactions(conn)
    if not pending:
        st.success("No pending transactions. All done!")
        if st.button("Proceed to Reports →"):
            st.session_state.process_step = 5
            st.session_state.pop("cat_auto_done", None)
            st.rerun()
        return

    # Get dropdown options
    category_names = queries.get_category_names(conn)
    all_categories = queries.get_categories(conn)
    subcats_map = {}
    for cat in all_categories:
        c = cat["category"]
        sc = cat["subcategory"]
        if c not in subcats_map:
            subcats_map[c] = []
        if sc:
            subcats_map[c].append(sc)

    # Build dataframe
    data = []
    for t in pending:
        data.append({
            "id": t["id"],
            "Date": t["date"],
            "Source": t["source"],
            "Payee": t["payee"] or "",
            "Via": t["via"] or "",
            "Amount": float(t["amount"]),
            "Category": t["category"] or "",
            "Subcategory": t["subcategory"] or "",
            "Tax Flags": t["tax_flags"] or "",
            "Payor": t["payor"] or "",
            "Note": t["note"] or "",
            "Status": t["status"],
        })

    df = pd.DataFrame(data)

    payor_options = ["", "David", "Debra", "Both", "Unknown"]

    edited = st.data_editor(
        df[["Date", "Source", "Payee", "Via", "Amount", "Category", "Subcategory",
            "Tax Flags", "Payor", "Note"]],
        column_config={
            "Date": st.column_config.TextColumn(disabled=True),
            "Source": st.column_config.TextColumn(disabled=True),
            "Payee": st.column_config.TextColumn(disabled=True),
            "Via": st.column_config.TextColumn(disabled=True),
            "Amount": st.column_config.NumberColumn(disabled=True, format="$%.2f"),
            "Category": st.column_config.SelectboxColumn(options=category_names),
            "Subcategory": st.column_config.TextColumn(),
            "Tax Flags": st.column_config.TextColumn(),
            "Payor": st.column_config.SelectboxColumn(options=payor_options),
            "Note": st.column_config.TextColumn(),
        },
        use_container_width=True,
        num_rows="fixed",
        height=600,
    )

    # Summary
    confirmed = (edited["Category"] != "").sum()
    unresolved = len(edited) - confirmed
    st.caption(f"{confirmed} confirmed, {unresolved} still need a category")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Save & Continue"):
            _save_categories(conn, df, edited)
            st.session_state.pop("cat_auto_done", None)
            remaining = queries.get_pending_count(conn)
            if remaining == 0:
                st.session_state.process_step = 5
            st.rerun()

    with col2:
        if st.button("Pause for Now"):
            _save_categories(conn, df, edited)
            st.session_state.pop("cat_auto_done", None)
            st.session_state.process_step = 1
            st.rerun()

    with col3:
        if st.button("Print Pending Items"):
            out = generate_pending_items_pdf(conn)
            if out:
                st.success(f"Saved: {out}")


def _save_categories(conn, original_df, edited_df):
    """Save category edits and update payee metadata."""
    edits = []
    payee_defaults = []
    for idx in range(len(original_df)):
        txn_id = original_df.iloc[idx]["id"]
        cat = edited_df.iloc[idx]["Category"]
        subcat = edited_df.iloc[idx]["Subcategory"]
        tax_flags = edited_df.iloc[idx]["Tax Flags"]
        payor = edited_df.iloc[idx]["Payor"]
        note = edited_df.iloc[idx]["Note"]

        edit = {"id": txn_id}
        if cat:
            edit["category"] = cat
            edit["subcategory"] = subcat if subcat else None
            edit["tax_flags"] = tax_flags if tax_flags else None
            edit["payor"] = payor if payor else None
            edit["note"] = note if note else None
            edit["status"] = "confirmed"
        else:
            edit["status"] = "needs_review"

        edits.append(edit)

        # Save as payee default if payee exists and category was set
        payee = original_df.iloc[idx].get("Payee")
        if cat and payee:
            payee_defaults.append({
                "normalized_name": payee,
                "category": cat,
                "subcategory": subcat if subcat else None,
                "tax_flags": tax_flags if tax_flags else None,
                "payor": payor if payor else None,
                "note": note if note else None,
            })

    apply_category_edits(conn, edits)
    if payee_defaults:
        save_payee_defaults(conn, payee_defaults)


# ---------------------------------------------------------------------------
# Step 5 — Generate Reports
# ---------------------------------------------------------------------------

def _step5_reports(conn):
    st.subheader("Step 5 — Generate Reports")

    pending_count = queries.get_pending_count(conn)
    if pending_count > 0:
        st.warning(
            f"{pending_count} transaction(s) still pending. "
            "Resolve them first or generate reports with gaps."
        )

    period_start = st.session_state.get("period_start", date.today().replace(day=1))
    period_end = st.session_state.get("period_end", date.today())

    col1, col2 = st.columns(2)
    start = col1.date_input("Report start date", value=period_start, key="rpt_start")
    end = col2.date_input("Report end date", value=period_end, key="rpt_end")

    start_str = start.isoformat()
    end_str = end.isoformat()

    st.markdown("**Select reports to generate:**")
    do_monthly = st.checkbox("Monthly PDF Report (Category Summary, Payee Summary, Detail, Tax Items)", value=True)
    do_pending = st.checkbox("Pending Items", value=False, disabled=(pending_count == 0))
    do_excel = st.checkbox("Excel Export", value=True)

    if st.button("Generate Selected Reports"):
        generated = []
        if do_monthly:
            path = generate_monthly_pdf(conn, start_str, end_str)
            generated.append(path)
        if do_pending and pending_count > 0:
            path = generate_pending_items_pdf(conn)
            if path:
                generated.append(path)
        if do_excel:
            path = generate_excel_export(conn, start_str, end_str)
            generated.append(path)

        if generated:
            st.success("Reports generated:")
            for p in generated:
                st.write(f"  📄 `{p}`")
        else:
            st.info("No reports selected.")
