"""Step 4: category assignment and tax flagging."""

import sqlite3

from db import queries


def auto_categorize(conn: sqlite3.Connection, transaction_ids: list[int] | None = None):
    """Apply category/subcategory/tax_flags from payee_metadata for pending transactions.

    If transaction_ids is given, only categorize those rows.
    Otherwise categorizes all pending transactions that have a payee but no category.

    Returns count of auto-categorized transactions.
    """
    if transaction_ids:
        placeholders = ",".join("?" * len(transaction_ids))
        rows = conn.execute(
            f"SELECT * FROM transactions WHERE id IN ({placeholders})",
            transaction_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE payee IS NOT NULL "
            "AND category IS NULL AND status = 'pending'"
        ).fetchall()

    count = 0
    for row in rows:
        meta = queries.get_payee_metadata_by_name(conn, row["payee"])
        if meta:
            updates = {}
            if meta["category_override"]:
                updates["category"] = meta["category_override"]
            if meta["subcategory_override"]:
                updates["subcategory"] = meta["subcategory_override"]
            if meta["tax_flags_override"]:
                updates["tax_flags"] = meta["tax_flags_override"]
            if meta["payor"]:
                updates["payor"] = meta["payor"]

            if updates:
                queries.update_transaction(conn, row["id"], **updates)
                count += 1

    return count


def apply_category_edits(conn: sqlite3.Connection, edits: list[dict]) -> None:
    """Apply user edits from the category review table.

    Each edit dict should have: id (transaction id), plus any of:
    category, subcategory, tax_flags, payor, note, status.

    If the user changed any field from the auto-assigned default,
    overridden is set to True.
    """
    for edit in edits:
        txn_id = edit.pop("id")
        if not edit:
            continue

        # If any categorization field was provided, mark as overridden
        cat_fields = {"category", "subcategory", "tax_flags", "payor", "note"}
        if any(k in edit for k in cat_fields):
            edit["overridden"] = 1

        # If status not explicitly set, mark confirmed if category is present
        if "status" not in edit and edit.get("category"):
            edit["status"] = "confirmed"

        queries.update_transaction(conn, txn_id, **edit)


def save_payee_defaults(conn: sqlite3.Connection, edits: list[dict]) -> None:
    """Save category assignments as payee_metadata defaults for future use.

    Each edit dict should have: normalized_name, category, subcategory,
    tax_flags (optional), payor (optional), note (optional).
    """
    for edit in edits:
        name = edit.get("normalized_name")
        if not name:
            continue
        queries.upsert_payee_metadata(
            conn,
            normalized_name=name,
            category_override=edit.get("category"),
            subcategory_override=edit.get("subcategory"),
            tax_flags_override=edit.get("tax_flags"),
            payor=edit.get("payor"),
            note=edit.get("note"),
        )
