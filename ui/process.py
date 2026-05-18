"""Ingest — file validation, template setup, CSV ingestion, and enrichment."""

import streamlit as st
import pandas as pd
from datetime import date, datetime
from pathlib import Path

from db import queries
from processing.ingest import (
    INPUT_DIR, validate_all_files, read_csv_headers, parse_filename,
    ingest_file, parse_file_with_template, FileValidationResult,
    check_cross_source_completeness,
)
from processing.enrich import (
    PENDING_DIR, route_input_files, scan_pending, commit_pending,
    list_pending_status, is_enricher_kind, enricher_account_types,
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

    # Enrichment section — always visible at the bottom of the Ingest page.
    # Shows pending/ contents, lets the user preview and apply matches.
    st.divider()
    _enrichment_section(conn)


# ---------------------------------------------------------------------------
# Step 1 — File Validation
# ---------------------------------------------------------------------------

def _step1_validation(conn):
    st.subheader("Step 1 — File Validation")

    with st.expander("Instructions"):
        st.markdown(
            "1. Place your CSV files in the **input/** folder.\n"
            "2. Filename format: **`<account-prefix> MM-DD-YYYY to MM-DD-YYYY.csv`**\n"
            "   - Bank/CC examples: `Chase5616 01-01-2026 to 02-28-2026.csv`\n"
            "   - Enrichment examples: `Venmo 01-01-2026 to 01-31-2026.csv`, "
            "`Amazon 01-01-2026 to 01-31-2026.csv`\n"
            "3. Click **Check Input Files** to scan, auto-route enrichment files to "
            "`pending/`, and warn about anything new or missing.\n"
            "4. Bank/CC files proceed through validation → preview → commit.\n"
            "5. Enrichment files are reviewed and applied at the bottom of this page."
        )

    # --- Phase 1: scan + classify + route ---
    if st.button("Check Input Files"):
        _scan_and_route(conn)

    # If unknown prefixes were detected, force the continuity prompt before
    # anything else can proceed.
    unknown = st.session_state.get("unknown_prefixes", [])
    if unknown:
        _continuity_prompt(conn, unknown)
        return

    # Show cross-source missing-account warnings if any.
    missing = st.session_state.get("missing_sources", [])
    if missing:
        _missing_source_prompt(conn, missing)
        return

    scanned = st.session_state.get("scanned_files")
    if not scanned:
        return

    routed_summary = st.session_state.get("routing_summary")
    if routed_summary:
        _show_routing_summary(routed_summary)

    valid_count = sum(1 for _, p in scanned if p is not None)
    if valid_count == 0:
        st.warning("No ingest-type files to process. Enrichment-only? See section below.")
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
    total_skipped = 0
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
            fresh, skipped = ingest_file(conn, filepath, template)
            ingested += 1
            total_txns += len(fresh)
            total_skipped += skipped
        except Exception as e:
            st.error(f"{filepath.name}: {e}")
            return

    st.session_state.pop("ingestion_preview", None)
    st.session_state.pop("validation_results", None)
    st.session_state.pop("scanned_files", None)
    st.session_state.pop("routing_summary", None)
    msg = f"Ingested {ingested} file(s), {total_txns} new transaction(s)."
    if total_skipped:
        msg += f" Skipped {total_skipped} duplicate row(s) (overlap from prior periods)."
    msg += " Scroll down to apply pending enrichments, then go to **Normalize & Categorize**."
    st.success(msg)


# ---------------------------------------------------------------------------
# Scan + route + continuity + missing-source helpers
# ---------------------------------------------------------------------------

def _scan_and_route(conn):
    """Scan input/, identify unknown prefixes, route enrichment files, and run
    the cross-source completeness check. Populates session state for the
    downstream UI to consume.
    """
    INPUT_DIR.mkdir(exist_ok=True)
    csv_files = sorted(f for f in INPUT_DIR.iterdir() if f.suffix.lower() == ".csv")
    if not csv_files:
        st.warning("No CSV files found in the input folder.")
        return

    # First pass: identify any unknown prefixes BEFORE routing, so the user
    # can resolve them via the continuity prompt.
    unknown = []
    for fp in csv_files:
        parsed = parse_filename(fp.name)
        if not parsed:
            continue
        prefix = parsed["prefix"]
        if queries.get_account_type(conn, prefix) is None:
            unknown.append((fp.name, prefix))

    if unknown:
        # Deduplicate by prefix — multiple files might share the same new prefix.
        seen = set()
        deduped = []
        for fname, prefix in unknown:
            if prefix not in seen:
                seen.add(prefix)
                deduped.append((fname, prefix))
        st.session_state.unknown_prefixes = deduped
        st.session_state.scanned_files = None
        st.session_state.routing_summary = None
        st.session_state.missing_sources = None
        st.rerun()
        return

    # No unknown prefixes — route enrich-type files to pending/.
    routing = route_input_files(conn)
    st.session_state.routing_summary = routing

    # Re-scan input/ after routing (enrichment files are now gone)
    csv_files = sorted(f for f in INPUT_DIR.iterdir() if f.suffix.lower() == ".csv")
    parsed_files = []
    all_starts, all_ends = [], []
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

    # Cross-source completeness check
    current_prefixes = set()
    for _, p in parsed_files:
        if p:
            current_prefixes.add(p["prefix"])
    # Include prefixes of enrichment files we just moved to pending/
    for fname in routing["enrich_moved"]:
        p = parse_filename(fname)
        if p:
            current_prefixes.add(p["prefix"])
    # Plus enrichment files already sitting in pending/ from prior periods
    for status in list_pending_status(conn):
        current_prefixes.add(status["prefix"])
    missing = check_cross_source_completeness(conn, current_prefixes)
    if missing:
        st.session_state.missing_sources = missing
    else:
        st.session_state.missing_sources = []

    st.rerun()


def _show_routing_summary(routing: dict):
    """Render the routing summary returned by route_input_files()."""
    parts = []
    if routing["ingest"]:
        parts.append(f"**{len(routing['ingest'])}** ingest-type file(s) staying in input/")
    if routing["enrich_moved"]:
        parts.append(f"**{len(routing['enrich_moved'])}** enrichment file(s) routed to pending/")
    if routing["unparseable"]:
        parts.append(f"**{len(routing['unparseable'])}** unparseable filename(s)")
    if parts:
        st.info(" · ".join(parts))

    if routing["enrich_moved"]:
        with st.expander(f"Enrichment files routed to pending/ ({len(routing['enrich_moved'])})"):
            for fname in routing["enrich_moved"]:
                st.write(f"  - `{fname}`")
    if routing["ingest"]:
        with st.expander(f"Ingest-type files in input/ ({len(routing['ingest'])})"):
            for fname in routing["ingest"]:
                st.write(f"  - `{fname}`")
    if routing["unparseable"]:
        st.error("These files don't match the naming pattern:")
        for fname in routing["unparseable"]:
            st.write(f"  - `{fname}`")


def _continuity_prompt(conn, unknown: list[tuple[str, str]]):
    """Render the account-continuity prompt for unknown prefixes.

    Each unknown prefix is shown with options:
      - Brand-new account: pick account_type + nickname
      - Replacement for an existing prefix: pick which one to link

    On save, source_file_map gets a new entry (and replaced_by_prefix link
    if applicable). After all unknowns are resolved, scan + route re-runs.
    """
    st.warning(
        f"**{len(unknown)} new account prefix(es) detected.** "
        "Each one is either a brand-new account or a replacement for an existing account."
    )

    existing_sources = queries.get_active_sources(conn)
    existing_prefixes = [r["source_prefix"] for r in existing_sources]
    enricher_types = sorted(enricher_account_types())
    all_types = ["checking", "credit_card"] + enricher_types

    for fname, prefix in unknown:
        st.markdown(f"### `{prefix}` (first seen in `{fname}`)")
        with st.form(f"continuity_{prefix}"):
            mode = st.radio(
                "What is this account?",
                ["Brand-new account", "Replacement for an existing account"],
                key=f"mode_{prefix}", horizontal=True,
            )
            acct_type = st.selectbox(
                "Account type", all_types, key=f"acct_{prefix}",
                help=(
                    "checking / credit_card: file goes through normal ingest "
                    "and needs a column template. "
                    "venmo_detail / amazon_detail: file is an enrichment file."
                ),
            )
            nickname = st.text_input("Nickname (optional)", key=f"nick_{prefix}")
            replaced = None
            if mode.startswith("Replacement"):
                replaced = st.selectbox(
                    "Which existing prefix does this replace?",
                    existing_prefixes, key=f"replaces_{prefix}",
                )
            saved = st.form_submit_button("Save")
            if saved:
                queries.upsert_source_file_map(
                    conn, prefix, prefix, nickname or None, acct_type
                )
                if replaced:
                    queries.set_replaced_by_prefix(conn, replaced, prefix)
                st.success(f"Saved `{prefix}` as `{acct_type}`")
                # Drop this prefix from the unknown list
                st.session_state.unknown_prefixes = [
                    (f, p) for (f, p) in st.session_state.unknown_prefixes
                    if p != prefix
                ]
                # If all resolved, kick off a re-scan
                if not st.session_state.unknown_prefixes:
                    st.session_state.pop("unknown_prefixes", None)
                    _scan_and_route(conn)
                st.rerun()


def _missing_source_prompt(conn, missing: list[dict]):
    """Render the 4-option prompt for sources that were present last period
    but absent from the current input.
    """
    st.error(
        f"**{len(missing)} known account(s) appear to be missing from this input batch.**"
    )

    existing_sources = queries.get_active_sources(conn)
    existing_prefixes = [r["source_prefix"] for r in existing_sources]

    for entry in missing:
        prefix = entry["prefix"]
        nick = entry["nickname"] or ""
        st.markdown(f"### `{prefix}` ({nick}) — last seen through {entry['last_seen']}")
        with st.form(f"missing_{prefix}"):
            choice = st.radio(
                "How do you want to handle this?",
                [
                    "Continue anyway (just remind me later)",
                    "Stop processing so I can add the missing data",
                    "This account has been discontinued",
                    "This account has been replaced by a new account",
                ],
                key=f"missing_choice_{prefix}",
            )
            replacement = None
            if choice.startswith("This account has been replaced"):
                replacement = st.text_input(
                    "New account prefix (e.g. Chase12345)",
                    key=f"missing_repl_{prefix}",
                )
            submitted = st.form_submit_button("Confirm")
            if submitted:
                if choice.startswith("Continue"):
                    pass
                elif choice.startswith("Stop"):
                    st.warning("Processing stopped. Re-run after adding the missing file.")
                    st.session_state.missing_sources = []
                    return
                elif choice.startswith("This account has been discontinued"):
                    queries.mark_account_discontinued(conn, prefix)
                elif choice.startswith("This account has been replaced"):
                    if replacement and replacement.strip():
                        repl = replacement.strip()
                        # Create the replacement if it doesn't exist yet
                        if queries.get_source_row(conn, repl) is None:
                            queries.upsert_source_file_map(
                                conn, repl, repl, None, None
                            )
                        queries.set_replaced_by_prefix(conn, prefix, repl)
                # Drop this prefix from missing list
                st.session_state.missing_sources = [
                    m for m in st.session_state.missing_sources
                    if m["prefix"] != prefix
                ]
                if not st.session_state.missing_sources:
                    st.session_state.missing_sources = []
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


# ---------------------------------------------------------------------------
# Enrichment section (always visible at the bottom of the Ingest page)
# ---------------------------------------------------------------------------

def _enrichment_section(conn):
    """Render the pending/-folder summary and the enrichment preview + apply."""
    st.header("Enrichment")
    st.caption(
        "Files in `pending/` add detail to Chase rows (e.g. who you sent a "
        "Venmo to, or what was in an Amazon order). They never create new "
        "transactions. Files that don't fully match this period stay in "
        "pending and retry next period automatically."
    )

    PENDING_DIR.mkdir(exist_ok=True)
    statuses = list_pending_status(conn)
    if not statuses:
        st.info("No files in pending/. Drop Venmo or Amazon CSV files in input/ "
                "and click **Check Input Files** above.")
        return

    # Pending folder status banner
    oldest_age = max(s["age_days"] for s in statuses)
    st.warning(
        f"**{len(statuses)} file(s)** in pending/ "
        f"(oldest covers period ending {oldest_age} days ago)."
    )

    # Build the preview by parsing every pending file and matching.
    if st.button("Preview Enrichment Matches", key="enrich_preview"):
        summaries = scan_pending(conn)
        st.session_state.enrich_summaries = summaries

    summaries = st.session_state.get("enrich_summaries")
    if summaries is None:
        # Show a brief table of what's in pending so the user knows what's there.
        df = pd.DataFrame(statuses)
        st.dataframe(df, use_container_width=True, hide_index=True)
        return

    _show_enrichment_preview(conn, summaries)


def _show_enrichment_preview(conn, summaries):
    """Render the preview for every file in pending/ + the Apply button."""
    if not summaries:
        st.info("No enrichment files to preview.")
        return

    total_matched = sum(len(s.matched) for s in summaries)
    total_unmatched_expected = sum(len(s.unmatched_expected) for s in summaries)
    total_unmatched_unexpected = sum(len(s.unmatched_unexpected) for s in summaries)

    summary_parts = [
        f"**{total_matched}** match(es)",
        f"**{total_unmatched_expected}** unmatched (will retry next period)",
    ]
    if total_unmatched_unexpected:
        summary_parts.append(f"**{total_unmatched_unexpected}** no-match-expected (informational)")
    st.write(" · ".join(summary_parts))

    for summary in summaries:
        fname = summary.filepath.name
        n_match = len(summary.matched)
        n_unmatched = len(summary.unmatched_expected)
        n_skip_ok = len(summary.unmatched_unexpected)
        status_icon = "✅" if summary.fully_resolved else "⏳"
        with st.expander(
            f"{status_icon} {fname} — {n_match} match · {n_unmatched} pending · {n_skip_ok} expected-no-match",
            expanded=not summary.fully_resolved,
        ):
            _render_summary_table(summary)

    col1, col2 = st.columns(2)
    if col1.button("Apply Enrichments", type="primary", key="enrich_apply"):
        result = commit_pending(conn, summaries)
        st.session_state.pop("enrich_summaries", None)
        msg = (
            f"Applied **{result['applied']}** patch(es). "
            f"Skipped **{result['skipped']}** already-overridden row(s). "
            f"Moved **{len(result['moved_to_processed'])}** file(s) to processed/; "
            f"**{len(result['kept_in_pending'])}** file(s) remain in pending/."
        )
        st.success(msg)
        st.rerun()

    if col2.button("Cancel", key="enrich_cancel"):
        st.session_state.pop("enrich_summaries", None)
        st.rerun()


def _render_summary_table(summary):
    """One file's match details: matched, unmatched (expected), no-match-expected."""
    rows_matched = []
    for prop in summary.matched:
        txn = prop.txn_row
        rec = prop.record
        rows_matched.append({
            "Chase Date": txn["date"] if txn else "",
            "Chase Description": (txn["description_raw"] if txn else "")[:60],
            "Amount": float(txn["amount"]) if txn else None,
            "→ Payee": rec.payee_hint or "",
            "→ Via": rec.via_hint or "",
            "→ Note": (rec.note_hint or "")[:60],
            "Reason": prop.reason,
        })
    if rows_matched:
        st.markdown("**Matched (will be applied):**")
        st.dataframe(pd.DataFrame(rows_matched), use_container_width=True,
                     hide_index=True, height=min(35 * (len(rows_matched) + 1) + 3, 300))

    rows_unmatched = []
    for prop in summary.unmatched_expected:
        rec = prop.record
        rows_unmatched.append({
            "Record Date": rec.match_key.get("date", ""),
            "Amount": rec.match_key.get("amount", ""),
            "Hint Payee": rec.payee_hint or "",
            "Reason": prop.reason,
        })
    if rows_unmatched:
        st.markdown("**Unmatched (file stays in pending for next period):**")
        st.dataframe(pd.DataFrame(rows_unmatched), use_container_width=True,
                     hide_index=True, height=min(35 * (len(rows_unmatched) + 1) + 3, 250))

    rows_info = []
    for prop in summary.unmatched_unexpected:
        rec = prop.record
        rows_info.append({
            "Record Date": rec.match_key.get("date", "") if rec.match_key else "",
            "Amount": rec.match_key.get("amount", "") if rec.match_key else "",
            "Hint Payee": rec.payee_hint or "",
            "Reason": prop.reason,
        })
    if rows_info:
        st.markdown("**No Chase match expected (informational only):**")
        st.dataframe(pd.DataFrame(rows_info), use_container_width=True,
                     hide_index=True, height=min(35 * (len(rows_info) + 1) + 3, 200))
