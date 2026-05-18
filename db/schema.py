"""Database schema definitions and initialization for Abacus."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "abacus.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            DATE NOT NULL,
    amount          DECIMAL NOT NULL,
    check_number    TEXT,
    description_raw TEXT NOT NULL,
    category_raw    TEXT,
    payee           TEXT,
    via             TEXT,
    payor           TEXT,
    category        TEXT,
    subcategory     TEXT,
    tax_flags       TEXT,
    note            TEXT,
    order_ref       TEXT,
    source          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'confirmed', 'needs_review')),
    overridden      BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payee_normalization (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    search_pattern  TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    payee_suffix    TEXT
);

CREATE TABLE IF NOT EXISTS payee_metadata (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_name      TEXT NOT NULL UNIQUE,
    category_override    TEXT,
    subcategory_override TEXT,
    tax_flags_override   TEXT,
    payor                TEXT,
    note                 TEXT
);

CREATE TABLE IF NOT EXISTS categories (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    category         TEXT NOT NULL,
    subcategory      TEXT,
    tax_flag_default TEXT,
    UNIQUE (category, subcategory)
);

CREATE TABLE IF NOT EXISTS source_file_map (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_prefix       TEXT NOT NULL UNIQUE,
    source_label        TEXT NOT NULL,
    nickname            TEXT,
    account_type        TEXT,
    discontinued_since  DATE,
    replaced_by_prefix  TEXT
);

CREATE TABLE IF NOT EXISTS column_templates (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_prefix      TEXT NOT NULL UNIQUE,
    date_column        TEXT NOT NULL,
    check_number_column TEXT,
    date_format        TEXT NOT NULL,
    amount_mode        TEXT NOT NULL CHECK (amount_mode IN ('single', 'split')),
    amount_column      TEXT,
    debit_column       TEXT,
    credit_column      TEXT,
    description_column TEXT NOT NULL,
    category_raw_column TEXT,
    sign_convention    TEXT CHECK (sign_convention IN ('negative_is_debit', 'positive_is_debit')),
    card_column        TEXT
);

CREATE TABLE IF NOT EXISTS processed_files (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    filename         TEXT NOT NULL,
    source_prefix    TEXT NOT NULL,
    file_hash        TEXT NOT NULL,
    date_range_start DATE NOT NULL,
    date_range_end   DATE NOT NULL,
    ingested_at      DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS db_audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    action    TEXT NOT NULL,
    detail    TEXT,
    timestamp DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS item_metadata (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key     TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    category     TEXT,
    subcategory  TEXT,
    tax_flags    TEXT
);
"""

# Enrichment-source seeds inserted on first init. Account types ending in
# _detail are handled by the enrichment pipeline rather than column templates.
SEED_ENRICHMENT_SOURCES = [
    # (source_prefix, source_label, nickname, account_type)
    ("Venmo",  "Venmo",  "Venmo (shared)",  "venmo_detail"),
    ("Amazon", "Amazon", "Amazon (shared)", "amazon_detail"),
]

# Seed data: the full category taxonomy from the spec.
# Each tuple is (category, subcategory, tax_flag_default).
SEED_CATEGORIES = [
    # Income
    ("Income", "Social Security (net)", None),
    ("Income", "W2 and 1099 (net)", None),
    # Transfer
    ("Transfer", None, None),
    # Household
    ("Household", "Groceries", None),
    ("Household", "Utilities", None),
    ("Household", "Auto", None),
    ("Household", "Mortgage", None),
    ("Household", "Maintenance", None),
    ("Household", "Subscriptions", None),
    ("Household", "Capital Improvements", "Capital Improvements"),
    ("Household", "Office", None),
    # Fun
    ("Fun", "Restaurants", None),
    ("Fun", "Tickets and Other", None),
    ("Fun", "Travel", None),
    # Health and Wellness
    ("Health and Wellness", None, None),
    # Medical
    ("Medical", "Prescription Medicines", "Medical"),
    ("Medical", "Medical Insurance", "Medical"),
    ("Medical", "Annual Plan Charges", "Medical"),
    ("Medical", "Medical Devices and Supplies", "Medical"),
    ("Medical", "Long Term Care", "Medical"),
    ("Medical", "Travel and Lodging", "Medical"),
    ("Medical", "Hospitals", "Medical"),
    ("Medical", "Lab Fees", "Medical"),
    ("Medical", "Doctors and Dentists", "Medical"),
    ("Medical", "Therapists", "Medical"),
    ("Medical", "Misc Medical", "Medical"),
    # Business Expenses NEC — Business Expense Categories
    ("Business Expenses NEC", "Travel", "Business Expense"),
    ("Business Expenses NEC", "Meals", "Business Expense"),
    ("Business Expenses NEC", "Subcontractors", "Business Expense"),
    ("Business Expenses NEC", "Office Equipment", "Business Expense"),
    ("Business Expenses NEC", "Subscriptions and Services", "Business Expense"),
    ("Business Expenses NEC", "Miscellaneous", "Business Expense"),
    # Business Expenses (BsnsExp)
    ("Business Expenses NEC", "Bsns Exp Reimbursable", "Business Expense,Reimbursable"),
    ("Business Expenses NEC", "Bsns Exp Non-reimbursable", "Business Expense"),
    ("Business Expenses NEC", "Bsns Exp Reimbursement", "Business Expense,Reimbursable"),
    # Business Use of Home (BUH)
    ("Business Expenses NEC", "BUH Deductible Interest", "Business Expense,Home Office"),
    ("Business Expenses NEC", "BUH Real Estate Tax", "Business Expense,Home Office"),
    ("Business Expenses NEC", "BUH Insurance", "Business Expense,Home Office"),
    ("Business Expenses NEC", "BUH Repairs and Maintenance", "Business Expense,Home Office"),
    ("Business Expenses NEC", "BUH Utilities", "Business Expense,Home Office"),
    # Shopping
    ("Shopping", None, None),
    # Payments
    ("Payments", "Venmo and Zelle", None),
    # Cash
    ("Cash", "Deposited or Withdrawn", None),
    # Donations
    ("Donations", "Deductible – Cash", "Donations – Deductible"),
    ("Donations", "Deductible – Merchandise", "Donations – Deductible"),
    ("Donations", "Non-Deductible", None),
    # Tax and Investment Expenses
    ("Tax and Investment Expenses", "Taxes Paid or Refunded", "Tax-reportable"),
    ("Tax and Investment Expenses", "Tax Preparation", "Tax-reportable"),
    ("Tax and Investment Expenses", "Safe Deposit", None),
    ("Tax and Investment Expenses", "Financial Subscriptions", None),
    # Taxes Paid (sub-items under Tax and Investment Expenses)
    ("Tax and Investment Expenses", "Personal Property", "Tax-reportable"),
    ("Tax and Investment Expenses", "Vehicle Taxes", "Tax-reportable"),
]


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection to the database with standard settings."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> tuple[sqlite3.Connection, bool]:
    """Create the database if needed, apply schema, run migrations, seed defaults.

    Returns (connection, is_new) where is_new is True if the database
    was just created for the first time.
    """
    path = db_path or DB_PATH
    is_new = not path.exists()
    conn = get_connection(path)

    conn.executescript(SCHEMA_SQL)

    _migrate_source_file_map(conn)
    _migrate_venmo_to_via_only(conn)

    if is_new:
        conn.executemany(
            "INSERT OR IGNORE INTO categories (category, subcategory, tax_flag_default) "
            "VALUES (?, ?, ?)",
            SEED_CATEGORIES,
        )

    # Seed enrichment sources idempotently (safe across upgrades too)
    conn.executemany(
        "INSERT OR IGNORE INTO source_file_map "
        "(source_prefix, source_label, nickname, account_type) VALUES (?, ?, ?, ?)",
        SEED_ENRICHMENT_SOURCES,
    )
    conn.commit()

    return conn, is_new


def _migrate_source_file_map(conn: sqlite3.Connection) -> None:
    """Drop the legacy CHECK constraint on account_type and add new columns.

    The original schema constrained account_type to ('checking', 'credit_card').
    Enrichment sources need additional values ('venmo_detail', 'amazon_detail').
    SQLite can't ALTER a CHECK constraint in place, so we rebuild the table.

    Idempotent — only runs when the legacy constraint is present. New columns
    discontinued_since and replaced_by_prefix are added if missing.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='source_file_map'"
    ).fetchone()
    if not row:
        return

    sql = row["sql"]
    has_old_check = "CHECK (account_type IN" in sql
    has_discontinued = "discontinued_since" in sql
    has_replaced = "replaced_by_prefix" in sql

    if has_old_check:
        # Rebuild the table without the CHECK and with the new columns
        conn.executescript("""
            CREATE TABLE source_file_map_new (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                source_prefix       TEXT NOT NULL UNIQUE,
                source_label        TEXT NOT NULL,
                nickname            TEXT,
                account_type        TEXT,
                discontinued_since  DATE,
                replaced_by_prefix  TEXT
            );
            INSERT INTO source_file_map_new
                (id, source_prefix, source_label, nickname, account_type)
                SELECT id, source_prefix, source_label, nickname, account_type
                FROM source_file_map;
            DROP TABLE source_file_map;
            ALTER TABLE source_file_map_new RENAME TO source_file_map;
        """)
        conn.commit()
        return

    # No CHECK present, but new columns may still be missing on a partially-
    # migrated DB. Add whichever aren't there.
    if not has_discontinued:
        conn.execute("ALTER TABLE source_file_map ADD COLUMN discontinued_since DATE")
    if not has_replaced:
        conn.execute("ALTER TABLE source_file_map ADD COLUMN replaced_by_prefix TEXT")
    conn.commit()


def _migrate_venmo_to_via_only(conn: sqlite3.Connection) -> None:
    """Switch any legacy 'Venmo Payment' placeholder rows to the new convention.

    Old design: VENMO PAYMENT Chase rows normalized to payee='Venmo Payment',
                with via=NULL. A polluting payee_metadata row mapped
                'Venmo Payment' to a single category, applying it to every
                Venmo row regardless of who the real recipient was.

    New design: those Chase rows land at ingest with payee=NULL, via='Venmo'.
                The real payee comes from the Venmo enrichment file.

    This migration:
      1. Deletes the polluting payee_metadata row for 'Venmo Payment'
      2. Deletes the seed payee_normalization rule for 'Venmo Payment'
      3. Converts existing transactions with payee='Venmo Payment' to
         payee=NULL, via='Venmo' (preserves status, category, overridden so
         user's manual work is untouched here — a separate Maintenance action
         resets those when the user explicitly opts in)

    Idempotent: once converted, no row matches payee='Venmo Payment' again.
    """
    conn.execute(
        "DELETE FROM payee_metadata WHERE normalized_name = 'Venmo Payment'"
    )
    conn.execute(
        "DELETE FROM payee_normalization WHERE normalized_name = 'Venmo Payment'"
    )
    conn.execute(
        "UPDATE transactions "
        "SET payee = NULL, "
        "    via = COALESCE(NULLIF(via, ''), 'Venmo') "
        "WHERE payee = 'Venmo Payment'"
    )
    conn.commit()
