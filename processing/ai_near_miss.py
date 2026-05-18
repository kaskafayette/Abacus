"""AI-assisted detection of near-miss duplicate payees.

Over time the same merchant can accumulate under slightly different normalized
names — e.g. "Martha's", "Martha Bros", "Martha's Coffee" all refer to one place.
This module asks Claude to scan the user's distinct payee list and propose
groups of likely-duplicate payees, with a suggested canonical name per group.

The user reviews each group and clicks Merge (which renames all variants to the
canonical) or Leave Separate. All merging uses the same plumbing as the existing
Rename / Merge Payees workflow in Maintenance.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Literal

from pydantic import BaseModel


class NearMissGroup(BaseModel):
    """A set of payees Claude thinks refer to the same real-world entity."""
    members: list[str]
    canonical: str  # must be one of the members
    confidence: Literal["high", "medium"]
    reasoning: str = ""


class NearMissBatch(BaseModel):
    groups: list[NearMissGroup]


SYSTEM_PROMPT = """\
You are a duplicate-detection assistant for a personal household bookkeeping
system. The user has a list of normalized payee names that has accumulated
over time. The same merchant can sometimes appear under slightly different
spellings because the user entered them differently in different months (e.g.
"Martha's", "Martha Bros", "Martha's Coffee" — all the same coffee shop).

Your task: identify GROUPS of payees that almost certainly refer to the same
real-world entity. For each group, pick a canonical name (must be one of the
members of the group — do NOT invent new names) and briefly explain your
reasoning.

STRICT RULES:

1. Only group payees you're confident are the same entity. Return confidence
   "high" when the match is unambiguous (clear typo, "X" vs "X's", "X" vs
   "X LLC"), "medium" when reasonable but not certain. Never return "low" —
   if you're not at least medium-confident, don't propose the group at all.
2. The canonical name MUST be one of the existing members verbatim — do not
   coin new spellings. The user can rename afterward if they want a different
   canonical form.
3. Personal names (humans, not businesses) are usually NOT duplicates unless
   the spelling is clearly a variant of the same person (e.g. "John Smith"
   vs "Smith, John" vs "John Q. Smith"). Different first names ARE different
   people. When in doubt with personal names, leave them separate.
4. Don't merge generic categories with specific stores ("Restaurant" should
   not merge with "Mario's Restaurant").
5. Don't merge merchants that share a common word but are clearly different
   businesses (e.g. "Whole Foods" and "Foods Co." — both have "Foods" but
   are separate companies).
6. Don't propose groups of size 1. A group needs at least 2 members.
7. A given payee can only appear in one group. Don't propose overlapping groups.
8. Return zero groups if nothing's clearly duplicated. False positives are
   worse than false negatives here.

OUTPUT:
- For each suspected duplicate group, return: members (list of names exactly
  as they appear in the input), canonical (one of the members), confidence,
  and reasoning (1-2 sentences explaining why these are the same entity).
"""


def find_near_miss_groups(conn: sqlite3.Connection
                           ) -> tuple[list[NearMissGroup], list[str]]:
    """Ask Claude to identify groups of near-miss duplicate payees.

    Returns:
        (valid_groups, warnings)
        - valid_groups: groups where every member is a real distinct payee
          and canonical is one of the members
        - warnings: any rejected groups (invalid canonical, unknown member,
          single-member, etc.) with reasons
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Get a key at console.anthropic.com "
            "and set it as a system environment variable, then restart Streamlit."
        )

    # Pull distinct payees actually in use, with transaction counts so the user
    # can sanity-check the suggested groups against scale.
    rows = conn.execute("""
        SELECT payee, COUNT(*) AS cnt
        FROM transactions
        WHERE payee IS NOT NULL AND payee != ''
        GROUP BY payee
        ORDER BY payee
    """).fetchall()

    if len(rows) < 2:
        return [], []

    payees = [r["payee"] for r in rows]
    valid_payee_set = set(payees)

    # Build user message: numbered list of payees with transaction counts
    payee_lines = [f"{i}. {r['payee']!r}  ({r['cnt']} transactions)"
                   for i, r in enumerate(rows, 1)]
    user_msg = (
        f"Here are {len(payees)} distinct payee names. Identify any groups "
        f"that look like duplicates of the same real-world entity.\n\n"
        + "\n".join(payee_lines)
    )

    from anthropic import Anthropic
    client = Anthropic()
    response = client.messages.parse(
        model="claude-opus-4-7",
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
        output_format=NearMissBatch,
        thinking={"type": "adaptive"},
    )

    batch = response.parsed_output
    if batch is None:
        raise RuntimeError(
            f"Claude returned a refusal or non-structured response. "
            f"stop_reason={response.stop_reason}"
        )

    # Validate each group:
    # - all members must be real distinct payees
    # - canonical must be one of the members
    # - group must have at least 2 members
    # - no payee appears in multiple groups
    valid_groups: list[NearMissGroup] = []
    warnings: list[str] = []
    seen_payees: set[str] = set()

    for g in batch.groups:
        if len(g.members) < 2:
            warnings.append(
                f"Skipped a group with only {len(g.members)} member(s) "
                f"(canonical={g.canonical!r}). Need at least 2."
            )
            continue

        unknown = [m for m in g.members if m not in valid_payee_set]
        if unknown:
            warnings.append(
                f"Skipped group {g.members!r}: member(s) {unknown!r} aren't "
                f"in the current payee list."
            )
            continue

        if g.canonical not in g.members:
            warnings.append(
                f"Skipped group {g.members!r}: canonical {g.canonical!r} "
                f"isn't one of the members."
            )
            continue

        overlap = [m for m in g.members if m in seen_payees]
        if overlap:
            warnings.append(
                f"Skipped group {g.members!r}: payee(s) {overlap!r} already "
                f"appear in an earlier group."
            )
            continue

        seen_payees.update(g.members)
        valid_groups.append(g)

    return valid_groups, warnings
