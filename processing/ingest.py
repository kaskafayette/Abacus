"""Step 1–2: file validation, filename parsing, template setup, and CSV ingestion."""

import csv
import re
import sqlite3
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from db import queries

INPUT_DIR = Path(__file__).resolve().parent.parent / "input"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "processed"

# Pattern: <prefix> <MM-DD-YYYY> to <MM-DD-YYYY>.csv  (tolerant of extra whitespace)
FILENAME_RE = re.compile(
    r"^(?P<prefix>.+?)\s+(?P<start>\d{2}-\d{2}-\d{4})\s+to\s+(?P<end>\d{2}-\d{2}-\d{4})\.csv$",
    re.IGNORECASE,
)

FILENAME_DATE_FMT = "%m-%d-%Y"


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

def parse_filename(filename: str) -> dict | None:
    """Parse a CSV filename into prefix, start date, end date.

    Returns dict with keys: prefix, start_date, end_date (as date objects),
    or None if the filename doesn't match the expected pattern.
    """
    m = FILENAME_RE.match(filename)
    if not m:
        return None
    try:
        start = datetime.strptime(m.group("start"), FILENAME_DATE_FMT).date()
        end = datetime.strptime(m.group("end"), FILENAME_DATE_FMT).date()
    except ValueError:
        return None
    return {
        "prefix": m.group("prefix").strip(),
        "start_date": start,
        "end_date": end,
    }


# ---------------------------------------------------------------------------
# File validation (Step 1)
# ---------------------------------------------------------------------------

class FileValidationResult:
    """Validation result for a single input file."""

    def __init__(self, filename: str):
        self.filename = filename
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.parsed: dict | None = None      # output of parse_filename
        self.template: sqlite3.Row | None = None
        self.needs_template: bool = False
        self.file_hash: str | None = None

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0 and not self.needs_template


def validate_all_files(conn: sqlite3.Connection,
                       period_start: date,
                       period_end: date) -> list[FileValidationResult]:
    """Validate every CSV in the input folder. Returns a result per file."""
    INPUT_DIR.mkdir(exist_ok=True)
    files = sorted(f for f in INPUT_DIR.iterdir() if f.suffix.lower() == ".csv")
    if not files:
        return []

    results = []
    for fp in files:
        result = _validate_one(conn, fp, period_start, period_end)
        results.append(result)
    return results


def _validate_one(conn: sqlite3.Connection, filepath: Path,
                  period_start: date, period_end: date) -> FileValidationResult:
    result = FileValidationResult(filepath.name)

    # 1. Filename format
    parsed = parse_filename(filepath.name)
    if parsed is None:
        result.errors.append(
            f"Filename does not match expected pattern: <prefix> MM-DD-YYYY to MM-DD-YYYY.csv"
        )
        return result
    result.parsed = parsed

    # 2. Date range match
    if parsed["start_date"] != period_start or parsed["end_date"] != period_end:
        result.errors.append(
            f"Date range {parsed['start_date']} – {parsed['end_date']} "
            f"does not match processing period {period_start} – {period_end}"
        )

    # 3. File hash — check for duplicate ingestion
    file_hash = queries.compute_file_hash(filepath)
    result.file_hash = file_hash
    existing = queries.check_file_hash(conn, file_hash)
    if existing:
        result.errors.append(
            f"This file has already been ingested (matched processed file: {existing['filename']})"
        )

    # 4. Prefix lookup
    prefix = parsed["prefix"]
    template = queries.get_column_template(conn, prefix)
    if template is None:
        result.needs_template = True
    else:
        result.template = template

    # 5. Date continuity check
    _check_continuity(conn, result, parsed, prefix, template)

    return result


def _check_continuity(conn, result, parsed, prefix, template):
    """Check for gaps or overlaps with existing transaction data."""
    # For multi-card accounts, check each sub-source
    sources_to_check = []
    if template and template["card_column"]:
        # We can't know card values until we read the file, so check the base prefix
        label = queries.get_source_label(conn, prefix)
        if label:
            sources_to_check.append(label)
    else:
        label = queries.get_source_label(conn, prefix)
        if label:
            sources_to_check.append(label)

    for source in sources_to_check:
        latest = queries.get_latest_transaction_date(conn, source)
        if latest is None:
            continue
        latest_date = datetime.strptime(latest, "%Y-%m-%d").date() if isinstance(latest, str) else latest
        file_start = parsed["start_date"]

        gap_days = (file_start - latest_date).days
        if gap_days > 1:
            result.warnings.append(
                f"Gap detected for {source}: last transaction {latest_date}, "
                f"file starts {file_start} ({gap_days - 1} day gap)"
            )
        elif gap_days < 0:
            overlap_end = min(latest_date, parsed["end_date"])
            result.warnings.append(
                f"Overlap detected for {source}: existing data through {latest_date}, "
                f"file starts {file_start} (overlap through {overlap_end})"
            )


# ---------------------------------------------------------------------------
# CSV reading helpers
# ---------------------------------------------------------------------------

def _clean_csv_lines(filepath: Path):
    """Yield lines from a CSV file with trailing commas and whitespace stripped."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for line in f:
            yield line.rstrip().rstrip(",")


def read_csv_headers(filepath: Path) -> list[str]:
    """Read and return the column headers from a CSV file."""
    reader = csv.reader(_clean_csv_lines(filepath))
    headers = next(reader)
    return [h.strip() for h in headers if h.strip()]


def read_csv_rows(filepath: Path) -> tuple[list[str], list[dict]]:
    """Read a CSV file and return (headers, list of row dicts)."""
    reader = csv.DictReader(_clean_csv_lines(filepath))
    headers = [h.strip() for h in (reader.fieldnames or []) if h.strip()]
    rows = []
    for row in reader:
        cleaned = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k and k.strip()}
        rows.append(cleaned)
    return headers, rows


# ---------------------------------------------------------------------------
# CSV parsing with template (Step 2 complete → Step 1 ingestion)
# ---------------------------------------------------------------------------

def parse_file_with_template(filepath: Path, template: sqlite3.Row,
                              conn: sqlite3.Connection) -> list[dict]:
    """Parse a CSV file using its column template.

    Returns a list of transaction dicts ready for insert_transactions().
    Each dict has all the fields needed, with payee/category/etc. set to defaults
    to be filled in during normalization and categorization steps.
    """
    parsed_info = parse_filename(filepath.name)
    prefix = parsed_info["prefix"]

    _, rows = read_csv_rows(filepath)
    transactions = []

    for row in rows:
        # Parse date
        raw_date = row.get(template["date_column"], "").strip()
        if not raw_date:
            continue
        try:
            dt = datetime.strptime(raw_date, template["date_format"]).date()
        except ValueError:
            continue

        # Parse amount
        amount = _parse_amount(row, template)
        if amount is None:
            continue

        # Raw description
        desc_raw = row.get(template["description_column"], "").strip()

        # Check number
        check_num = None
        if template["check_number_column"]:
            check_num = row.get(template["check_number_column"], "").strip() or None

        # Category raw (from source, e.g. Chase credit card)
        cat_raw = None
        if template["category_raw_column"]:
            cat_raw = row.get(template["category_raw_column"], "").strip() or None

        # Determine source label
        if template["card_column"]:
            card_val = row.get(template["card_column"], "").strip()
            sub_prefix = f"{prefix}-{card_val}" if card_val else prefix
            source_label = queries.get_source_label(conn, sub_prefix)
            if not source_label:
                source_label = sub_prefix
        else:
            source_label = queries.get_source_label(conn, prefix)
            if not source_label:
                source_label = prefix

        # Extract order/reference code from description
        order_ref = _extract_order_ref(desc_raw)

        transactions.append({
            "date": dt.isoformat(),
            "amount": str(amount),
            "check_number": check_num,
            "description_raw": desc_raw,
            "category_raw": cat_raw,
            "payee": None,
            "via": None,
            "payor": None,
            "category": None,
            "subcategory": None,
            "tax_flags": None,
            "note": None,
            "order_ref": order_ref,
            "source": source_label,
            "status": "pending",
            "overridden": 0,
        })

    return transactions


# Patterns that contain an order/reference code after * or #
_ORDER_REF_PATTERNS = [
    re.compile(r"Amazon\.com\*(\w+)", re.IGNORECASE),
    re.compile(r"AMAZON MKTPL\*(\w+)", re.IGNORECASE),
    re.compile(r"AMAZON MKTPLACE\s+\w+\s+(\w+)", re.IGNORECASE),
    re.compile(r"AMAZON PRIME\*(\w+)", re.IGNORECASE),
    re.compile(r"Prime Video \*(\w+)", re.IGNORECASE),
    re.compile(r"Audible\*(\w+)", re.IGNORECASE),
    re.compile(r"Etsy\.com\*(\w+)", re.IGNORECASE),
    re.compile(r"GOOGLE \*(\w+)", re.IGNORECASE),
    re.compile(r"Lumosity\.com\*(\w+)", re.IGNORECASE),
    re.compile(r"Scribd \*(\w+)", re.IGNORECASE),
    re.compile(r"ONEQUINCE\*\s*(\w+)", re.IGNORECASE),
    re.compile(r"GiftHealth\*(\w+)", re.IGNORECASE),
    re.compile(r"FANDANGO\s+\*(\w+)", re.IGNORECASE),
]


def _extract_order_ref(description: str) -> str | None:
    """Extract an order/reference code from a raw description, if present."""
    for pattern in _ORDER_REF_PATTERNS:
        m = pattern.search(description)
        if m:
            return m.group(1)
    return None


def _parse_amount(row: dict, template: sqlite3.Row) -> Decimal | None:
    """Parse the amount from a row, applying sign convention.

    Returns a Decimal where negative = money out, positive = money in
    (bank statement convention).
    """
    try:
        if template["amount_mode"] == "single":
            raw = row.get(template["amount_column"], "").strip().replace(",", "")
            if not raw:
                return None
            val = Decimal(raw)
            if template["sign_convention"] == "negative_is_debit":
                # Source already uses negative = money out — keep as-is
                return val
            else:  # positive_is_debit
                # Source uses positive = money out — flip sign
                return -val
        else:  # split
            debit_raw = row.get(template["debit_column"], "").strip().replace(",", "")
            credit_raw = row.get(template["credit_column"], "").strip().replace(",", "")
            if debit_raw:
                return -abs(Decimal(debit_raw))
            elif credit_raw:
                return abs(Decimal(credit_raw))
            else:
                return None
    except (InvalidOperation, KeyError):
        return None


# ---------------------------------------------------------------------------
# Ingestion orchestration
# ---------------------------------------------------------------------------

def ingest_file(conn: sqlite3.Connection, filepath: Path,
                template: sqlite3.Row) -> list[dict]:
    """Parse a file and insert transactions. Returns the inserted rows.

    Also checks for row-level duplicates and records the file as processed.
    Raises ValueError if duplicates are found.
    """
    transactions = parse_file_with_template(filepath, template, conn)
    parsed_info = parse_filename(filepath.name)

    # Row-level duplicate check
    duplicates = []
    for txn in transactions:
        if queries.check_duplicate_rows(conn, txn["date"], txn["source"],
                                         txn["amount"], txn["description_raw"]):
            duplicates.append(txn)

    if duplicates:
        raise ValueError(
            f"Found {len(duplicates)} duplicate transaction(s) in {filepath.name}. "
            f"Resolve before proceeding."
        )

    # Insert all transactions in one batch
    queries.insert_transactions(conn, transactions)

    # Record the processed file
    file_hash = queries.compute_file_hash(filepath)
    queries.record_processed_file(
        conn,
        filename=filepath.name,
        source_prefix=parsed_info["prefix"],
        file_hash=file_hash,
        date_start=parsed_info["start_date"].isoformat(),
        date_end=parsed_info["end_date"].isoformat(),
    )

    # Move file to processed/
    PROCESSED_DIR.mkdir(exist_ok=True)
    filepath.rename(PROCESSED_DIR / filepath.name)

    return transactions
