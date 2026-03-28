"""Ingest — file validation, template setup, and CSV ingestion."""

import streamlit as st
import pandas as pd
from datetime import date, datetime
from pathlib import Path

from db import queries
from processing.ingest import (
    INPUT_DIR, validate_all_files, read_csv_headers, parse_filename,
    ingest_file, parse_file_with_template, FileValidationResult,
)

STEPS = ["File Validation", "Template Setup"]


def process_page(conn):
    st.title("Ingest")

    # Initialize step state
    if "process_step" not in st.session_state:
        st.session_state.process_step = 1

    step = st.session_state.process_step

    st.progress(step / len(STEPS))
    st.caption(f"Step {step} of {len(STEPS)}: {STEPS[step - 1]}")

    if step == 1:
        _step1_validation(conn)
    elif step == 2:
        _step2_template(conn)


# ---------------------------------------------------------------------------
# Step 1 — File Validation
# ---------------------------------------------------------------------------

def _step1_validation(conn):
    st.subheader("Step 1 — File Validation")

    with st.expander("Instructions"):
        st.markdown(
            "1. Place your CSV bank/credit card export files in the **input/** folder.\n"
            "2. Files must be named: **`<account-prefix> MM-DD-YYYY to MM-DD-YYYY.csv`**\n"
            "   - Example: `Chase5616 01-01-2026 to 02-28-2026.csv`\n"
            "   - Extra spaces between the prefix and dates are OK.\n"
            "3. The prefix must match a known account, or a new one will be set up.\n"
            "4. Click **Check Input Files** below to scan the input folder."
        )

    # --- Phase 1: scan for files ---
    if st.button("Check Input Files"):
        INPUT_DIR.mkdir(exist_ok=True)
        csv_files = sorted(f for f in INPUT_DIR.iterdir() if f.suffix.lower() == ".csv")

        if not csv_files:
            st.warning("No CSV files found in the input folder.")
            return

        parsed_files = []
        all_starts = []
        all_ends = []
        for fp in csv_files:
            parsed = parse_filename(fp.name)
            parsed_files.append((fp.name, parsed))
            if parsed:
                all_starts.append(parsed["start_date"])
                all_ends.append(parsed["end_date"])

        st.session_state.scanned_files = parsed_files

        if all_starts and all_ends:
            st.session_state.period_start = min(all_starts)
            st.session_state.period_end = max(all_ends)

    scanned = st.session_state.get("scanned_files")
    if not scanned:
        return

    valid_count = sum(1 for _, p in scanned if p is not None)
    st.success(f"Found **{valid_count}** valid input file(s):")
    for fname, parsed in scanned:
        if parsed:
            st.write(f"  - `{fname}` — {parsed['prefix']}, {parsed['start_date']} to {parsed['end_date']}")
        else:
            st.error(f"  - `{fname}` — does not match expected filename format")

    if valid_count == 0:
        return

    # --- Phase 2: validate with dates ---
    st.divider()
    col1, col2 = st.columns(2)
    period_start = col1.date_input(
        "Processing period start",
        value=st.session_state.get("period_start", date.today().replace(day=1)),
    )
    period_end = col2.date_input(
        "Processing period end",
        value=st.session_state.get("period_end", date.today()),
    )

    if st.button("Validate Files"):
        results = validate_all_files(conn, period_start, period_end)
        st.session_state.validation_results = results
        st.session_state.period_start = period_start
        st.session_state.period_end = period_end

    results = st.session_state.get("validation_results", [])
    if not results:
        return

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

    # Proceed button
    all_ok = all(r.ok or r.needs_template for r in results) and error_count == 0
    if all_ok and results:
        if needs_template:
            if st.button("Proceed to Template Setup →"):
                st.session_state.process_step = 2
                st.rerun()
        else:
            if st.button("Preview Ingestion →"):
                period_start = st.session_state.get("period_start")
                period_end = st.session_state.get("period_end")
                fresh_results = validate_all_files(conn, period_start, period_end)
                st.session_state.validation_results = fresh_results
                _preview_ingestion(conn, fresh_results)

    # Show preview if already generated
    if "ingestion_preview" in st.session_state:
        _show_ingestion_preview(conn)


def _preview_ingestion(conn, results: list[FileValidationResult]):
    """Parse all files without committing and store preview for user review."""
    all_parsed = []
    for r in results:
        if not r.parsed or r.errors:
            continue
        filepath = INPUT_DIR / r.filename
        if not filepath.exists():
            continue
        template = r.template or queries.get_column_template(conn, r.parsed["prefix"])
        if not template:
            continue
        txns = parse_file_with_template(filepath, template, conn)
        all_parsed.extend(txns)

    st.session_state.ingestion_preview = all_parsed
    st.rerun()


def _show_ingestion_preview(conn):
    """Display parsed transactions for review before committing."""
    preview = st.session_state.ingestion_preview

    st.divider()
    st.subheader(f"Ingestion Preview — {len(preview)} transaction(s)")
    st.caption("Review the parsed data below. If columns look wrong, click Cancel to go back and fix templates.")

    if preview:
        display = []
        for t in preview:
            display.append({
                "Date": t["date"],
                "Source": t["source"],
                "Description": t["description_raw"],
                "Amount": float(t["amount"]),
                "Check #": t["check_number"] or "",
                "Category (raw)": t["category_raw"] or "",
                "Order Ref": t["order_ref"] or "",
            })
        df = pd.DataFrame(display)
        st.dataframe(df, use_container_width=True, height=400)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Commit Ingestion", type="primary"):
            _commit_ingestion(conn)
    with col2:
        if st.button("Cancel"):
            st.session_state.pop("ingestion_preview", None)
            st.rerun()


def _commit_ingestion(conn):
    """Actually ingest all files after user has reviewed the preview."""
    results = st.session_state.get("validation_results", [])
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

    st.session_state.pop("ingestion_preview", None)
    st.session_state.pop("validation_results", None)
    st.session_state.pop("scanned_files", None)
    st.success(f"Ingested {ingested} file(s), {total_txns} transaction(s). Go to **Normalize & Categorize** to continue.")


# ---------------------------------------------------------------------------
# Step 2 — Template Setup
# ---------------------------------------------------------------------------

def _step2_template(conn):
    st.subheader("Step 2 — Template Setup (new accounts)")

    results = st.session_state.get("validation_results", [])
    new_accounts = [r for r in results if r.needs_template and r.parsed]

    if not new_accounts:
        st.info("No new accounts to set up. Moving on.")
        st.session_state.process_step = 1
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

        # Preview all rows, scrollable
        try:
            from processing.ingest import read_csv_rows
            _, all_rows = read_csv_rows(filepath)
            if all_rows:
                df_preview = pd.DataFrame(all_rows, columns=headers)
                st.dataframe(df_preview, use_container_width=True, height=350)
        except Exception:
            pass

        with st.form(f"template_{prefix}"):
            source_label = st.text_input("Account name", value=prefix, disabled=True, key=f"label_{prefix}")
            nickname = st.text_input(
                "Nickname (e.g. Chase Sapphire Preferred)",
                value="", key=f"nick_{prefix}",
            )
            acct_type = st.selectbox(
                "Account type",
                ["checking", "credit_card"],
                key=f"accttype_{prefix}",
            )

            # Auto-select Post Date if available
            headers_lower = [h.lower() for h in headers]
            default_date_idx = 0
            if "post date" in headers_lower:
                default_date_idx = headers_lower.index("post date")
            elif "posting date" in headers_lower:
                default_date_idx = headers_lower.index("posting date")
            st.caption(
                "**Use Post Date (not Transaction Date).** Abacus uses post date "
                "for all date filtering and reports, consistent with how banks "
                "filter downloads."
            )
            date_col = st.selectbox("Post date column", headers,
                                    index=default_date_idx, key=f"date_{prefix}")
            if date_col.lower() in ("transaction date", "trans date", "txn date"):
                st.warning(
                    "You selected **Transaction Date** — are you sure? "
                    "Post Date is strongly recommended for consistency."
                )
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
                queries.upsert_source_file_map(conn, prefix, prefix, nickname or None, acct_type)

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

                if card_col:
                    _setup_card_subsources(conn, filepath, prefix, card_col, acct_type)

                st.success(f"Template saved for {prefix}")
                r.needs_template = False
                r.template = queries.get_column_template(conn, prefix)

        if r.needs_template:
            all_saved = False

    if all_saved:
        if st.button("Proceed to Ingestion →"):
            period_start = st.session_state.get("period_start")
            period_end = st.session_state.get("period_end")
            fresh_results = validate_all_files(conn, period_start, period_end)
            st.session_state.validation_results = fresh_results
            st.session_state.process_step = 1
            st.rerun()


def _setup_card_subsources(conn, filepath, prefix, card_col, account_type=None):
    """Detect distinct card values and create source_file_map entries."""
    from processing.ingest import read_csv_rows
    _, rows = read_csv_rows(filepath)
    card_values = sorted(set(row.get(card_col, "").strip() for row in rows if row.get(card_col, "").strip()))

    for card_val in card_values:
        sub_prefix = f"{prefix}-{card_val}"
        if not queries.get_source_label(conn, sub_prefix):
            queries.upsert_source_file_map(conn, sub_prefix, sub_prefix, f"{prefix} Card {card_val}", account_type)
            st.info(f"Created source: {sub_prefix} → {prefix} Card {card_val}")
