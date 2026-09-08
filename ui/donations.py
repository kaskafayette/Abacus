"""Non-cash Donations — a small, standalone log of charitable donations of
goods, vehicles, stock, etc. Not part of the bank-transaction stream; a
future report section will pull from `non_cash_donations` at year-end."""

import streamlit as st
import pandas as pd
from datetime import date
from decimal import Decimal
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from db import queries
from ui._amount_style import (
    amount_cell_style, amount_value_formatter, format_signed_amount,
    case_insensitive_comparator,
)


def donations_page(conn):
    st.title("Non-cash Donations")
    st.caption(
        "Log charitable donations that don't flow through a bank account — "
        "goods, vehicles, stock, etc. Fair-market-value in dollars. These are "
        "kept separately from the transaction ledger and will feed a section "
        "of the annual tax report."
    )

    # --- Year filter ---
    row = conn.execute(
        "SELECT MIN(date) AS lo, MAX(date) AS hi FROM non_cash_donations"
    ).fetchone()
    today = date.today()
    if row and row["lo"]:
        first_year = int(row["lo"][:4])
        last_year = max(int(row["hi"][:4]), today.year)
    else:
        first_year = last_year = today.year
    years = list(range(first_year, last_year + 1))
    col_y, col_stat, _ = st.columns([1, 3, 3])
    year_choices = ["All years"] + [str(y) for y in reversed(years)]
    year_pick = col_y.selectbox("Year", year_choices, key="donations_year")
    year_filter = None if year_pick == "All years" else int(year_pick)

    rows = queries.list_non_cash_donations(conn, year=year_filter)
    total_amt = sum(Decimal(str(r["amount"])) for r in rows)
    col_stat.metric(
        f"Total ({year_pick})",
        format_signed_amount(float(total_amt)),
        help=f"{len(rows)} donation(s) in view.",
    )

    st.divider()

    # --- Add new donation ---
    with st.expander("➕ Add new donation", expanded=(len(rows) == 0)):
        with st.form("add_donation", clear_on_submit=True):
            c1, c2 = st.columns([1, 1])
            d = c1.date_input("Date", value=today, key="donation_new_date")
            amt = c2.number_input(
                "Amount (fair market value)", min_value=0.0, step=0.01,
                format="%.2f", key="donation_new_amt",
            )
            recipient = st.text_input(
                "Recipient", key="donation_new_rec",
                placeholder="e.g. Goodwill",
            )
            description = st.text_input(
                "Description", key="donation_new_desc",
                placeholder="e.g. box of used clothing",
            )
            reference = st.text_input(
                "Reference number (optional)", key="donation_new_ref",
                placeholder="Receipt or reference number",
            )
            submitted = st.form_submit_button("Add donation", type="primary")
            if submitted:
                if not recipient.strip():
                    st.error("Recipient is required.")
                elif amt <= 0:
                    st.error("Amount must be greater than 0.")
                else:
                    queries.insert_non_cash_donation(
                        conn,
                        d.isoformat(),
                        amt,
                        recipient.strip(),
                        description.strip() or None,
                        reference.strip() or None,
                    )
                    st.success(
                        f"Added: {d.isoformat()} · ${amt:,.2f} · "
                        f"{recipient.strip()}"
                    )
                    st.rerun()

    # --- Existing donations table ---
    if not rows:
        st.info("No non-cash donations recorded yet. Add one above.")
        return

    data = []
    for r in rows:
        data.append({
            "id": r["id"],
            "Date": r["date"],
            "Amount": float(r["amount"]),
            "Recipient": r["recipient"],
            "Description": r["description"] or "",
            "Reference #": r["reference_number"] or "",
        })
    df = pd.DataFrame(data)

    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, sortable=True, editable=False,
                                comparator=case_insensitive_comparator())
    gb.configure_grid_options(singleClickEdit=True,
                              stopEditingWhenCellsLoseFocus=True)
    gb.configure_column("id", hide=True)
    gb.configure_column("Date", width=110, editable=True)
    gb.configure_column("Amount", width=110, editable=True,
                        type=["numericColumn"],
                        valueFormatter=amount_value_formatter(),
                        cellStyle=amount_cell_style())
    gb.configure_column("Recipient", width=200, editable=True)
    gb.configure_column("Description", width=300, editable=True)
    gb.configure_column("Reference #", width=150, editable=True)
    gb.configure_selection(selection_mode="single", use_checkbox=True)

    grid_response = AgGrid(
        df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.MODEL_CHANGED,
        fit_columns_on_grid_load=True,
        height=400,
        allow_unsafe_jscode=True,
    )

    col_save, col_del, _ = st.columns([1, 1, 4])

    if col_save.button("Save Changes", key="donations_save"):
        edited = grid_response["data"]
        saved = 0
        for _, row in edited.iterrows():
            queries.update_non_cash_donation(
                conn, int(row["id"]),
                date=row["Date"],
                amount=float(row["Amount"]),
                recipient=row["Recipient"] or "",
                description=(row["Description"] or None) or None,
                reference_number=(row["Reference #"] or None) or None,
            )
            saved += 1
        st.success(f"Saved {saved} donation(s).")
        st.rerun()

    sel = grid_response.get("selected_rows")
    if sel is not None and len(sel) > 0:
        selected_id = int(sel.iloc[0]["id"])
        if col_del.button(f"Delete selected (id={selected_id})",
                          key="donations_delete"):
            queries.delete_non_cash_donation(conn, selected_id)
            st.success(f"Deleted donation id={selected_id}.")
            st.rerun()
