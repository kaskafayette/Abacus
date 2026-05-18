"""Generalized enrichment pipeline for detail files (Venmo, Amazon, ...).

Enrichment files don't create new transactions — they patch existing Chase
rows with detail that the Chase statement doesn't include (e.g. who the real
Venmo recipient was, or what items were in an Amazon order).

Folder layout:
  input/    — drop zone for all files
  pending/  — enrichment files whose records haven't all matched yet;
              retried every Ingest run
  processed/ — files that have either fully ingested (transactions) or
              fully resolved (all expected enrichment records matched)

Each enrich-type source registers itself with @register_enricher. Adding a
new source = adding one parser function + an entry in placeholders.py.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from db import queries

PENDING_DIR = Path(__file__).resolve().parent.parent / "pending"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "processed"
INPUT_DIR = Path(__file__).resolve().parent.parent / "input"


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class EnrichmentRecord:
    """One record extracted from an enrichment file.

    `expected_to_match` is False when we know up-front that no Chase row will
    correspond (e.g. a Venmo card swipe funded from the Venmo balance, not
    from a Chase account). These records are informational; they don't block
    a file from moving to processed/.
    """
    match_key: dict           # strategy-specific match data
    payee_hint: str | None
    note_hint: str | None
    via_hint: str | None
    expected_to_match: bool = True
    raw: dict = field(default_factory=dict)


@dataclass
class MatchProposal:
    """A single record matched (or attempted) against a Chase row."""
    record: EnrichmentRecord
    txn_id: int | None        # matched transaction id, None if unmatched
    txn_row: sqlite3.Row | None = None
    reason: str = ""          # why unmatched, or "applied", "skipped", etc.


@dataclass
class FileMatchSummary:
    """All proposals for one enrichment file."""
    filepath: Path
    proposals: list[MatchProposal]

    @property
    def matched(self) -> list[MatchProposal]:
        return [p for p in self.proposals if p.txn_id is not None]

    @property
    def unmatched_expected(self) -> list[MatchProposal]:
        return [p for p in self.proposals
                if p.txn_id is None and p.record.expected_to_match]

    @property
    def unmatched_unexpected(self) -> list[MatchProposal]:
        return [p for p in self.proposals
                if p.txn_id is None and not p.record.expected_to_match]

    @property
    def fully_resolved(self) -> bool:
        """True iff every expected_to_match record has a matched txn."""
        return len(self.unmatched_expected) == 0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, dict] = {}

ParserFn = Callable[[Path], list[EnrichmentRecord]]


def register_enricher(account_type: str, *, match_strategy: str, **kwargs):
    """Decorator. Registers a parser function for a given account_type.

    kwargs depend on the match strategy:
      - amount_date_window: match_source (str), match_description_like (str),
                            date_window_days (int)
      - order_ref: no extra kwargs
    """
    def decorator(fn: ParserFn) -> ParserFn:
        _REGISTRY[account_type] = {
            "parse": fn,
            "match_strategy": match_strategy,
            **kwargs,
        }
        return fn
    return decorator


def enricher_account_types() -> set[str]:
    """Set of account_type values that route through enrichment, not ingest."""
    return set(_REGISTRY.keys())


def is_enricher_kind(account_type: str | None) -> bool:
    return account_type in _REGISTRY


# ---------------------------------------------------------------------------
# Venmo parser
# ---------------------------------------------------------------------------

# Venmo CSV layout:
#   Row 1: "Account Statement - (@username)"  (banner)
#   Row 2: "Account Activity"                  (banner)
#   Row 3: column headers (with a leading empty column)
#   Row 4: opening balance marker (only Beginning Balance populated)
#   Rows 5..N-1: transaction rows (each starts with an empty cell, then ID)
#   Row N: closing balance + multi-line disclaimer text
_VENMO_BANNER_ROWS = 2

# Funding sources that resolve to a Chase account (so the corresponding Chase
# row should exist). Detected case-insensitively.
_CHASE_FUNDING_RE = re.compile(r"Chase.*\*\d{4}", re.IGNORECASE)


@register_enricher(
    "venmo_detail",
    match_strategy="amount_date_window",
    match_source="Chase5616",
    match_description_like="VENMO%PAYMENT%",
    date_window_days=4,
)
def parse_venmo(filepath: Path) -> list[EnrichmentRecord]:
    """Parse a Venmo monthly statement CSV into enrichment records.

    Skips banner rows, balance markers, and the closing disclaimer block.
    Emits one record per Complete transaction.
    """
    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        lines = f.readlines()

    # Skip the leading banner rows; csv.DictReader needs a real header row at line 0.
    body = "".join(lines[_VENMO_BANNER_ROWS:])
    reader = csv.DictReader(io.StringIO(body))

    records: list[EnrichmentRecord] = []
    for raw_row in reader:
        # Normalize whitespace and drop the leading-empty-column key
        row = {(k or "").strip(): (v.strip() if v else "")
               for k, v in raw_row.items() if k is not None}

        txn_id = row.get("ID", "")
        if not txn_id:
            continue
        if row.get("Status", "") != "Complete":
            continue

        raw_dt = row.get("Datetime", "")
        try:
            dt = datetime.strptime(raw_dt, "%Y-%m-%dT%H:%M:%S").date()
        except ValueError:
            continue

        amount = _parse_venmo_amount(row.get("Amount (total)", ""))
        if amount is None:
            continue

        from_name = row.get("From", "").strip()
        to_name = row.get("To", "").strip()
        # For outflows (negative amount), payee is the recipient (To).
        # For inflows, payee is the sender (From).
        counterparty_raw = to_name if amount < 0 else from_name
        payee = _clean_payee_name(counterparty_raw) if counterparty_raw else None

        note = row.get("Note", "").strip() or None
        funding = row.get("Funding Source", "")
        expected = bool(_CHASE_FUNDING_RE.search(funding))

        records.append(EnrichmentRecord(
            match_key={"amount": str(amount), "date": dt.isoformat()},
            payee_hint=payee,
            note_hint=note,
            via_hint="Venmo",
            expected_to_match=expected,
            raw={
                "venmo_id": txn_id,
                "type": row.get("Type", ""),
                "from": from_name,
                "to": to_name,
                "funding_source": funding,
            },
        ))

    return records


_AMOUNT_CLEAN_RE = re.compile(r"[^\d.\-]")


def _parse_venmo_amount(raw: str) -> Decimal | None:
    """Parse a Venmo amount like '- $34.50' or '- $1,500.00' into a Decimal.

    Negative = outflow (matches the bank-statement convention used by Abacus).
    """
    if not raw:
        return None
    cleaned = _AMOUNT_CLEAN_RE.sub("", raw)
    if not cleaned or cleaned in ("-", ".", "-."):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _clean_payee_name(name: str) -> str:
    """Title-case an all-caps Venmo counterparty; leave mixed case alone."""
    from processing.normalize import smart_title
    s = name.strip()
    if s and s == s.upper():
        s = smart_title(s)
    return s


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------

def compute_matches(conn: sqlite3.Connection, account_type: str,
                    records: list[EnrichmentRecord]) -> list[MatchProposal]:
    """Match records against transactions using the registered strategy."""
    spec = _REGISTRY[account_type]
    strategy = spec["match_strategy"]

    if strategy == "amount_date_window":
        return _match_amount_date_window(
            conn, records,
            source=spec["match_source"],
            desc_like=spec.get("match_description_like"),
            window_days=spec.get("date_window_days", 4),
        )
    elif strategy == "order_ref":
        return _match_order_ref(conn, records)
    else:
        raise ValueError(f"Unknown match strategy: {strategy}")


def _match_amount_date_window(conn: sqlite3.Connection,
                              records: list[EnrichmentRecord],
                              source: str,
                              desc_like: str | None,
                              window_days: int) -> list[MatchProposal]:
    """Match each record to the Chase row with the same amount and a date
    within ±window_days. Picks the closest date when multiple candidates exist.
    Skips Chase rows already claimed by another record in this batch.
    """
    proposals: list[MatchProposal] = []
    claimed: set[int] = set()

    for rec in records:
        if not rec.expected_to_match:
            proposals.append(MatchProposal(
                record=rec, txn_id=None,
                reason="no Chase match expected (Venmo balance funding)",
            ))
            continue

        amt = rec.match_key["amount"]
        rdate = date.fromisoformat(rec.match_key["date"])
        start = (rdate - timedelta(days=window_days)).isoformat()
        end = (rdate + timedelta(days=window_days)).isoformat()

        params: list = [source, amt, start, end]
        sql = (
            "SELECT * FROM transactions "
            "WHERE source = ? AND amount = ? AND date BETWEEN ? AND ?"
        )
        if desc_like:
            sql += " AND description_raw LIKE ?"
            params.append(desc_like)

        candidates = conn.execute(sql, params).fetchall()
        # Filter out already-claimed rows
        candidates = [c for c in candidates if c["id"] not in claimed]

        if not candidates:
            proposals.append(MatchProposal(
                record=rec, txn_id=None,
                reason=f"no Chase row found for ${amt} within ±{window_days} days of {rdate}",
            ))
            continue

        # Pick the candidate whose date is closest to the record date.
        def _dist(row):
            return abs((date.fromisoformat(row["date"]) - rdate).days)
        candidates.sort(key=_dist)
        best = candidates[0]
        claimed.add(best["id"])
        proposals.append(MatchProposal(
            record=rec, txn_id=best["id"], txn_row=best,
            reason="matched" if len(candidates) == 1
                   else f"matched (chose closest of {len(candidates)} candidates)",
        ))

    return proposals


def _match_order_ref(conn: sqlite3.Connection,
                     records: list[EnrichmentRecord]) -> list[MatchProposal]:
    """Match by `order_ref` field, which Chase ingest extracts from descriptions.

    Placeholder for Amazon. The Amazon parser sets record.match_key['order_ref'].
    """
    proposals: list[MatchProposal] = []
    for rec in records:
        if not rec.expected_to_match:
            proposals.append(MatchProposal(record=rec, txn_id=None,
                                           reason="no Chase match expected"))
            continue
        ref = rec.match_key.get("order_ref")
        if not ref:
            proposals.append(MatchProposal(record=rec, txn_id=None,
                                           reason="record has no order_ref"))
            continue
        row = conn.execute(
            "SELECT * FROM transactions WHERE order_ref = ?", (ref,)
        ).fetchone()
        if row:
            proposals.append(MatchProposal(record=rec, txn_id=row["id"],
                                           txn_row=row, reason="matched"))
        else:
            proposals.append(MatchProposal(
                record=rec, txn_id=None,
                reason=f"no Chase row found with order_ref={ref}",
            ))
    return proposals


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_matches(conn: sqlite3.Connection,
                  proposals: list[MatchProposal]) -> tuple[int, int]:
    """Patch matched Chase rows from their enrichment records.

    Idempotency contract: rows with `overridden = 1` are skipped (preserves
    user manual edits and previously-applied enrichments). Returns
    (applied_count, skipped_count).

    When the existing row was "awaiting enrichment" (payee=NULL with a known
    enrichment via like Venmo) OR had a placeholder payee (like "Amazon"),
    also clear category/subcategory/tax_flags so auto_apply_payee_metadata
    can fill in the per-payee defaults for the newly-real payee. Without this,
    a Chase row pre-categorized with the default (Cash for Venmo) would stay
    Cash even after the payee becomes Sylvia Vientulis who's actually
    categorized as Gifts.
    """
    from processing.placeholders import is_placeholder_payee, is_awaiting_enrichment

    applied = 0
    skipped = 0
    for prop in proposals:
        if prop.txn_id is None:
            continue
        txn = prop.txn_row or conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (prop.txn_id,)
        ).fetchone()
        if txn is None:
            continue
        # Skip only when overridden=1 AND there's already a real payee — that's
        # a row the user (or a prior enrichment) has filled in. A row with
        # overridden=1 but payee=NULL is the "awaiting enrichment" state from
        # the legacy "Venmo Payment" placeholder migration; safe to patch.
        if txn["overridden"] and txn["payee"]:
            skipped += 1
            prop.reason = "skipped (already overridden)"
            continue

        rec = prop.record
        updates: dict = {}
        if rec.payee_hint:
            updates["payee"] = rec.payee_hint
            # If the row was awaiting enrichment (payee=NULL with enrichment via)
            # or had a placeholder payee, the default category applied at ingest
            # is no longer appropriate — clear it so auto_apply_payee_metadata
            # can fill in the right one for the real payee.
            old_payee = txn["payee"]
            old_via = txn["via"]
            if is_placeholder_payee(old_payee) or is_awaiting_enrichment(old_payee, old_via):
                updates["category"] = None
                updates["subcategory"] = None
                updates["tax_flags"] = None
                # Also reset to pending — the prior 'confirmed' status was based
                # on the placeholder data; now that the real payee is known,
                # the row needs to be re-categorized in the Categorize tab.
                updates["status"] = "pending"
        if rec.via_hint:
            updates["via"] = rec.via_hint
        if rec.note_hint:
            existing = txn["note"]
            if existing and existing.strip():
                updates["note"] = f"{existing}\n[{rec.via_hint or 'Enrich'}: {rec.note_hint}]"
            else:
                updates["note"] = rec.note_hint
        if updates:
            updates["overridden"] = 1
            queries.update_transaction(conn, prop.txn_id, **updates)
            applied += 1
            prop.reason = "applied"

    # After patching, fire payee-metadata auto-categorize on touched rows so
    # known per-payee categorizations land immediately.
    touched_ids = [p.txn_id for p in proposals if p.txn_id is not None and p.reason == "applied"]
    if touched_ids:
        from processing.categorize import auto_apply_payee_metadata
        auto_apply_payee_metadata(conn, touched_ids)

    return applied, skipped


# ---------------------------------------------------------------------------
# Routing + pending-folder orchestration
# ---------------------------------------------------------------------------

def route_input_files(conn: sqlite3.Connection) -> dict:
    """Sort files in input/ into ingest-type vs. enrich-type.

    Moves enrich-type files to pending/. Leaves ingest-type files in input/.
    Files with an unknown prefix are reported back (the UI handles the
    account-continuity prompt for those).

    Returns:
      {
        "ingest": [filenames staying in input/],
        "enrich_moved": [filenames moved from input/ to pending/],
        "unknown": [(filename, prefix) tuples — need user attention],
        "unparseable": [filenames that don't match the naming pattern],
      }
    """
    from processing.ingest import parse_filename, INPUT_DIR as ING_INPUT_DIR

    PENDING_DIR.mkdir(exist_ok=True)
    ING_INPUT_DIR.mkdir(exist_ok=True)

    result = {"ingest": [], "enrich_moved": [], "unknown": [], "unparseable": []}

    files = sorted(f for f in ING_INPUT_DIR.iterdir() if f.suffix.lower() == ".csv")
    for fp in files:
        parsed = parse_filename(fp.name)
        if not parsed:
            result["unparseable"].append(fp.name)
            continue

        acct_type = queries.get_account_type(conn, parsed["prefix"])
        if acct_type is None:
            result["unknown"].append((fp.name, parsed["prefix"]))
            continue

        if is_enricher_kind(acct_type):
            dest = PENDING_DIR / fp.name
            # If same-name file already exists in pending (rare), overwrite —
            # hash dedup downstream will catch true duplicates.
            if dest.exists():
                dest.unlink()
            fp.rename(dest)
            result["enrich_moved"].append(fp.name)
        else:
            result["ingest"].append(fp.name)

    return result


def scan_pending(conn: sqlite3.Connection) -> list[FileMatchSummary]:
    """Parse every file in pending/ and compute its match summary.

    Used to build the user's enrichment preview before they confirm.
    """
    PENDING_DIR.mkdir(exist_ok=True)
    from processing.ingest import parse_filename

    summaries: list[FileMatchSummary] = []
    files = sorted(f for f in PENDING_DIR.iterdir() if f.suffix.lower() == ".csv")
    for fp in files:
        parsed = parse_filename(fp.name)
        if not parsed:
            continue
        acct_type = queries.get_account_type(conn, parsed["prefix"])
        if not is_enricher_kind(acct_type):
            continue
        spec = _REGISTRY[acct_type]
        try:
            records = spec["parse"](fp)
        except Exception as e:
            # Surface parse failures as a single-proposal summary so the UI
            # can show what went wrong without crashing the page.
            summaries.append(FileMatchSummary(
                filepath=fp,
                proposals=[MatchProposal(
                    record=EnrichmentRecord(
                        match_key={}, payee_hint=None, note_hint=None,
                        via_hint=None, expected_to_match=False,
                        raw={"error": str(e)},
                    ),
                    txn_id=None, reason=f"parse failed: {e}",
                )],
            ))
            continue

        proposals = compute_matches(conn, acct_type, records)
        summaries.append(FileMatchSummary(filepath=fp, proposals=proposals))

    return summaries


def commit_pending(conn: sqlite3.Connection,
                   summaries: list[FileMatchSummary]) -> dict:
    """Apply all matched proposals across all summaries. Move fully-resolved
    files from pending/ to processed/. Record them in processed_files.

    Returns a tally dict.
    """
    PROCESSED_DIR.mkdir(exist_ok=True)
    total_applied = 0
    total_skipped = 0
    moved = []
    kept = []

    for summary in summaries:
        applied, skipped = apply_matches(conn, summary.proposals)
        total_applied += applied
        total_skipped += skipped

        if summary.fully_resolved:
            # Record in processed_files (idempotent — skip if hash already there)
            file_hash = queries.compute_file_hash(summary.filepath)
            existing = queries.check_file_hash(conn, file_hash)
            if not existing:
                from processing.ingest import parse_filename
                parsed = parse_filename(summary.filepath.name)
                if parsed:
                    queries.record_processed_file(
                        conn,
                        filename=summary.filepath.name,
                        source_prefix=parsed["prefix"],
                        file_hash=file_hash,
                        date_start=parsed["start_date"].isoformat(),
                        date_end=parsed["end_date"].isoformat(),
                    )
            dest = PROCESSED_DIR / summary.filepath.name
            if dest.exists():
                dest.unlink()
            summary.filepath.rename(dest)
            moved.append(summary.filepath.name)
        else:
            kept.append(summary.filepath.name)

    return {
        "applied": total_applied,
        "skipped": total_skipped,
        "moved_to_processed": moved,
        "kept_in_pending": kept,
    }


def cleanup_pending(conn: sqlite3.Connection, older_than_days: int) -> list[str]:
    """Manual: move pending/ files whose oldest record date is older than the
    threshold into processed/ regardless of match state.

    Returns list of moved filenames. Records them in processed_files so file
    hash dedup picks them up if reintroduced.
    """
    from processing.ingest import parse_filename
    PROCESSED_DIR.mkdir(exist_ok=True)

    today = date.today()
    moved = []
    files = sorted(f for f in PENDING_DIR.iterdir() if f.suffix.lower() == ".csv")
    for fp in files:
        parsed = parse_filename(fp.name)
        if not parsed:
            continue
        age_days = (today - parsed["end_date"]).days
        if age_days < older_than_days:
            continue

        file_hash = queries.compute_file_hash(fp)
        if not queries.check_file_hash(conn, file_hash):
            queries.record_processed_file(
                conn,
                filename=fp.name,
                source_prefix=parsed["prefix"],
                file_hash=file_hash,
                date_start=parsed["start_date"].isoformat(),
                date_end=parsed["end_date"].isoformat(),
            )
        dest = PROCESSED_DIR / fp.name
        if dest.exists():
            dest.unlink()
        fp.rename(dest)
        moved.append(fp.name)
    return moved


def list_pending_status(conn: sqlite3.Connection) -> list[dict]:
    """Summary info for each file currently in pending/."""
    from processing.ingest import parse_filename
    PENDING_DIR.mkdir(exist_ok=True)
    today = date.today()
    out = []
    for fp in sorted(PENDING_DIR.iterdir()):
        if fp.suffix.lower() != ".csv":
            continue
        parsed = parse_filename(fp.name)
        if not parsed:
            continue
        out.append({
            "filename": fp.name,
            "prefix": parsed["prefix"],
            "start": parsed["start_date"].isoformat(),
            "end": parsed["end_date"].isoformat(),
            "age_days": (today - parsed["end_date"]).days,
        })
    return out
