"""All database access functions for Abacus."""

import hashlib
import sqlite3
from decimal import Decimal
from pathlib import Path


# SQL fragment selecting rows that are NOT split parents — i.e. rows that
# should be counted in totals. A split parent is any transaction referenced by
# another row's split_parent_id; its dollars are represented by its legs
# instead, so counting both would double-count. Reuse this everywhere a total
# is computed (reports, checksums, banners) so the books stay balanced.
NOT_PARENT_SQL = (
    "id NOT IN (SELECT split_parent_id FROM transactions "
    "WHERE split_parent_id IS NOT NULL)"
)


def _cents(value) -> Decimal:
    """Quantize any amount to cents. SQLite stores amount as REAL, so sums can
    carry float noise; comparing at cent precision is the correct test."""
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


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
    """Case-insensitive lookup of source_label by prefix."""
    row = conn.execute(
        "SELECT source_label FROM source_file_map WHERE source_prefix = ? COLLATE NOCASE",
        (prefix,),
    ).fetchone()
    return row["source_label"] if row else None


def upsert_source_file_map(conn: sqlite3.Connection, prefix: str, label: str,
                           nickname: str | None = None,
                           account_type: str | None = None) -> None:
    conn.execute(
        "INSERT INTO source_file_map (source_prefix, source_label, nickname, account_type) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT (source_prefix) DO UPDATE SET source_label = excluded.source_label, "
        "nickname = excluded.nickname, account_type = excluded.account_type",
        (prefix, label, nickname, account_type),
    )
    conn.commit()


def get_source_row(conn: sqlite3.Connection, prefix: str) -> sqlite3.Row | None:
    """Full source_file_map row for a prefix (case-insensitive lookup)."""
    return conn.execute(
        "SELECT * FROM source_file_map WHERE source_prefix = ? COLLATE NOCASE",
        (prefix,),
    ).fetchone()


def get_account_type(conn: sqlite3.Connection, prefix: str) -> str | None:
    """Case-insensitive lookup of account_type by prefix."""
    row = conn.execute(
        "SELECT account_type FROM source_file_map WHERE source_prefix = ? COLLATE NOCASE",
        (prefix,),
    ).fetchone()
    return row["account_type"] if row else None


def set_replaced_by_prefix(conn: sqlite3.Connection, old_prefix: str, new_prefix: str) -> None:
    """Link an old (reissued/discontinued) account to its replacement."""
    conn.execute(
        "UPDATE source_file_map SET replaced_by_prefix = ? WHERE source_prefix = ?",
        (new_prefix, old_prefix),
    )
    conn.commit()


def mark_account_discontinued(conn: sqlite3.Connection, prefix: str, since: str | None = None) -> None:
    """Mark an account as discontinued so missing-account warnings stop firing."""
    from datetime import date as _date
    when = since or _date.today().isoformat()
    conn.execute(
        "UPDATE source_file_map SET discontinued_since = ? WHERE source_prefix = ?",
        (when, prefix),
    )
    conn.commit()


def get_active_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Source rows that aren't marked discontinued."""
    return conn.execute(
        "SELECT * FROM source_file_map WHERE discontinued_since IS NULL "
        "ORDER BY source_prefix"
    ).fetchall()


# ---------------------------------------------------------------------------
# Column templates
# ---------------------------------------------------------------------------

def get_column_template(conn: sqlite3.Connection, prefix: str) -> sqlite3.Row | None:
    """Case-insensitive lookup of the column_template for a prefix."""
    return conn.execute(
        "SELECT * FROM column_templates WHERE source_prefix = ? COLLATE NOCASE",
        (prefix,),
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
        "category, subcategory, tax_flags, note, order_ref, source, status, overridden) "
        "VALUES (:date, :amount, :check_number, :description_raw, :category_raw, "
        ":payee, :via, :payor, :category, :subcategory, :tax_flags, :note, "
        ":order_ref, :source, :status, :overridden)",
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
                     search: str | None = None, search_payee: str | None = None,
                     status: str | None = None,
                     exclude_parents: bool = False) -> list[sqlite3.Row]:
    """Flexible transaction query with optional filters.

    exclude_parents=True drops split-parent rows (their legs carry the dollars),
    so the result totals correctly. Leave it False for editing/browsing views
    that need to display parents.
    """
    clauses = []
    params = []
    if exclude_parents:
        clauses.append(NOT_PARENT_SQL)
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
    if search_payee:
        clauses.append("payee LIKE ?")
        params.append(f"%{search_payee}%")
    if search:
        clauses.append(
            "(payee LIKE ? OR description_raw LIKE ? OR note LIKE ? "
            "OR category LIKE ? OR subcategory LIKE ? OR tax_flags LIKE ? "
            "OR source LIKE ? OR via LIKE ? OR payor LIKE ?)"
        )
        term = f"%{search}%"
        params.extend([term] * 9)

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


def get_transaction(conn: sqlite3.Connection, txn_id: int) -> sqlite3.Row | None:
    """Fetch a single transaction by id."""
    return conn.execute(
        "SELECT * FROM transactions WHERE id = ?", (txn_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Transaction splitting
#
# A "split" breaks one ingested transaction into several legs, each separately
# categorizable. The original (parent) row is never modified — it stays so that
# re-ingest dedup and bank-statement comparison keep matching. Each leg is a
# new row whose split_parent_id points at the parent. Parent-ness is derived
# from those references (see NOT_PARENT_SQL), never stored as a flag.
# ---------------------------------------------------------------------------

MAX_SPLIT_LEGS = 10


def get_parent_ids(conn: sqlite3.Connection) -> set[int]:
    """Set of transaction ids that have at least one split leg (i.e. parents)."""
    rows = conn.execute(
        "SELECT DISTINCT split_parent_id FROM transactions "
        "WHERE split_parent_id IS NOT NULL"
    ).fetchall()
    return {r["split_parent_id"] for r in rows}


def get_split_children(conn: sqlite3.Connection, parent_id: int) -> list[sqlite3.Row]:
    """All legs of a split, oldest-id first (creation order)."""
    return conn.execute(
        "SELECT * FROM transactions WHERE split_parent_id = ? ORDER BY id",
        (parent_id,),
    ).fetchall()


def split_role(conn: sqlite3.Connection, txn_id: int) -> str:
    """Return '', 'parent', or 'leg' for a transaction id."""
    row = get_transaction(conn, txn_id)
    if row is None:
        return ""
    if row["split_parent_id"] is not None:
        return "leg"
    if conn.execute(
        "SELECT 1 FROM transactions WHERE split_parent_id = ? LIMIT 1", (txn_id,)
    ).fetchone():
        return "parent"
    return ""


def replace_split_legs(conn: sqlite3.Connection, parent_id: int,
                       legs: list[dict], status: str = "confirmed") -> None:
    """Create or replace the legs of a split atomically.

    `legs` is a list of dicts; each may carry amount, category, subcategory,
    payee, payor, tax_flags, check_number, note. Legs inherit the parent's
    date, source and description_raw. Raises ValueError if there are not
    between 2 and MAX_SPLIT_LEGS legs, or if their amounts don't sum (to the
    cent) to the parent's amount.
    """
    parent = get_transaction(conn, parent_id)
    if parent is None:
        raise ValueError(f"Transaction {parent_id} not found.")
    if parent["split_parent_id"] is not None:
        raise ValueError("Cannot split a row that is itself a split leg.")

    if not (2 <= len(legs) <= MAX_SPLIT_LEGS):
        raise ValueError(
            f"A split needs between 2 and {MAX_SPLIT_LEGS} legs; got {len(legs)}."
        )

    legs_total = sum(_cents(leg.get("amount")) for leg in legs)
    if legs_total != _cents(parent["amount"]):
        raise ValueError(
            f"Legs total {legs_total} does not equal parent amount "
            f"{_cents(parent['amount'])} (off by "
            f"{_cents(parent['amount']) - legs_total})."
        )

    rows = []
    for leg in legs:
        rows.append({
            "date": parent["date"],
            "amount": str(_cents(leg.get("amount"))),
            "check_number": leg.get("check_number") or None,
            "description_raw": parent["description_raw"],
            "category_raw": None,
            "payee": leg.get("payee") or None,
            "via": None,
            "payor": leg.get("payor") or None,
            "category": leg.get("category") or None,
            "subcategory": leg.get("subcategory") or None,
            "tax_flags": leg.get("tax_flags") or None,
            "note": leg.get("note") or None,
            "order_ref": None,
            "source": parent["source"],
            "status": status,
            "overridden": 1,
            "split_parent_id": parent_id,
        })

    # Replace any existing legs in one transaction so a split is never partial.
    conn.execute("DELETE FROM transactions WHERE split_parent_id = ?", (parent_id,))
    conn.executemany(
        "INSERT INTO transactions "
        "(date, amount, check_number, description_raw, category_raw, payee, via, "
        "payor, category, subcategory, tax_flags, note, order_ref, source, "
        "status, overridden, split_parent_id) "
        "VALUES (:date, :amount, :check_number, :description_raw, :category_raw, "
        ":payee, :via, :payor, :category, :subcategory, :tax_flags, :note, "
        ":order_ref, :source, :status, :overridden, :split_parent_id)",
        rows,
    )
    conn.execute(
        "INSERT INTO db_audit_log (action, detail) VALUES (?, ?)",
        ("SPLIT_TRANSACTION",
         f"parent={parent_id} legs={len(rows)} total={legs_total}"),
    )
    conn.commit()


def unsplit_transaction(conn: sqlite3.Connection, parent_id: int) -> int:
    """Delete all legs of a split, returning the parent to a normal row.

    Returns the number of legs removed. Atomic — you can never be left half-split.
    """
    cur = conn.execute(
        "DELETE FROM transactions WHERE split_parent_id = ?", (parent_id,)
    )
    conn.execute(
        "INSERT INTO db_audit_log (action, detail) VALUES (?, ?)",
        ("UNSPLIT_TRANSACTION", f"parent={parent_id} legs_removed={cur.rowcount}"),
    )
    conn.commit()
    return cur.rowcount


def check_split_integrity(conn: sqlite3.Connection) -> list[dict]:
    """Per-split balance check: for each parent, sum(legs) must equal the parent.

    Returns one dict per UNBALANCED split (empty list means all splits balance).
    A per-split check is essential — a global parents-vs-legs total could net to
    zero while individual splits are wrong in opposite directions.
    """
    parents = conn.execute(
        "SELECT p.id, p.date, p.source, p.amount AS parent_amount, "
        "       p.description_raw, "
        "       COALESCE(SUM(c.amount), 0) AS legs_total, "
        "       COUNT(c.id) AS leg_count "
        "FROM transactions p "
        "JOIN transactions c ON c.split_parent_id = p.id "
        "GROUP BY p.id ORDER BY p.date"
    ).fetchall()
    broken = []
    for p in parents:
        parent_amt = _cents(p["parent_amount"])
        legs_total = _cents(p["legs_total"])
        diff = parent_amt - legs_total
        if diff != 0:
            broken.append({
                "id": p["id"],
                "date": p["date"],
                "source": p["source"],
                "description_raw": p["description_raw"],
                "parent_amount": parent_amt,
                "legs_total": legs_total,
                "difference": diff,
                "leg_count": p["leg_count"],
            })
    return broken


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


def get_sources_seen_in_history(conn: sqlite3.Connection) -> dict[str, str]:
    """Map of source_prefix → most-recent date_range_end seen in processed_files.

    Used for the cross-source completeness check at Ingest time.
    """
    rows = conn.execute(
        "SELECT source_prefix, MAX(date_range_end) AS last_end "
        "FROM processed_files GROUP BY source_prefix"
    ).fetchall()
    return {r["source_prefix"]: r["last_end"] for r in rows}


# ---------------------------------------------------------------------------
# Item metadata (Amazon line-item learning)
# ---------------------------------------------------------------------------

def get_item_metadata(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM item_metadata ORDER BY display_name"
    ).fetchall()


def get_item_metadata_by_key(conn: sqlite3.Connection, item_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM item_metadata WHERE item_key = ?", (item_key,)
    ).fetchone()


def upsert_item_metadata(conn: sqlite3.Connection, item_key: str, display_name: str,
                          category: str | None = None,
                          subcategory: str | None = None,
                          tax_flags: str | None = None) -> None:
    conn.execute(
        "INSERT INTO item_metadata (item_key, display_name, category, subcategory, tax_flags) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (item_key) DO UPDATE SET "
        "display_name = excluded.display_name, "
        "category = excluded.category, "
        "subcategory = excluded.subcategory, "
        "tax_flags = excluded.tax_flags",
        (item_key, display_name, category, subcategory, tax_flags),
    )
    conn.commit()


def delete_item_metadata(conn: sqlite3.Connection, row_id: int) -> None:
    conn.execute("DELETE FROM item_metadata WHERE id = ?", (row_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Stats / utility
# ---------------------------------------------------------------------------

def get_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for all tables."""
    tables = ["transactions", "payee_normalization", "payee_metadata",
              "categories", "source_file_map", "column_templates", "processed_files",
              "item_metadata"]
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
