"""Diagnostics — the single "what needs my attention?" page.

Lists every transaction that is either not yet confirmed OR is missing a
required subcategory, along with a plain-English 'Issue' column so the
user knows what's wrong without having to inspect each row. Read-only:
edits happen on Maintenance -> Edit Transactions.
"""

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from db import queries
from ui._amount_style import (
    amount_cell_style, amount_value_formatter, case_insensitive_comparator,
)


def _issues_for_row(conn, r) -> list[str]:
    """Return a short list of problems with a transaction row, for display."""
    issues: list[str] = []
    st_val = r["status"]
    if st_val == "pending":
        issues.append("status = pending (not yet confirmed)")
    elif st_val == "needs_review":
        issues.append("status = needs_review (flagged for follow-up)")
    if not r["category"]:
        issues.append("no category")
    else:
        if (not r["subcategory"]) and queries.category_requires_subcategory(conn, r["category"]):
            issues.append(f"missing subcategory (category '{r['category']}' has real sub-choices)")
    if not r["payee"]:
        issues.append("no payee")
    return issues


def diagnostics_page(conn):
    st.title("Diagnostics — Unresolved Transactions")
    st.caption(
        "Every row that is either not yet confirmed OR missing a required "
        "subcategory. The **Issue** column explains what's wrong so you know "
        "where to start. This page is read-only — edits happen on "
        "**Maintenance → Edit Transactions** (search by the row's `id` to "
        "find and fix it)."
    )

    # Union of all unresolved-status rows AND confirmed-but-missing-subcat rows,
    # split parents excluded.
    NOT_PARENT = ("id NOT IN (SELECT split_parent_id FROM transactions "
                  "WHERE split_parent_id IS NOT NULL)")
    cats_with_subs = [row["category"] for row in conn.execute(
        "SELECT DISTINCT category FROM categories "
        "WHERE subcategory IS NOT NULL AND subcategory != ''"
    ).fetchall()]

    parts = [f"status IN ('pending', 'needs_review')"]
    params: list = []
    if cats_with_subs:
        placeholders = ",".join("?" * len(cats_with_subs))
        parts.append(
            f"(category IN ({placeholders}) AND "
            f"(subcategory IS NULL OR subcategory = ''))"
        )
        params.extend(cats_with_subs)
    where = " OR ".join(f"({p})" for p in parts)
    rows = conn.execute(
        f"SELECT id, date, amount, source, payee, category, subcategory, "
        f"status, description_raw, note "
        f"FROM transactions "
        f"WHERE ({where}) AND {NOT_PARENT} "
        f"ORDER BY status, date",
        tuple(params),
    ).fetchall()

    if not rows:
        st.success(
            "✓ **The ledger is fully clean.** No unresolved status, no "
            "missing subcategories. Nothing here to work on."
        )
        return

    # Summary metrics
    st.warning(f"⚠ **{len(rows)}** row(s) need attention.")
    abs_total = sum(abs(float(r["amount"])) for r in rows)
    c1, c2, c3 = st.columns(3)
    c1.metric("Rows", len(rows))
    c2.metric("Absolute total", f"${abs_total:,.2f}")
    c3.metric("Distinct sources", len({r["source"] for r in rows}))

    # Build display dataframe
    data = []
    for r in rows:
        issues = _issues_for_row(conn, r)
        data.append({
            "id": r["id"],
            "Date": r["date"],
            "Amount": float(r["amount"]),
            "Source": r["source"],
            "Payee": r["payee"] or "(none)",
            "Category": r["category"] or "(none)",
            "Subcategory": r["subcategory"] or "(none)",
            "Status": r["status"],
            "Issue": " · ".join(issues) if issues else "?",
            "Description": r["description_raw"] or "",
            "Note": r["note"] or "",
        })
    df = pd.DataFrame(data)

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, sortable=True, editable=False, filter=True,
                                comparator=case_insensitive_comparator())
    gb.configure_column("id", width=70)
    gb.configure_column("Date", width=105)
    gb.configure_column("Amount", width=115, type=["numericColumn"],
                        valueFormatter=amount_value_formatter(),
                        cellStyle=amount_cell_style())
    gb.configure_column("Source", width=120)
    gb.configure_column("Payee", width=180)
    gb.configure_column("Category", width=140)
    gb.configure_column("Subcategory", width=140)
    gb.configure_column("Status", width=120)
    gb.configure_column("Issue", width=350)   # widest — the actual takeaway
    gb.configure_column("Description", width=280)
    gb.configure_column("Note", width=200)
    gb.configure_grid_options(headerHeight=32)

    AgGrid(
        df, gridOptions=gb.build(),
        update_mode=GridUpdateMode.NO_UPDATE,     # read-only
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=False,
        height=560,
        theme="alpine-dark",
    )

    st.caption(
        "**How to fix a row:** copy its `id`, open "
        "**Maintenance → Edit Transactions**, paste the id into the search "
        "box (once numeric search lands — Next Steps #12), or filter by "
        "Source + Date. Set the missing field(s) and click Save."
    )
