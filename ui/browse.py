"""Browse / Search — view and filter all transactions."""

import io
import streamlit as st
import pandas as pd
from datetime import date, timedelta

import openpyxl
from openpyxl.utils import get_column_letter

from db import queries


# Tolerance applied when the user gives a single amount (only "From" filled)
# so a search for 4600.65 finds 4600.00 and 4601.00 too. Wide enough for
# rounding drift, narrow enough to still isolate the transaction.
AMOUNT_SINGLE_TOL = 1.00


def _months_ago(d: date, n: int) -> date:
    """Return the calendar date n months before d, clamped to the shorter
    month when the source day doesn't exist there (Feb 30 -> Feb 28/29)."""
    y = d.year
    m = d.month - n
    while m <= 0:
        m += 12
        y -= 1
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return d.replace(year=y, month=m, day=day)


def browse_page(conn):
    st.title("Browse / Search")

    # Small CSS to tighten the filter block — Streamlit's default row spacing
    # burns a lot of vertical real estate above the actual results.
    st.markdown(
        "<style>"
        "div[data-testid='stVerticalBlock']>div[data-testid='element-container']"
        "{margin-bottom:-0.35rem;}"
        "</style>",
        unsafe_allow_html=True,
    )

    # --- Row 1: text search + source + status (all on one line) ---
    col_s1, col_s2, col_src, col_status = st.columns([2, 2, 1.2, 1])
    with col_s1:
        search_payee = st.text_input("Search in Payee", placeholder="e.g. Safeway")
    with col_s2:
        search_all = st.text_input("Search Anywhere",
                                    placeholder="e.g. Medical, Venmo, Chase...")
    sources = queries.get_distinct_sources(conn)
    with col_src:
        source_filter = st.selectbox("Source", ["All"] + sources, key="browse_source")
    with col_status:
        status_filter = st.selectbox("Status",
            ["All", "pending", "confirmed", "needs_review"], key="browse_status")

    # --- Row 2: date preset + From/To + Amount From/To ---
    col_p, col_from, col_to, col_af, col_at = st.columns([1.4, 1, 1, 1, 1])

    with col_p:
        preset = st.selectbox("Date preset", [
            "All time", "This month", "Last month", "This quarter",
            "YTD", "TTM (trailing 12 mo.)", "Last year", "Custom",
        ])

    today = date.today()
    if preset == "All time":
        start_val = None
        end_val = None
    elif preset == "This month":
        start_val = today.replace(day=1)
        end_val = today
    elif preset == "Last month":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        start_val = last_month_end.replace(day=1)
        end_val = last_month_end
    elif preset == "This quarter":
        q_month = ((today.month - 1) // 3) * 3 + 1
        start_val = today.replace(month=q_month, day=1)
        end_val = today
    elif preset == "YTD":
        start_val = today.replace(month=1, day=1)
        end_val = today
    elif preset == "TTM (trailing 12 mo.)":
        # 12 calendar months back from today, day-of-month clamped for short
        # months (Feb, 30-day months).
        start_val = _months_ago(today, 12)
        end_val = today
    elif preset == "Last year":
        start_val = today.replace(year=today.year - 1, month=1, day=1)
        end_val = today.replace(year=today.year - 1, month=12, day=31)
    else:  # Custom
        start_val = today.replace(month=1, day=1)
        end_val = today

    date_disabled = (preset != "Custom" and preset != "All time")
    with col_from:
        start_date = st.date_input("From", value=start_val, key="browse_start",
                                    disabled=date_disabled)
    with col_to:
        end_date = st.date_input("To", value=end_val, key="browse_end",
                                  disabled=date_disabled)

    # Amount range search — magnitude semantics (|amount|), so a search for
    # 4600.65 finds both -4600.65 and +4600.65. If only From is filled,
    # search that amount ± AMOUNT_SINGLE_TOL (single-value convenience so
    # rounding drift doesn't bite).
    with col_af:
        amt_from = st.number_input("Amount from ($)", value=None,
                                    min_value=0.0, step=1.0, format="%.2f",
                                    key="browse_amt_from",
                                    placeholder="e.g. 4600.65")
    with col_at:
        amt_to = st.number_input("Amount to ($)", value=None,
                                  min_value=0.0, step=1.0, format="%.2f",
                                  key="browse_amt_to",
                                  placeholder="blank for ±$1")

    # Resolve the effective amount window from the two inputs.
    if amt_from is not None and amt_to is not None:
        amount_lo, amount_hi = min(amt_from, amt_to), max(amt_from, amt_to)
    elif amt_from is not None:
        amount_lo = amt_from - AMOUNT_SINGLE_TOL
        amount_hi = amt_from + AMOUNT_SINGLE_TOL
    elif amt_to is not None:
        amount_lo = amt_to - AMOUNT_SINGLE_TOL
        amount_hi = amt_to + AMOUNT_SINGLE_TOL
    else:
        amount_lo = amount_hi = None

    # --- Query ---
    # Browse mirrors the reports: split parents are excluded (their dollars and
    # categories live on the legs), so a parent's stale pre-split category can't
    # surface it in a search. Legs are shown, tagged in the Split column.
    rows = queries.get_transactions(
        conn,
        start_date=start_date.isoformat() if start_date else None,
        end_date=end_date.isoformat() if end_date else None,
        source=source_filter if source_filter != "All" else None,
        search_payee=search_payee or None,
        search=search_all or None,
        status=status_filter if status_filter != "All" else None,
        exclude_parents=True,
    )

    # Amount filter is applied here (not in the DB layer) so we don't have to
    # broaden get_transactions' signature; the row count is already narrowed
    # by everything else so the extra filter is negligible.
    if amount_lo is not None:
        rows = [r for r in rows if amount_lo <= abs(float(r["amount"])) <= amount_hi]

    if not rows:
        st.info("No transactions match the filters.")
        return

    # --- Build dataframe ---
    row_ids = []
    data = []
    for r in rows:
        row_ids.append(r["id"])
        data.append({
            "Date": r["date"],
            "Source": r["source"],
            "Split": "leg" if r["split_parent_id"] is not None else "",
            "Payee": r["payee"] or "",
            "Category": r["category"] or "",
            "Subcategory": r["subcategory"] or "",
            "Amount": float(r["amount"]) if r["amount"] else 0.0,
            "Payor": r["payor"] or "",
            "Tax Flags": r["tax_flags"] or "",
            "Description (raw)": r["description_raw"],
            "Check #": r["check_number"] or "",
            "Note": r["note"] or "",
        })

    df = pd.DataFrame(data)

    # --- Summary ---
    # Transfer rows net to ~$0 in principle (money moved between two of your
    # own accounts, both captured), so they'd double-count on Money Out /
    # Money In and inflate the gap. Exclude them from the primary metrics and
    # show their net separately as a data-quality signal — if the sum drifts
    # far from zero, one leg of a transfer may be missing or miscategorized.
    non_xfer = df[df["Category"] != "Transfer"]
    xfer = df[df["Category"] == "Transfer"]
    total_out = non_xfer[non_xfer["Amount"] < 0]["Amount"].sum()
    total_in = non_xfer[non_xfer["Amount"] > 0]["Amount"].sum()
    xfer_net = float(xfer["Amount"].sum()) if not xfer.empty else 0.0

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Transactions", f"{len(df)}  ({len(xfer)} xfer)")
    col_b.metric("Money Out (excl. xfer)", f"-${abs(total_out):,.2f}")
    col_c.metric("Money In (excl. xfer)", f"${total_in:,.2f}")
    # Net Transfer should be ~$0 if both legs of every transfer are captured
    # and correctly labeled. A large positive or negative here is a
    # data-quality signal, not real spending.
    xfer_help = ("Should be near $0. A large value means one leg of some "
                 "transfer isn't captured or is labeled inconsistently — "
                 "worth investigating on Diagnostics.")
    col_d.metric("Net Transfer", f"${xfer_net:,.2f}", help=xfer_help)

    # --- Export buttons ---
    col_x, col_y, _ = st.columns([1, 1, 4])
    with col_x:
        excel_buf = _df_to_excel(df)
        st.download_button(
            "Download Excel",
            data=excel_buf,
            file_name="abacus_browse.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col_y:
        csv_data = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download CSV (Print)",
            data=csv_data,
            file_name="abacus_browse.csv",
            mime="text/csv",
        )

    # --- Sortable, scrollable table with row selection ---
    # Apply consistent UI convention: credits in green, signed currency format.
    from ui._amount_style import styler_for_amount_column
    selection = st.dataframe(
        styler_for_amount_column(df, "Amount"),
        use_container_width=True,
        hide_index=True,
        height=500,
        on_select="rerun",
        selection_mode="single-row",
        key="browse_table",
    )

    # --- Edit selected transaction ---
    selected_rows = selection.get("selection", {}).get("rows", [])
    if selected_rows:
        idx = selected_rows[0]
        if idx < len(row_ids):
            txn_id = row_ids[idx]
            sel = data[idx]
            st.divider()
            st.markdown(f"**{sel['Date']}** | {sel['Payee']} | {sel['Source']} | {_fmt_browse_amt(sel['Amount'])}")

            col1, col2 = st.columns([3, 1])
            new_note = col1.text_input("Note", value=sel["Note"], key=f"browse_note_{txn_id}")
            if col2.button("Save", key=f"browse_save_{txn_id}"):
                queries.update_transaction(conn, txn_id, note=new_note if new_note else None)
                st.success("Note saved.")
                st.rerun()


def _fmt_browse_amt(val):
    if val > 0:
        # Credit — render in green via Streamlit markdown color span
        return f":green[\\${val:,.2f}]"
    if val < 0:
        return f"-${abs(val):,.2f}"
    return f"${val:,.2f}"


def _df_to_excel(df: pd.DataFrame) -> bytes:
    """Convert a DataFrame to an Excel file in memory."""
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Browse Results"

    # Header
    for col_idx, col_name in enumerate(df.columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = openpyxl.styles.Font(bold=True)

    # Data
    for row_idx, row in enumerate(df.itertuples(index=False), 2):
        for col_idx, val in enumerate(row, 1):
            ws.cell(row=row_idx, column=col_idx, value=val)

    # Auto-filter and column widths
    ws.auto_filter.ref = f"A1:{get_column_letter(len(df.columns))}{len(df) + 1}"
    for col_idx, col_name in enumerate(df.columns, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = max(len(str(col_name)) + 4, 12)

    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()
