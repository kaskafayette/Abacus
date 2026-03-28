"""All database access functions for Abacus."""

import hashlib
import sqlite3
from decimal import Decimal
from pathlib import Path


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

def get_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM categories ORDER BY category, subcategory"
    ).fetchall()


def get_category_names(conn: sqlite3.Connection) -> list[str]:
    """Distinct category names, sorted."""
    rows = conn.execute(
        "SELECT DISTINCT category FROM categories ORDER BY category"
    ).fetchall()
    return [r["category"] for r in rows]


def get_subcategories(conn: sqlite3.Connection, category: str) -> list[str]:
    """Subcategory names for a given category, sorted."""
    rows = conn.execute(
        "SELECT subcategory FROM categories WHERE category = ? AND subcategory IS NOT NULL "
        "ORDER BY subcategory",
        (category,),
    ).fetchall()
    return [r["subcategory"] for r in rows]


def upsert_category(conn: sqlite3.Connection, category: str, subcategory: str | None,
                     tax_flag_default: str | None) -> None:
    conn.execute(
        "INSERT INTO categories (category, subcategory, tax_flag_default) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT (category, subcategory) DO UPDATE SET tax_flag_default = excluded.tax_flag_default",
        (category, subcategory, tax_flag_default),
    )
    conn.commit()


def delete_category(conn: sqlite3.Connection, category_id: int) -> None:
    conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Source file map
# ---------------------------------------------------------------------------

def get_source_file_map(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM source_file_map ORDER BY source_prefix").fetchall()


def get_source_label(conn: sqlite3.Connection, prefix: str) -> str | None:
    row = conn.execute(
        "SELECT source_label FROM source_file_map WHERE source_prefix = ?", (prefix,)
    ).fetchone()
    return row["source_label"] if row else None


def upsert_source_file_map(conn: sqlite3.Connection, prefix: str, label: str) -> None:
    conn.execute(
        "INSERT INTO source_file_map (source_prefix, source_label) VALUES (?, ?) "
        "ON CONFLICT (source_prefix) DO UPDATE SET source_label = excluded.source_label",
        (prefix, label),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Column templates
# ---------------------------------------------------------------------------

def get_column_template(conn: sqlite3.Connection, prefix: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM column_templates WHERE source_prefix = ?", (prefix,)
    ).fetchone()


def get_all_column_templates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM column_templates ORDER BY source_prefix").fetchall()


def save_column_template(conn: sqlite3.Connection, **kwargs) -> None:
    """Insert or replace a column template. Pass column names as keyword args."""
    conn.execute(
        "INSERT OR REPLACE INTO column_templates "
        "(source_prefix, date_column, check_number_column, date_format, "
        "amount_mode, amount_column, debit_column, credit_column, "
        "description_column, category_raw_column, sign_convention, card_column) "
        "VALUES (:source_prefix, :date_column, :check_number_column, :date_format, "
        ":amount_mode, :amount_column, :debit_column, :credit_column, "
        ":description_column, :category_raw_column, :sign_convention, :card_column)",
        kwargs,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Payee normalization
# ---------------------------------------------------------------------------

def get_payee_normalizations(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM payee_normalization ORDER BY normalized_name, search_pattern"
    ).fetchall()


def find_payee_match(conn: sqlite3.Connection, raw_description: str) -> sqlite3.Row | None:
    """Find the first matching normalization rule for a raw description (case-insensitive)."""
    rows = conn.execute("SELECT * FROM payee_normalization").fetchall()
    raw_lower = raw_description.lower()
    for row in rows:
        if row["search_pattern"].lower() in raw_lower:
            return row
    return None


def insert_payee_normalization(conn: sqlite3.Connection, search_pattern: str,
                                normalized_name: str, payee_suffix: str | None = None) -> None:
    conn.execute(
        "INSERT INTO payee_normalization (search_pattern, normalized_name, payee_suffix) "
        "VALUES (?, ?, ?)",
        (search_pattern, normalized_name, payee_suffix),
    )
    conn.commit()


def update_payee_normalization(conn: sqlite3.Connection, row_id: int, search_pattern: str,
                                normalized_name: str, payee_suffix: str | None = None) -> None:
    conn.execute(
        "UPDATE payee_normalization SET search_pattern = ?, normalized_name = ?, payee_suffix = ? "
        "WHERE id = ?",
        (search_pattern, normalized_name, payee_suffix, row_id),
    )
    conn.commit()


def delete_payee_normalization(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("DELETE FROM payee_normalization WHERE id = ?", (row_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Payee metadata
# ---------------------------------------------------------------------------

def get_payee_metadata(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM payee_metadata ORDER BY normalized_name").fetchall()


def get_payee_metadata_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM payee_metadata WHERE normalized_name = ?", (name,)
    ).fetchone()


def upsert_payee_metadata(conn: sqlite3.Connection, **kwargs) -> None:
    conn.execute(
        "INSERT INTO payee_metadata "
        "(normalized_name, category_override, subcategory_override, tax_flags_override, payor, note) "
        "VALUES (:normalized_name, :category_override, :subcategory_override, "
        ":tax_flags_override, :payor, :note) "
        "ON CONFLICT (normalized_name) DO UPDATE SET "
        "category_override = excluded.category_override, "
        "subcategory_override = excluded.subcategory_override, "
        "tax_flags_override = excluded.tax_flags_override, "
        "payor = excluded.payor, note = excluded.note",
        kwargs,
    )
    conn.commit()


def delete_payee_metadata(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("DELETE FROM payee_metadata WHERE id = ?", (row_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def insert_transactions(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Bulk-insert transactions. Expects a list of dicts matching column names."""
    if not rows:
        return
    conn.executemany(
        "INSERT INTO transactions "
        "(date, amount, check_number, description_raw, category_raw, payee, via, payor, "
        "category, subcategory, tax_flags, note, source, status, overridden) "
        "VALUES (:date, :amount, :check_number, :description_raw, :category_raw, "
        ":payee, :via, :payor, :category, :subcategory, :tax_flags, :note, "
        ":source, :status, :overridden)",
        rows,
    )
    conn.commit()


def get_pending_transactions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM transactions WHERE status = 'pending' ORDER BY date, source"
    ).fetchall()


def get_pending_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) as cnt FROM transactions WHERE status = 'pending'").fetchone()
    return row["cnt"]


def get_transactions(conn: sqlite3.Connection, start_date: str | None = None,
                     end_date: str | None = None, source: str | None = None,
                     search: str | None = None, status: str | None = None) -> list[sqlite3.Row]:
    """Flexible transaction query with optional filters."""
    clauses = []
    params = []
    if start_date:
        clauses.append("date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("date <= ?")
        params.append(end_date)
    if source:
        clauses.append("source = ?")
        params.append(source)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if search:
        clauses.append("(payee LIKE ? OR description_raw LIKE ? OR note LIKE ?)")
        term = f"%{search}%"
        params.extend([term, term, term])

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute(
        f"SELECT * FROM transactions{where} ORDER BY date, source", params
    ).fetchall()


def update_transaction(conn: sqlite3.Connection, txn_id: int, **kwargs) -> None:
    """Update specific fields on a transaction by id."""
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [txn_id]
    conn.execute(f"UPDATE transactions SET {sets} WHERE id = ?", vals)
    conn.commit()


def get_latest_transaction_date(conn: sqlite3.Connection, source: str) -> str | None:
    """Most recent transaction date for a given source."""
    row = conn.execute(
        "SELECT MAX(date) as max_date FROM transactions WHERE source = ?", (source,)
    ).fetchone()
    return row["max_date"] if row else None


def check_duplicate_rows(conn: sqlite3.Connection, date: str, source: str,
                         amount: str, description_raw: str) -> bool:
    """Returns True if a matching row already exists."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM transactions "
        "WHERE date = ? AND source = ? AND amount = ? AND description_raw = ?",
        (date, source, amount, description_raw),
    ).fetchone()
    return row["cnt"] > 0


def purge_transactions(conn: sqlite3.Connection) -> int:
    """Delete all transactions. Returns count of deleted rows."""
    cursor = conn.execute("DELETE FROM transactions")
    count = cursor.rowcount
    conn.execute(
        "INSERT INTO db_audit_log (action, detail) VALUES (?, ?)",
        ("PURGE_TRANSACTIONS", f"Deleted {count} rows"),
    )
    conn.commit()
    return count


# ---------------------------------------------------------------------------
# Processed files
# ---------------------------------------------------------------------------

def get_processed_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM processed_files ORDER BY ingested_at DESC").fetchall()


def check_file_hash(conn: sqlite3.Connection, file_hash: str) -> sqlite3.Row | None:
    """Check if a file with this hash has already been ingested."""
    return conn.execute(
        "SELECT * FROM processed_files WHERE file_hash = ?", (file_hash,)
    ).fetchone()


def record_processed_file(conn: sqlite3.Connection, filename: str, source_prefix: str,
                           file_hash: str, date_start: str, date_end: str) -> None:
    conn.execute(
        "INSERT INTO processed_files (filename, source_prefix, file_hash, date_range_start, date_range_end) "
        "VALUES (?, ?, ?, ?, ?)",
        (filename, source_prefix, file_hash, date_start, date_end),
    )
    conn.commit()


def compute_file_hash(filepath: Path) -> str:
    """SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Stats / utility
# ---------------------------------------------------------------------------

def get_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for all tables."""
    tables = ["transactions", "payee_normalization", "payee_metadata",
              "categories", "source_file_map", "column_templates", "processed_files"]
    counts = {}
    for t in tables:
        row = conn.execute(f"SELECT COUNT(*) as cnt FROM {t}").fetchone()
        counts[t] = row["cnt"]
    return counts


def get_distinct_sources(conn: sqlite3.Connection) -> list[str]:
    """All distinct source values from transactions."""
    rows = conn.execute(
        "SELECT DISTINCT source FROM transactions ORDER BY source"
    ).fetchall()
    return [r["source"] for r in rows]
