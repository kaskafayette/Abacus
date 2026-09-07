"""Venmo statement ingest — treats Venmo as a first-class checking-style
account rather than as an enrichment source.

Under this model, every Venmo statement row becomes a transaction on the
`Venmo` source. Chase↔Venmo funding is Transfer on both sides (nets to zero;
excluded from spending summaries). Venmo→external payments are the real
spend transactions, categorized normally.

The parsing helpers live here (rather than in enrich.py or the generic
column-template path in ingest.py) because the Venmo CSV has banner rows,
balance markers, and a closing disclaimer that the standard tabular ingest
can't handle.
"""

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from db import queries

# 2 banner rows, then column headers, then a balance-marker row, then
# transactions, then a closing/disclaimer row.
_VENMO_BANNER_ROWS = 2

# Any Chase account marker in Funding Source ("Chase Personal Checking *5616")
_CHASE_FUNDING_RE = re.compile(r"Chase.*\*\d{4}", re.IGNORECASE)

_AMOUNT_CLEAN_RE = re.compile(r"[^\d.\-]")

VENMO_SOURCE = "Venmo"


def _parse_amount(raw: str) -> Decimal | None:
    """Parse a Venmo amount like '- $34.50' or '- $1,500.00'."""
    if not raw:
        return None
    cleaned = _AMOUNT_CLEAN_RE.sub("", raw)
    if not cleaned or cleaned in ("-", ".", "-."):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _clean_name(name: str) -> str:
    """Title-case an all-caps counterparty name; leave mixed case alone."""
    from processing.normalize import smart_title
    s = (name or "").strip()
    if s and s == s.upper():
        s = smart_title(s)
    return s


def parse_venmo_transactions(filepath: Path) -> list[dict]:
    """Parse a Venmo statement CSV into transaction dicts for the Venmo source.

    Emits one transaction per Complete Venmo record:
      - source          = 'Venmo'
      - date            = Datetime (date part)
      - amount          = signed (negative = outflow, matches bank convention)
      - payee           = counterparty (To for outflows, From for inflows)
      - description_raw = 'VENMO {venmo_id} {counterparty}' — includes the
                          Venmo transaction ID so re-ingest dedup works
                          (the check_duplicate_rows key uses description_raw).
      - note            = the Venmo note, if any
      - via             = None (Venmo IS the source, not an intermediary)
      - status          = 'pending'
      - overridden      = 0
      - category        = 'Transfer' if the Funding Source references a Chase
                          account (money moving between our own accounts —
                          the corresponding Chase VENMO PAYMENT row defaults
                          to Transfer too, so they cancel out in reports).
                          NULL for Venmo-balance-funded rows (user categorizes).
    """
    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        lines = f.readlines()

    body = "".join(lines[_VENMO_BANNER_ROWS:])
    reader = csv.DictReader(io.StringIO(body))

    out: list[dict] = []
    for raw_row in reader:
        row = {(k or "").strip(): (v.strip() if v else "")
               for k, v in raw_row.items() if k is not None}

        venmo_id = row.get("ID", "")
        if not venmo_id:
            continue
        if row.get("Status", "") != "Complete":
            continue

        raw_dt = row.get("Datetime", "")
        try:
            dt = datetime.strptime(raw_dt, "%Y-%m-%dT%H:%M:%S").date()
        except ValueError:
            continue

        amount = _parse_amount(row.get("Amount (total)", ""))
        if amount is None:
            continue

        from_name = row.get("From", "").strip()
        to_name = row.get("To", "").strip()
        counterparty_raw = to_name if amount < 0 else from_name
        payee = _clean_name(counterparty_raw) if counterparty_raw else None

        note = row.get("Note", "").strip() or None
        funding = row.get("Funding Source", "")
        chase_funded = bool(_CHASE_FUNDING_RE.search(funding))
        vtype = row.get("Type", "") or "Payment"

        # A Chase-funded Venmo row corresponds to a matching Chase VENMO
        # PAYMENT row (which defaults to Transfer under the new model). Set
        # this row's category to Transfer as well so they net out in reports.
        # Balance-funded rows are real spending and stay uncategorized.
        default_category = "Transfer" if chase_funded else None

        description_raw = f"VENMO {venmo_id} {vtype} {counterparty_raw}".strip()

        out.append({
            "date":            dt.isoformat(),
            "amount":          str(amount),
            "check_number":    None,
            "description_raw": description_raw,
            "category_raw":    None,
            "payee":           payee,
            "via":             None,
            "payor":           None,
            "category":        default_category,
            "subcategory":     None,
            "tax_flags":       None,
            "note":            note,
            "order_ref":       None,
            "source":          VENMO_SOURCE,
            "status":          "pending",
            "overridden":      0,
        })

    return out


def ingest_venmo_file(conn, filepath: Path) -> tuple[list[dict], int]:
    """Parse a Venmo CSV and insert new transactions (silent dedup skip).

    Returns (inserted_rows, skipped_count). Skipped rows are duplicates that
    matched an existing (date, source, amount, description_raw) row.
    """
    all_rows = parse_venmo_transactions(filepath)
    fresh = []
    skipped = 0
    for r in all_rows:
        if queries.check_duplicate_rows(
            conn, r["date"], r["source"], r["amount"], r["description_raw"]
        ):
            skipped += 1
            continue
        fresh.append(r)
    if fresh:
        queries.insert_transactions(conn, fresh)
    return fresh, skipped
