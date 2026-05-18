"""AI-assisted classification of payees into Abacus categories.

Uses Claude (via the Anthropic API) to suggest a category and subcategory for
a list of payee names. The suggestions land on the relevant transactions as
pending values — the user reviews and confirms in the Categorize tab.

Setup: set the ANTHROPIC_API_KEY environment variable to your key from
console.anthropic.com. A small balance (~$5) covers years of typical use.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Literal

from pydantic import BaseModel

from db import queries


# Static usage guidance — boundaries, policy rules, examples. The actual list
# of categories is built dynamically from the DB at call time so the prompt
# always reflects the user's real category set, including any edits made via
# Maintenance → Category Master.
USAGE_GUIDANCE = """\
Guidance for picking categories:

- Use the payee name as the strongest signal. Real merchant names are usually
  obvious (Whole Foods → Household / Groceries; CVS Pharmacy → Medical /
  Prescription Medicines; Vuori → Household / Clothing).

- **Shopping is a last-resort residual bucket** — only use it when nothing more
  specific fits. Before falling back to Shopping, try:
  - A clothing retailer (J. Crew, Vuori, Athleta) → Household / Clothing
  - Hardware, screws, tools, paint → Household / Maint & Supplies
  - Furniture, decor, lamps → Household / Furnishings
  - Books → Fun / Tickets and Other
  - Beauty/skincare/personal care products → Health and Wellness
  - Bedding, kitchenware, housewares → Household / Furnishings or Maint & Supplies
  Only choose Shopping when the merchant is genuinely a generic online
  marketplace you can't pin down (e.g. a no-context Amazon order before
  enrichment fills in the items).

- Health and Wellness vs Medical: Medical is for healthcare proper (drugs,
  doctor visits, insurance premiums, hospitals, devices, therapists). Health
  and Wellness is for everything else body/self-care related — spa,
  fitness/gym, personal care products, hair, etc. Anything you'd file a tax
  receipt for goes to Medical.

- Household / Insurance is for homeowners/auto/umbrella insurance. Medical
  insurance goes to Medical / Medical Insurance.

- Household / Subscriptions is for personal streaming/software. Business
  software goes to Bsns Expense NEC / Subscriptions and Services.

- Transfer is for money moving between the user's own accounts (e.g. a
  checking-to-credit-card payment, a transfer between bank and brokerage).
  Both sides of the same internal movement go here.

- Cash → Deposited or Withdrawn is for actual ATM cash movements. Generic
  Venmo outflows where the real recipient is unknown ALSO go here by default
  (until enrichment fills in the real payee). It's NOT for retail purchases —
  those go to the appropriate Household or other category.

- Payments / Venmo and Zelle should rarely be the final answer. It's a
  placeholder for Venmo/Zelle transactions whose real purpose isn't yet known.
  If the note or payee makes the real purpose clear (e.g. payment to a
  therapist, a gift to a relative, a restaurant bill share), categorize there
  instead.

- Donations: "Deductible - Cash" for cash gifts to qualifying 501(c)(3)
  charities. "Deductible - Merchandise" for in-kind donations. "Non-Deductible"
  for political contributions (ActBlue, candidates), gifts to individuals not
  registered as charities, etc.

- Personal names (e.g. "Sylvia Vientulis") need context. If the transaction
  has a note giving a clue (note: "Therapy" → Medical / Therapists; note:
  "Birthday gift" → Household / Gifts), use it. Without context, return
  confidence="low" — don't guess high-confidence at unknown people.

- Always return a category. Subcategory is optional — leave blank when
  unsure between subcategories of the right top-level category.

- Use ONLY the exact category and subcategory names from the authoritative
  list above. Do not invent new categories or paraphrase ("Maintenance" is
  wrong — it's "Maint & Supplies"). If you can't find a fitting subcategory,
  leave it blank rather than making one up.
"""


def _build_system_prompt(conn: sqlite3.Connection) -> str:
    """Build the system prompt from the live categories table plus static guidance.

    Pulling categories from the DB rather than hardcoding them ensures the AI
    always sees the user's actual current category set — including subcategories
    added in Maintenance → Category Master.
    """
    rows = conn.execute(
        "SELECT category, subcategory FROM categories ORDER BY category, subcategory"
    ).fetchall()

    from collections import defaultdict
    groups: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        cat = r["category"]
        sub = r["subcategory"]
        if sub:
            groups[cat].append(sub)
        else:
            groups.setdefault(cat, [])  # ensure key exists even if no subs

    lines = [
        "Abacus is a personal household bookkeeping tool. Each transaction has a",
        "payee (merchant or person) and must be assigned a category — and optionally",
        "a subcategory — from the AUTHORITATIVE list below. These come directly from",
        "the user's database; do not invent new categories or paraphrase names.",
        "",
        "Authoritative category list:",
        "",
    ]
    for cat in sorted(groups.keys()):
        subs = groups[cat]
        if subs:
            sub_list = ", ".join(f'"{s}"' for s in subs)
            lines.append(f'- "{cat}": {sub_list}')
        else:
            lines.append(f'- "{cat}": (no subcategories)')
    lines.append("")
    lines.append(USAGE_GUIDANCE)
    return "\n".join(lines)


class PayeeSuggestion(BaseModel):
    payee: str
    category: str
    subcategory: str = ""
    confidence: Literal["high", "medium", "low"]
    reasoning: str = ""


class SuggestionBatch(BaseModel):
    suggestions: list[PayeeSuggestion]


def _get_valid_categories(conn: sqlite3.Connection) -> tuple[set[str], set[tuple[str, str | None]]]:
    """Fetch the set of valid (category) names and (category, subcategory) pairs
    from the live DB. Used to validate Claude's output before applying it.
    """
    rows = conn.execute("SELECT category, subcategory FROM categories").fetchall()
    valid_categories = {r["category"] for r in rows}
    # Each (category, subcategory) pair, with NULL subcategory represented as None
    valid_pairs = {(r["category"], r["subcategory"]) for r in rows}
    return valid_categories, valid_pairs


def _validate_suggestion(s: PayeeSuggestion,
                          valid_categories: set[str],
                          valid_pairs: set[tuple[str, str | None]]) -> tuple[PayeeSuggestion | None, str | None]:
    """Validate a single suggestion against the DB.

    Returns (cleaned_suggestion, warning_message). If the category is invalid,
    cleaned_suggestion is None — the row should not be patched. If only the
    subcategory is invalid, it gets cleared and a warning is returned.
    """
    if s.category not in valid_categories:
        return None, (
            f"Rejected suggestion for {s.payee!r}: category {s.category!r} "
            f"is not in the database. (Subcategory was {s.subcategory!r}.)"
        )

    # Empty-string subcategory means "no subcategory selected"; check against NULL pair
    sub = s.subcategory if s.subcategory else None
    if (s.category, sub) not in valid_pairs:
        # Category exists but this subcategory doesn't pair with it.
        # Keep the category, drop the subcategory.
        cleaned = s.model_copy(update={"subcategory": ""})
        return cleaned, (
            f"Warning for {s.payee!r}: subcategory {s.subcategory!r} is not "
            f"valid under {s.category!r}. Subcategory cleared; category kept."
        )

    return s, None


def suggest_payee_categories(conn: sqlite3.Connection,
                              payee_names: list[str],
                              extra_context: dict[str, str] | None = None
                              ) -> tuple[list[PayeeSuggestion], list[str]]:
    """Call Claude to classify payees into the user's actual Abacus categories.

    The system prompt is built dynamically from the live `categories` table so
    the model only ever sees the user's real set. After Claude returns, every
    suggestion is validated against the same DB list — any suggestion with a
    category that doesn't exist is rejected outright; a suggestion with a
    nonexistent subcategory has the subcategory cleared (category preserved).
    Caller never sees an invalid (category, subcategory) pair.

    Args:
        conn: SQLite connection (used to fetch the authoritative category list)
        payee_names: payees that lack a category. Should be deduplicated.
        extra_context: optional per-payee hints (e.g. transaction notes).
            Keyed by payee name. Helps Claude make better guesses when the
            payee name alone is ambiguous.

    Returns:
        (valid_suggestions, warnings)
        - valid_suggestions: PayeeSuggestion objects safe to apply (category
          guaranteed to exist; subcategory either valid or empty)
        - warnings: human-readable strings describing every rejection or
          subcategory clearing, for surfacing in the UI

    Raises:
        RuntimeError: if ANTHROPIC_API_KEY is not set or Claude refuses.
    """
    if not payee_names:
        return [], []

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Get a key at console.anthropic.com "
            "and set it as a system environment variable, then restart Streamlit."
        )

    from anthropic import Anthropic
    client = Anthropic()

    system_prompt = _build_system_prompt(conn)

    # Build the user message: numbered list of payees with optional context
    lines = []
    for i, name in enumerate(payee_names, 1):
        line = f"{i}. {name}"
        if extra_context and name in extra_context:
            hint = extra_context[name].strip()
            if hint:
                line += f"   (note: {hint[:200]})"
        lines.append(line)
    user_msg = (
        "Classify each of these payees into one of the Abacus categories. "
        "Return one suggestion per payee, in order. Use ONLY exact category "
        "and subcategory names from the authoritative list in the system "
        "prompt above — do not invent or paraphrase.\n\n"
        + "\n".join(lines)
    )

    response = client.messages.parse(
        model="claude-opus-4-7",
        max_tokens=16000,
        # cache_control is a no-op below the 4K-token threshold (Opus 4.7) but
        # kicks in automatically if the category list grows large enough.
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
        output_format=SuggestionBatch,
        thinking={"type": "adaptive"},
    )

    batch = response.parsed_output
    if batch is None:
        raise RuntimeError(
            "Claude returned a refusal or could not produce a structured response. "
            f"stop_reason={response.stop_reason}"
        )

    # Validate every suggestion against the live DB. Reject anything that
    # would result in an invalid category; clear bad subcategories.
    valid_categories, valid_pairs = _get_valid_categories(conn)
    cleaned: list[PayeeSuggestion] = []
    warnings: list[str] = []
    for s in batch.suggestions:
        result, warn = _validate_suggestion(s, valid_categories, valid_pairs)
        if warn:
            warnings.append(warn)
        if result is not None:
            cleaned.append(result)

    return cleaned, warnings


# ---------------------------------------------------------------------------
# Higher-level helper used by the UI
# ---------------------------------------------------------------------------

def classify_pending_unmapped(conn: sqlite3.Connection) -> dict:
    """Classify every pending transaction's payee that lacks a category.

    Deduplicates by payee so each unique payee gets one Claude call's worth
    of suggestion regardless of how many transactions share that payee.

    Returns:
        {
          "suggestions": [PayeeSuggestion, ...],
          "applied": int,            # number of transactions updated
          "payees_classified": int,  # number of unique payees Claude processed
        }
    """
    rows = conn.execute("""
        SELECT id, payee, category, note
        FROM transactions
        WHERE status = 'pending'
          AND payee IS NOT NULL
          AND payee != ''
          AND category IS NULL
    """).fetchall()

    if not rows:
        return {"suggestions": [], "applied": 0, "payees_classified": 0, "warnings": []}

    # Group by payee
    by_payee: dict[str, list] = {}
    notes_by_payee: dict[str, list[str]] = {}
    for r in rows:
        by_payee.setdefault(r["payee"], []).append(r["id"])
        if r["note"]:
            notes_by_payee.setdefault(r["payee"], []).append(r["note"])

    # Build per-payee context strings from notes (most informative for Venmo)
    extra_context = {
        payee: " / ".join(set(notes))[:200]
        for payee, notes in notes_by_payee.items()
    }

    payee_names = sorted(by_payee.keys())
    suggestions, warnings = suggest_payee_categories(
        conn, payee_names, extra_context=extra_context
    )

    # Apply (already-validated) suggestions to transactions. Each row gets an
    # [AI: ...] marker in the note so the user can see which categories were
    # AI-suggested vs. manually set.
    applied = 0
    for s in suggestions:
        txn_ids = by_payee.get(s.payee, [])
        if not txn_ids:
            continue
        for tid in txn_ids:
            updates = {
                "category": s.category,
                "subcategory": s.subcategory or None,
            }
            existing_row = conn.execute(
                "SELECT note FROM transactions WHERE id = ?", (tid,)
            ).fetchone()
            existing_note = existing_row["note"] if existing_row else None
            marker = f"[AI: {s.confidence} confidence — {s.reasoning[:120]}]"
            updates["note"] = (
                f"{existing_note}\n{marker}" if existing_note else marker
            )
            queries.update_transaction(conn, tid, **updates)
            applied += 1

    return {
        "suggestions": suggestions,
        "applied": applied,
        "payees_classified": len(payee_names),
        "warnings": warnings,
    }
