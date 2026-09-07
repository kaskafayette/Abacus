"""Fidelity brokerage-account ingest — a first-class checking-style source
whose statement CSV needs special handling.

The user's stated goal: capture money LEAVING the account (checks, wires,
EFTs to external parties or to Chase), skip everything internal (dividends,
interest, reinvestments, internal Fidelity transfers, advisor fees). Inbound
transfers from OTHER Fidelity accounts are also skipped; inbound transfers
from external sources (e.g. tax refunds) are captured.

Transfers Fidelity → Chase are categorized as Transfer on the Fidelity side;
the Chase side keeps its existing Income/Investment Drawdown treatment so
the drawdown signal survives (no double-counting). External payments land
uncategorized for user categorization.
"""

import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from db import queries

# --- Skip patterns: internal Fidelity activity, dividends, interest, etc. ---
# Applied to the Action column, case-insensitive. Any match => skip the row.
_SKIP_PATTERNS = [
    re.compile(r"^DIVIDEND RECEIVED", re.IGNORECASE),
    re.compile(r"^REINVESTMENT ", re.IGNORECASE),
    re.compile(r"^INTEREST EARNED", re.IGNORECASE),
    re.compile(r"^REDEMPTION FROM CORE ACCOUNT", re.IGNORECASE),
    # 'YOU BOUGHT' / 'YOU SOLD' — internal position changes
    re.compile(r"^YOU (BOUGHT|SOLD)", re.IGNORECASE),
    # Internal transfer from another Fidelity account: 'TRANSFERRED FROM VS 668-...'
    re.compile(r"TRANSFERRED FROM VS\s+\d", re.IGNORECASE),
    # Advisor fee (paid to Fidelity itself) — user opted to skip these
    re.compile(r"ADVISOR FEE DEDUCTED", re.IGNORECASE),
]

# Extract check number from actions like 'Check Paid # 1007 (Cash)'
_CHECK_NUM_RE = re.compile(r"Check Paid\s+#\s*(\d+)", re.IGNORECASE)


def _is_fidelity_prefix(prefix: str) -> bool:
    """A source prefix is Fidelity if it starts with 'Fidelity' (case-insensitive)."""
    return prefix.lower().startswith("fidelity")


def _parse_amount(raw: str) -> Decimal | None:
    """Parse a Fidelity Amount ($) column value; return None on failure."""
    if raw is None:
        return None
    s = raw.strip().strip('"')
    if not s:
        return None
    # Strip embedded thousands commas but keep the minus sign
    s = s.replace(",", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_date(raw: str):
    """Parse MM/DD/YYYY; return date object or None."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip().strip('"'), "%m/%d/%Y").date()
    except ValueError:
        return None


def _find_header_row(lines: list[str]) -> int:
    """Return the 0-based index of the header row (starts with 'Run Date')."""
    for i, line in enumerate(lines):
        stripped = line.lstrip("﻿").strip()
        if stripped.startswith("Run Date,"):
            return i
    raise ValueError(
        "Could not locate the header row starting with 'Run Date' in the Fidelity CSV."
    )


def parse_fidelity_transactions(filepath: Path,
                                 source: str = "Fidelity8870") -> list[dict]:
    """Parse a Fidelity brokerage CSV into transaction dicts.

    Filters out internal-only rows (dividends, interest, reinvestments,
    intra-Fidelity transfers, advisor fees). Everything else — checks, wires,
    EFTs, external inbounds — becomes a transaction on the given source.
    """
    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        lines = f.readlines()

    header_idx = _find_header_row(lines)
    body = "".join(lines[header_idx:])
    reader = csv.DictReader(body.splitlines())

    out: list[dict] = []
    for raw_row in reader:
        row = {(k or "").strip(): (v.strip() if v else "")
               for k, v in raw_row.items() if k is not None}

        action = row.get("Action", "")
        if not action:
            continue

        # Skip internal / non-cash-movement rows
        if any(p.search(action) for p in _SKIP_PATTERNS):
            continue

        run_date = _parse_date(row.get("Run Date", ""))
        if run_date is None:
            continue

        amount = _parse_amount(row.get("Amount ($)", ""))
        if amount is None or amount == 0:
            continue

        # Extract check number from Action if present
        m = _CHECK_NUM_RE.search(action)
        check_number = m.group(1) if m else None

        # Include cash balance in description_raw for dedup uniqueness (same-
        # day same-amount rows share Action text, but each has a unique running
        # balance, so dedup keys on (date+source+amount+description) still work).
        bal = row.get("Cash Balance ($)", "").strip()
        description_raw = f"{action} [bal={bal}]" if bal else action

        out.append({
            "date":            run_date.isoformat(),
            "amount":          str(amount),
            "check_number":    check_number,
            "description_raw": description_raw,
            "category_raw":    None,
            "payee":           None,
            "via":             None,
            "payor":           None,
            "category":        None,
            "subcategory":     None,
            "tax_flags":       None,
            "note":            None,
            "order_ref":       None,
            "source":          source,
            "status":          "pending",
            "overridden":      0,
        })

    return out
