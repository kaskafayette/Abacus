"""Placeholder payees and their default categorizations.

Some Chase descriptions normalize to payees that represent a *category* of
transaction rather than a real entity ("Venmo Payment" maps to many different
real recipients; "Amazon" maps to many different real merchants behind the
storefront). Three rules apply to these:

  1. At ingest time, Chase rows matching a placeholder pattern get a default
     category so they're never sitting uncategorized while we wait for an
     enrichment file that may or may not arrive.
  2. In the Categorize UI, the payee field is locked — the user can't type a
     real payee name into a placeholder row because they don't have the data
     to know who the real payee is. Only enrichment fills these.
  3. `payee_metadata` is not written when a placeholder payee is categorized.
     A single placeholder represents many distinct real recipients, so
     generalizing would mis-categorize future transactions.

Adding a new enrichment source = adding the placeholder payee name to
PLACEHOLDER_PAYEES (so rules 2 & 3 apply) plus an entry in
DEFAULT_CATEGORY_PATTERNS (so rule 1 applies).
"""

import re

# Normalized payee names that represent a category, not a real entity.
# Matches against `transactions.payee` (after normalization).
#
# Note: Venmo intentionally has no placeholder payee. Chase Venmo rows land
# with payee=NULL and via="Venmo" — the via field identifies them, and the
# payee field stays empty until enrichment fills it with the real recipient.
PLACEHOLDER_PAYEES = frozenset({
    "Amazon",
})


# Patterns used at ingest time to recognize Chase rows that should get
# default values applied. Each entry:
#   - pattern: case-insensitive regex applied to description_raw
#   - category, subcategory, tax_flags: defaults
#   - via: identifier set so the row is recognizable even without a payee.
#     Used for Venmo (where payee stays NULL until enrichment).
#
# Categories must exist in the categories table (see db/schema.SEED_CATEGORIES).
DEFAULT_CATEGORY_PATTERNS = [
    {
        "pattern": re.compile(r"VENMO\s+PAYMENT", re.IGNORECASE),
        "category": "Cash",
        "subcategory": "Deposited or Withdrawn",
        "tax_flags": None,
        "via": "Venmo",
    },
    {
        "pattern": re.compile(r"Amazon\.com\*|AMAZON\s+MKTPL", re.IGNORECASE),
        "category": "Shopping",
        "subcategory": None,
        "tax_flags": None,
        "via": None,
    },
]


# Via values that indicate a row is waiting on an enrichment file to fill in
# the real payee. Rows with these `via` values and a NULL payee are filtered
# out of the normalization and categorization workflows (they need a Venmo
# file, not a user-typed payee).
ENRICHMENT_VIAS = frozenset({"Venmo"})


def is_placeholder_payee(payee: str | None) -> bool:
    """True if this normalized payee name is a placeholder for enrichment."""
    return payee is not None and payee in PLACEHOLDER_PAYEES


def is_awaiting_enrichment(payee: str | None, via: str | None) -> bool:
    """True if this row's real payee will come from an enrichment file (Venmo).

    Used by the UI to filter "waiting on Venmo" rows out of the normalization
    and category-assignment workflows.
    """
    return (payee is None or payee == "") and via in ENRICHMENT_VIAS


def default_category_for(description_raw: str) -> dict | None:
    """Return {'category', 'subcategory', 'tax_flags', 'via'} if the
    description matches a default-category pattern; None otherwise.
    """
    if not description_raw:
        return None
    for entry in DEFAULT_CATEGORY_PATTERNS:
        if entry["pattern"].search(description_raw):
            return {
                "category": entry["category"],
                "subcategory": entry["subcategory"],
                "tax_flags": entry["tax_flags"],
                "via": entry["via"],
            }
    return None
