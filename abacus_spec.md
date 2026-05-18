# Abacus - Software Specification

## Overview

Abacus is a personal bookkeeping tool for a household with both personal and business expenses. It ingests monthly bank and credit card CSV exports, normalizes and categorizes transactions using configurable lookup tables, and produces PDF and Excel reports. The user interacts through a Streamlit web UI. All data is stored locally in SQLite.

---

## Technology Stack

| Layer | Technology |
|---|---|
| UI | Streamlit |
| Backend / scripting | Python |
| Database | SQLite (local file, `check_same_thread=False` for Streamlit compatibility) |
| Report output | PDF via fpdf2, Excel (.xlsx) via openpyxl |

---

## Database Schema

### `transactions`
Stores all processed transactions. One row per transaction.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `date` | DATE | Post date (not transaction date) |
| `amount` | DECIMAL | Negative = money out, positive = money in (bank statement convention) |
| `check_number` | TEXT | Check number if applicable, otherwise NULL |
| `description_raw` | TEXT | Original text from source file |
| `category_raw` | TEXT | Category suggested by source file (e.g. Chase CC "Category"), preserved for reference only |
| `payee` | TEXT | Normalized payee name |
| `via` | TEXT | Payment intermediary (Square, Toast, Shopify, Venmo, Zelle, Chase BillPay, etc.) |
| `payor` | TEXT | Person within household (David / Debra / Both / Unknown) |
| `category` | TEXT | |
| `subcategory` | TEXT | |
| `tax_flags` | TEXT | Comma-separated flags, or NULL |
| `note` | TEXT | Free-text note |
| `order_ref` | TEXT | Order/reference code extracted from description (e.g. Amazon order IDs), for future matching |
| `source` | TEXT | Account label from `source_file_map` (e.g. "Chase5616", "Chase7625-4669") |
| `status` | TEXT | `"pending"` = imported but not fully confirmed; `"confirmed"` = all fields resolved; `"needs_review"` = user flagged for follow-up |
| `overridden` | BOOLEAN | True if any field was manually edited |

---

### `payee_normalization`
Maps raw description strings to normalized payee names.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `search_pattern` | TEXT | Case-insensitive substring match |
| `normalized_name` | TEXT | Canonical payee name |
| `payee_suffix` | TEXT | Order/suffix info stripped during normalization |

Multiple search patterns may map to the same `normalized_name`. The system ships with ~180 starter rules covering common payees.

---

### `payee_metadata`
Maps normalized payee names to category and tax metadata. Saved automatically when a user categorizes a payee; reused for auto-categorization in future months.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `normalized_name` | TEXT | UNIQUE |
| `category_override` | TEXT | NULL = not set |
| `subcategory_override` | TEXT | NULL = not set |
| `tax_flags_override` | TEXT | NULL = not set |
| `payor` | TEXT | Default payor for this payee |
| `note` | TEXT | |

---

### `categories`
Master list of categories and subcategories with tax defaults.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `category` | TEXT | |
| `subcategory` | TEXT | |
| `tax_flag_default` | TEXT | Default tax flag(s), or NULL |

UNIQUE constraint on (category, subcategory).

---

### `source_file_map`
Maps filename prefix to account metadata. The filename convention is `<prefix> MM-DD-YYYY to MM-DD-YYYY.csv`, e.g. `Chase5616 01-01-2026 to 02-28-2026.csv`. Extra whitespace between the prefix and the date range is tolerated. File extension matching is case-insensitive.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `source_prefix` | TEXT | UNIQUE. e.g. `"Chase5616"`, `"Chase7625-4669"` |
| `source_label` | TEXT | Account name, typically same as prefix |
| `nickname` | TEXT | Human-friendly name, e.g. `"Chase Sapphire Preferred"` |
| `account_type` | TEXT | `"checking"` or `"credit_card"` |

---

### `column_templates`
Stores a parsing template per source account. Created interactively on first encounter of a new prefix; reused silently thereafter.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `source_prefix` | TEXT | UNIQUE. FK to source_file_map |
| `date_column` | TEXT | CSV column name for **post date**. The UI warns if "Transaction Date" is selected instead. |
| `check_number_column` | TEXT | CSV column name for check number, or NULL |
| `date_format` | TEXT | Python strptime string, e.g. `"%m/%d/%Y"` |
| `amount_mode` | TEXT | `"single"` or `"split"` |
| `amount_column` | TEXT | Column name if amount_mode = "single" |
| `debit_column` | TEXT | Column name if amount_mode = "split" |
| `credit_column` | TEXT | Column name if amount_mode = "split" |
| `description_column` | TEXT | CSV column name for raw description |
| `category_raw_column` | TEXT | CSV column for source-provided category, or NULL |
| `sign_convention` | TEXT | `"negative_is_debit"` or `"positive_is_debit"` - single-column mode only |
| `card_column` | TEXT | CSV column that identifies the card/sub-account within a single file, or NULL. When present, each distinct value produces a separate source (e.g. prefix `Chase7625` with card value `4669` -> source `Chase7625-4669`). |

---

### `processed_files`
Records every source file that has been successfully ingested.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `filename` | TEXT | Original filename |
| `source_prefix` | TEXT | FK to source_file_map |
| `file_hash` | TEXT | SHA-256 hash of file contents |
| `date_range_start` | DATE | Start date parsed from filename |
| `date_range_end` | DATE | End date parsed from filename |
| `ingested_at` | DATETIME | Timestamp of successful ingestion |

---

### `db_audit_log`
Records administrative actions (e.g. purge).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `action` | TEXT | e.g. "PURGE_TRANSACTIONS" |
| `detail` | TEXT | |
| `timestamp` | DATETIME | |

---

## Category Taxonomy

### General Account Categories

| Category | Subcategories |
|---|---|
| Income | Social Security (net), W2 and 1099 (net) |
| Transfer | *(no subcategories)* |
| Household | Groceries, Utilities, Auto, Mortgage, Maint & Supplies, Subscriptions, Capital Improvements, Office, Gifts, Insurance, Furnishings |
| Fun | Restaurants, Tickets and Other, Travel |
| Health and Wellness | *(no subcategories)* |
| Medical | Prescription Medicines, Medical Insurance, Annual Plan Charges, Medical Devices and Supplies, Long Term Care, Travel and Lodging, Hospitals, Lab Fees, Doctors and Dentists, Therapists, Misc Medical |
| Bsns Expense NEC | Travel, Meals, Subcontractors, Office Equipment, Subscriptions and Services, Miscellaneous, Bsns Exp Reimbursable, Bsns Exp Non-reimbursable, Bsns Exp Reimbursement, BUH Deductible Interest, BUH Real Estate Tax, BUH Insurance, BUH Repairs and Maintenance, BUH Utilities |
| Shopping | *(no subcategories)* |
| Payments | Venmo and Zelle |
| Cash | Deposited or Withdrawn |
| Donations | Deductible - Cash, Deductible - Merchandise, Non-Deductible |
| Investment Expense | Taxes Paid or Refunded, Profl Services, Safe Deposit, Financial Subscriptions, Bank and CC Fees, Personal Property, Vehicle Taxes |

**Transfer** is a top-level category for money moving between accounts the user owns (e.g. a checking account payment to a credit card). Both sides of the movement should be categorized as Transfer. Reports exclude transfers from summaries so they do not distort spending totals.

### Tax Flags
Tax flags are boolean tags orthogonal to the category hierarchy. They are set at the category level (default), payee level (override), or transaction level (override). Flags include:

- Tax-reportable
- Reimbursable
- Capital Improvements
- Home Office
- Donations - Deductible
- Medical
- Business Expense

---

## Application Structure

The application is split into separate concerns, each accessible from the sidebar navigation:

### Sidebar Navigation
- **Home** - Database status and pending transaction count
- **Browse / Search** - View, search, and filter all transactions
- **Ingest** - File validation, template setup, and CSV ingestion
- **Normalize & Categorize** - Payee normalization and category assignment (resumable)
- **Reports** - Monthly reports, ad-hoc reports, and Excel export
- **Maintenance** - CRUD for all lookup tables, transaction editing, database admin

The sidebar always shows the database filename and a count of pending transactions if any exist.

---

## Ingest

A two-step workflow for importing CSV files.

### Step 1 - File Validation

1. An **Instructions** dropdown at the top explains the filename format and where to put files.
2. **Check Input Files** scans the `input/` folder, lists all valid CSV files found, and auto-sets the processing period dates from the filenames.
3. **Validate Files** runs full validation checks (filename format, prefix lookup, date range match, file hash deduplication, date continuity).
4. Files needing a new template are flagged and handled in Step 2.
5. **Preview Ingestion** parses all files using their templates and displays a scrollable preview table (Date, Source, Description, Amount, Check #, Category raw, Order Ref) for the user to verify before committing.
6. **Commit Ingestion** writes transactions to the database and moves source files to `processed/`.
7. **Cancel** returns to the validation screen without writing anything.

### Step 2 - Template Setup (new accounts only)

For each unknown prefix, the user sees:
- All CSV rows in a scrollable preview (full file, not just first few rows)
- **Account name** (locked to prefix, not editable)
- **Nickname** (e.g. "Chase Sapphire Preferred")
- **Account type** (checking or credit_card)
- Column mapping form: post date (with warning against selecting Transaction Date), check number, amount mode, description, category raw column, card column
- **Save Template** button

For multi-card files (card_column set), sub-source entries are auto-created in source_file_map.

### CSV Parsing

- Trailing commas in CSV files are stripped (Chase exports include them)
- File extension matching is case-insensitive (.csv, .CSV, .Csv all work)
- Order/reference codes are extracted from descriptions (Amazon, Etsy, Audible, etc.) and stored in `order_ref`

### Canonical Internal Format

Once ingested, every transaction is represented as:
- `date` - post date. Transaction date (if present) is discarded. All filtering and reporting uses post date only.
- `amount` - Decimal, negative = money out, positive = money in (bank statement convention)
- `description_raw` - raw string, untouched
- `check_number` - string or NULL
- `order_ref` - extracted reference code or NULL
- `source` - account label from source_file_map

---

## Normalize & Categorize

A resumable workflow accessible from the sidebar at any time. Split into two tabs.

### Payee Normalization Tab

1. On first run, ~180 starter normalization rules are seeded into `payee_normalization`.
2. Auto-matching runs against all pending transactions with no payee.
3. **Special-case extraction:** Zelle descriptions extract the recipient name (e.g. "Zelle payment to DAVID CLIFFORD JPM..." -> payee "David Clifford", via "Zelle"). Online Bill Payment descriptions extract the payee from "To <NAME>" (e.g. "Online Payment ... To STATE FARM INSURANCE" -> payee "State Farm Insurance", via "Chase BillPay").
4. **Auto-suggest:** Each unmatched description gets an automatically cleaned suggested name. The auto-suggest strips VIA prefixes (SQ *, TST*, AT *, etc.), trailing reference numbers, store numbers, dates, IDs, fixes HTML entities (`&amp;` -> `&`), and applies title case for all-caps names.
5. **Unmatched descriptions** are shown in a scrollable list, de-duplicated, sorted alphabetically by suggested name, with columns: Description, Count, Total ($), suggested Payee Name.
6. **Inline workflow per row:**
   - **Accept** - uses the suggested name as-is (or the edited version). Good for items that are already clean like "Atlas Cafe".
   - **Copy** - copies the raw description into the edit field for manual cleanup.
   - **Undo** - reverts an accepted item.
   - Accepted items show as struck-through with the accepted name in bold.
7. **Commit All** at the bottom saves all accepted items at once - creates normalization rules and updates transactions. No need to scroll up and down.
8. **Re-run All Normalization** - clears payee/via on all pending/needs_review transactions and re-applies all rules. Available even when all payees are matched.

### Category Assignment Tab

Uses AG Grid (streamlit-aggrid) for inline editing.

1. Auto-categorization applies saved `payee_metadata` defaults.
2. **View toggle:** "By Payee" (de-duplicated, one row per payee with count, total, and latest date) or "By Transaction" (every individual transaction).
3. **AG Grid** with inline editable columns:
   - Payee, # Txns, Total, Last Date (read-only)
   - Category (dropdown), Subcategory (dropdown, dynamically filtered by category), Tax Flags, Payor (dropdown), Note - all single-click editable
4. **Save Categorized** - commits every row that has a category set, auto-fills tax flags from category defaults if not manually set, saves payee_metadata defaults for future months. Rows without a category are left as pending.
5. **Set Rest to Needs Review** - marks all uncategorized rows as needs_review.
6. **Print Pending Items** - generates a PDF list of all pending transactions, sorted by payee.

---

## Browse / Search

A full-featured transaction viewer accessible from the sidebar.

- **Two search fields:** "Search in Payee" (payee field only) and "Search Anywhere" (searches across payee, description, note, category, subcategory, tax flags, source, via, payor)
- **Date presets:** All time, This month, Last month, This quarter, YTD, Last year, Custom
- **Filters:** Source dropdown, Status dropdown
- **Summary metrics:** Transaction count, Money Out total, Money In total
- **Export:** Download Excel and Download CSV buttons
- **Scrollable sortable table** (click column headers to sort)
- **Inline note editing:** Click a row to select it, edit the Note field below, click Save

---

## Reports

Three tabs: Monthly Report, Ad-Hoc Report, Excel Export.

### Monthly Report

- **Year/Month pickers** default to the last full month (e.g. running April 4 defaults to March)
- Shows confirmed transaction count for the month and YTD, warns if unconfirmed transactions exist
- **Checkboxes** to select which sections to include:

**Section 1 - Category Summary (Month & YTD)**
- Grouped by category with subcategories indented below
- Subtotal per category, grand total at bottom
- Transfers excluded from summaries (noted at bottom)

**Section 2 - Payee Summary (Month)**
- Grouped by category, then subcategory headers
- Each payee row shows count and total
- Subtotal per category, grand total

**Section 3 - Transaction Detail (Month)**
- Grouped by category/subcategory headers
- Columns: Date, Payee, Via, Amount, Payor, Note

**Section 4 - Tax Items (Month & YTD)**
- Grouped by tax flag
- Each flagged transaction listed with Date, Payee, Category, Amount, Note
- Month and YTD subtotals per tax flag

**Checksums Page** (always included)
- Database totals vs report totals for month and YTD
- Count and dollar amount cross-checks
- MATCH/MISMATCH indicator
- Breakdown by source account

### Ad-Hoc Report
- Arbitrary date range with same 4-section format
- Dates default to min/max transaction dates in the database

### Excel Export
- Arbitrary date range
- Single tab with all transaction fields, auto-filter, column widths

All reports saved to `output/`.

---

## Maintenance

Seven tabs. All editable tables use AG Grid with single-click inline editing and dropdown selectors.

### Source Accounts
- AG Grid: prefix, label (read-only), nickname and account_type (editable, dropdown for type)
- Save Changes button

### Payee Normalization
- AG Grid: search_pattern, normalized_name, payee_suffix - all editable inline
- Add New Rule form
- **Apply Rules to All Transactions** button - re-runs all normalization rules against every transaction (including confirmed), to fix inconsistencies after editing rules

### Payee Metadata
- AG Grid: normalized_name (read-only), category_override (dropdown), subcategory_override, tax_flags_override, payor (dropdown), note - all editable inline
- Add New form

### Category Master
- AG Grid: category, subcategory, tax_flag_default - all editable inline
- Add New Category form
- Warning that renaming does not cascade to existing transactions

### Rename / Merge Payees
- Shows all payees with transaction counts
- **Rename** mode: select a payee, type new name. Updates transactions, normalization rules, and payee_metadata in one click. Use for cleanup like "The Whitney" -> "Whitney".
- **Merge** mode: select two payees, pick which name to keep. All transactions from the second are reassigned. The kept name's metadata is preserved. Use for deduplication like "State Farm" + "State Farm Insurance" -> "State Farm Insurance".

### Edit Transactions
- Search, date range, source, and status filters
- AG Grid with pagination: payee, category (dropdown), subcategory, tax flags, payor (dropdown), note, status (dropdown) - all editable inline
- Save Changes button updates all edited rows

### Database
- File path, size, record counts per table
- **Purge Transaction Data** - deletes all transactions, preserves lookup tables, requires typing "DELETE ALL TRANSACTIONS" to confirm, logged to audit log

---

## Folder Structure

```
Abacus/
├── input/              <- User drops CSV files here each month
├── processed/          <- Source files moved here after successful ingestion
├── output/             <- Reports and exports deposited here
├── abacus.db           <- SQLite database
├── abacus.py           <- Streamlit entry point
├── requirements.txt    <- Python dependencies
├── processing/
│   ├── __init__.py
│   ├── ingest.py       <- File validation, template setup, CSV parsing
│   ├── normalize.py    <- Payee normalization with starter rules
│   ├── categorize.py   <- Category assignment
│   └── reports.py      <- PDF and Excel generation
├── db/
│   ├── __init__.py
│   ├── schema.py       <- Table definitions and seed data
│   └── queries.py      <- All DB access functions
└── ui/
    ├── __init__.py
    ├── browse.py       <- Browse / Search page
    ├── process.py      <- Ingest page (file validation + template setup)
    ├── normalize.py    <- Normalize & Categorize page
    ├── reports.py      <- Reports page
    └── maintenance.py  <- Maintenance pages
```

---

## Error Handling & Edge Cases

- **Duplicate transactions:** Two-layer check:
  1. **File-level:** SHA-256 hash checked against `processed_files`. Duplicate file rejected immediately.
  2. **Row-level:** Each transaction checked against existing rows using (date + source + amount + description_raw). Duplicates surfaced as errors.
- **Missing column template:** Triggers template setup flow; never guesses at column mappings.
- **Empty input folder:** Clear message displayed; pipeline does not run.
- **Database write failure:** Entire batch rolled back; no partial data written.
- **Report date validation:** Start > end caught, empty date ranges warned, report generation errors caught gracefully.
- **Unicode in PDFs:** Common unicode characters (em dash, smart quotes, etc.) replaced with ASCII equivalents; remaining characters handled via latin-1 fallback.
- **CSV trailing commas:** Stripped during parsing (Chase exports include them).
- **All validation errors shown explicitly.** Nothing silently skipped or auto-resolved.

---

## Detail-File Enrichment (Venmo & Amazon)

Some source files don't represent new transactions — they add missing detail to Chase rows that already exist. Venmo and Amazon are both this shape. Abacus handles them through a generalized enrichment pipeline (`processing/enrich.py`) that runs as part of the Ingest workflow. **Enrichment never creates new master transactions — it only updates fields on existing Chase rows.** This means totals stay correct even when enrichment is partial, delayed, or never arrives.

### Folder layout

- `input/` — where the user drops files each period
- `pending/` — enrichment files whose records haven't all matched yet; retried automatically every Ingest run
- `processed/` — files that have fully completed (transaction files fully ingested, enrichment files with all expected records matched)

### Workflow when the user opens Ingest

1. **Routing.** The page scans `input/` and sorts each file by its `source_file_map.account_type`:
   - Transaction-type (`checking`, `credit_card`) stays in `input/` for normal ingest.
   - Enrich-type (`venmo_detail`, `amazon_detail`) is moved to `pending/`.
   - Unknown prefix triggers an **account continuity prompt**: "Is this a brand-new account, or a replacement for an existing one?" If replacement, the old prefix's row records `replaced_by_prefix` so future matching traverses both.
2. **Cross-source completeness check.** For every source that was present in prior periods, verify the current input/pending set still includes it. Missing sources prompt with four options:
   1. Continue anyway
   2. Stop processing so I can provide the missing data
   3. The missing account has been discontinued — stop warning
   4. The missing account has been replaced — capture the new prefix and link them
3. **Ingest transaction files** (existing flow) with three behavior changes:
   - Chase rows matching the Venmo placeholder pattern get a default category of **Cash → Deposited or Withdrawn**. Chase rows matching Amazon patterns get **Shopping**. These defaults stick if enrichment never arrives, so reports remain accurate.
   - Row-level duplicates are **silently skipped** with a counter (previously a hard error that aborted the batch).
   - Per-source date continuity warns on gaps; **overlaps are silently OK** since row-level dedup catches duplicates.
4. **Process enrichment files from `pending/`.** For each file: parser → records → match → preview. User reviews and clicks **Confirm**. Matched proposals apply; the file moves to `processed/` only when every record marked `expected_to_match=True` has applied. Unresolved files stay in `pending/` and retry next period — no further action required. The manual confirm step exists for trust-building and will become auto-apply later.
5. **Categorize & finalize.** Auto-enriched rows land in `pending` status; user explicitly reviews and confirms each before reports run. This is intentional — the same payee can sometimes belong to a different category (a normally-social Venmo payment that's a one-off reimbursable business expense, e.g.), so per-transaction review stays a gate.

### Filename convention

Same regex as Chase. One shared account each — David and Debra share both:
- `Venmo MM-DD-YYYY to MM-DD-YYYY.csv`
- `Amazon MM-DD-YYYY to MM-DD-YYYY.csv`

### Placeholder payees and the no-pollute rule

Chase ingest normalizes "VENMO PAYMENT" descriptions to the payee "Venmo Payment", which is a *placeholder*: it represents many different real recipients, not one entity. The same goes for "Amazon" before enrichment fills in line-items. Two rules protect the learning system from being polluted by placeholders:

1. **Payee cell is locked in the Categorize UI for any row whose payee is a placeholder.** The user can't manually fill in a Venmo recipient name — only enrichment can. (The user's reasoning: without the Venmo file, they don't actually know who got paid, so guessing serves no purpose.)
2. **`payee_metadata` is not written when the user categorizes a placeholder-payee row.** The category lands on the transaction but doesn't generalize. This prevents "Venmo Payment → Cash" from being applied to every future Venmo row across different real recipients.

`PLACEHOLDER_PAYEES` lives in `processing/placeholders.py` as a single set; adding a new enrichment source = adding to that set.

### Idempotency

`apply_matches` skips any Chase row where `overridden = 1`. The first successful enrichment sets that flag, so re-running a pending file next period is safe — already-patched rows are no-ops, and only newly-matchable records get touched. Manual edits in the Maintenance / Categorize UIs also set `overridden = 1`, so they're protected from being overwritten by a delayed enrichment.

### Learning loop — Venmo payees

Once enrichment patches a Chase row's payee to "Sylvia Vientulis," the existing `payee_metadata` mechanism takes over. Categorize Sylvia once and next month's Venmo payment to her auto-categorizes. No special wiring — `apply_matches` triggers the auto-categorize pass on affected rows immediately after patching.

### AI-assisted payee categorization

For pending transactions that have a payee but no category (most often: brand-new payees you've never categorized before, or post-enrichment Venmo rows where the recipient is new), the Categorize tab has an "🤖 Ask Claude" expander that batch-sends them to the Anthropic API and gets back a category + subcategory + confidence per payee. The suggestions land on the transactions with an `[AI: <confidence> — <reasoning>]` marker appended to the note so you can spot which categories Claude chose; you review and edit in the grid before clicking Save Categorized. Requires an `ANTHROPIC_API_KEY` from console.anthropic.com (separate from any Claude.ai subscription). Implementation: `processing/ai_classify.py`. Costs under $1/year at typical household volume.

### Learning loop — Amazon items

A single Amazon order can span categories (atorvastatin + a dress shirt + a furniture polish), so the natural learning unit is the item, not the order. New `item_metadata` table keyed by ASIN (preferred) or normalized title.

- At enrichment time, the parser builds the note as a multi-line item list and looks up each item in `item_metadata`. If all items share a known category, the Chase row gets that category. Mixed-category orders stay uncategorized with hints in the note ("atorvastatin → [Medical]; dress shirt → [?]").
- At categorize time, a checkbox offers "Apply this category to the N unmapped items in this order" — opt-in writes to `item_metadata` only for items that don't already have a different mapping. This prevents a single mixed-category order from silently relearning the wrong thing.
- A new Maintenance tab "Amazon Items" allows direct edits to `item_metadata`.

### Pending folder cleanup

A file with one un-matchable record stays in `pending/` forever and gets re-parsed on every Ingest run. Maintenance → Pending Folder tab exposes a manual "Move files older than [XX] days to processed/" button (default 180 days). It only runs when the user clicks it — nothing moves automatically.

### Schema changes

- Drop CHECK constraint on `source_file_map.account_type` (idempotent table-rebuild migration in `init_db`) so it can hold `"venmo_detail"`, `"amazon_detail"`, and future detail kinds.
- Add `source_file_map.discontinued_since` (DATE) and `source_file_map.replaced_by_prefix` (TEXT) for the account-continuity mechanism.
- Add `item_metadata` table (item_key, display_name, category, subcategory, tax_flags).

### What it does

- Patches Chase row payee + via + note from Venmo file detail
- Patches Chase row note (and category, when items unanimously resolve) from Amazon order detail
- Asynchronous-by-default: unresolved records sit in `pending/` and retry every period; nothing requires manual intervention until or unless a file ages out
- Locks placeholder-payee cells so the user can't accidentally fill them in without data
- Prevents `payee_metadata` pollution from placeholder payees
- Tracks account replacements so reissued cards (Chase6190 → Chase12345) don't break matching
- Warns when prior-period sources are missing in the current input, with four resolution options
- Idempotent re-application — safe to retry the same pending file across periods
- Surfaces card-reissue and discontinued-account state in Maintenance

### What it doesn't do

- **Doesn't create new master transactions** from enrichment files. The Chase row is the only record of spend; enrichment relabels and annotates.
- **Doesn't auto-confirm** transactions. User review remains required before reports.
- **Doesn't write `payee_metadata` for placeholder payees** like "Venmo Payment." Real names from enrichment do generalize; placeholders don't.
- **Doesn't allow manual entry of a placeholder-payee row's payee field.** Only enrichment fills these.
- **Doesn't auto-categorize Amazon orders that span categories.** Mixed orders surface with per-item hints in the note for the user to decide.
- **Doesn't split a Chase Amazon row into per-item sub-rows.** Item-level learning gets most of the benefit; full splitting remains a future enhancement.
- **Doesn't overwrite a user-typed `note`** — appends instead.
- **Doesn't match Venmo card swipes** (Funding Source = Venmo balance, not a Chase account). These are flagged "no match expected" and never block file completion. Debra doesn't use the Venmo card so this is informational only.
- **Doesn't move pending files automatically based on age.** Cleanup is a manual user action.

### Things to watch out for / revisit

- **Default-category misclassification.** If `DEFAULT_CATEGORY_PATTERNS` accidentally matches a Chase row that isn't actually Venmo or Amazon, the default category will be wrong. Patterns are conservative ("VENMO PAYMENT" is unambiguous; Amazon patterns reuse the established `_extract_order_ref` regexes).
- **Same-amount Venmo collisions.** Two payments of the same amount on adjacent days could match the wrong Chase row. Mitigated by closest-date tiebreaker and conflict warnings in preview. If recurring, add a description-distance tiebreaker.
- **Amazon canonical item key.** ASIN vs. normalized title — deferred until a sample Amazon CSV is available. ASIN is more reliable but requires the export to include it.
- **`item_metadata` learning from mixed orders.** Today the "Apply to items" checkbox only writes mappings for items that don't already have a *different* mapping, to avoid silently relearning the wrong category. May need refinement after real-world use.
- **Pending folder accumulation.** Files that never fully match grow over time. Manual cleanup tool exists; if it gets unwieldy, consider an automatic age-based archive.
- **Account replacement during a partially-enriched period.** Cross-period matching needs to traverse `replaced_by_prefix` chains. Spec'd; implementation must handle the chain walk carefully.
- **Future: transaction splitting.** If/when implemented, Amazon enrichment would split a Chase row into per-item sub-rows directly, retiring the single-category-per-order pattern.

---

## Next Steps

Concrete work that's queued and committed — has a clear path, just hasn't been built yet. A future session can pick any of these up without re-deriving context.

1. **Amazon order scraper.** Amazon doesn't offer an order-history export, so we need a small tool that pulls the data ourselves. Recommended implementation: a **browser bookmarklet** — a one-line JavaScript snippet saved as a browser bookmark. Workflow per use: log into amazon.com normally, navigate to Your Orders, filter to the desired date range, click the bookmarklet, save the downloaded CSV into `input/`. Once a month, takes about a minute. Trade-off: brittle to Amazon's DOM changes (might need a small fix every year or two), but zero install, no anti-bot concerns, no TOS exposure since it runs in your own logged-in session.

   Alternative if the bookmarklet becomes painful: **Playwright in Python** with a visible browser window — full automation, but heavier setup (`pip install playwright; playwright install chromium`) and overkill for monthly use.

   **Pagination:** monthly volume can easily exceed a single Amazon orders page (e.g. ~10 orders/page is the default; YTD scrapes might span 12+ pages). The bookmarklet must walk pages itself, not rely on the user clicking Next manually. Two viable approaches:

   - **Auto-paginate via `fetch()`** (preferred): from the current logged-in page context, fetch subsequent pages directly using Amazon's `?startIndex=N` pagination URL pattern. Parse each fetched page's HTML, accumulate orders, stop when a fetched page is empty or the date-range boundary is crossed. Include a small inter-request delay (~300ms) to stay polite and avoid throttling. For 113 orders this completes in roughly 5-10 seconds.
   - **Single-page after raising page size**: if Amazon's UI honors a `?items_per_page=100` (or similar) URL parameter to show all orders on one page, the user navigates with that param first, then the bookmarklet scrapes once without pagination logic. Less reliable because Amazon controls whether the param is honored.

   Build the bookmarklet around auto-pagination as the primary path; the page-size hack is a nice-to-have fallback.

   **CSV output format the bookmarklet must produce** (matches the enricher's expected schema):

   - Filename: `Amazon MM-DD-YYYY to MM-DD-YYYY.csv` (same convention as other Abacus inputs)
   - One row per item (not per order). Multiple items in the same order share the same Order ID.
   - Columns:
     - `Order ID` — Amazon's order reference (e.g. `113-1234567-1234567`). This is what `order_ref` matches against on the Chase side.
     - `Order Date` — date the order was placed
     - `Order Total` — total of the full order (same on every row for that order)
     - `Item Title` — human-readable name (e.g. "Atorvastatin Calcium 20mg Tablets, 30 Count")
     - `ASIN` — Amazon's internal product identifier (the preferred `item_key` for `item_metadata`)
     - `Item Price` — per-unit price
     - `Item Quantity` — number ordered
     - `Item Category` — Amazon's top-level department from the product-page breadcrumb (e.g. "Books", "Clothing, Shoes & Jewelry", "Health & Household"). **Optional in v1** — leave blank if the bookmarklet doesn't fetch product pages. Adding breadcrumb capture is a v2 enhancement: it requires the bookmarklet to follow each item's product link, scrape the breadcrumb, then come back. Slower, but provides a clean Abacus-category mapping path (Books → Fun → Tickets and Other, Clothing → Shopping, Health → Health and Wellness, etc.) that reduces manual categorization.
     - `Seller` — third-party seller name if applicable, blank for items sold by Amazon directly (optional)

   This scraper is the prerequisite for items 2 and 3; without it, Amazon stays as a single Chase row labeled just "Amazon" with no visibility into what was actually purchased.
2. **Amazon enrichment parser.** Once the screen-scraper exists, implement `parse_amazon()` in `processing/enrich.py` and register it with `@register_enricher("amazon_detail", match_strategy="order_ref")`. Most of the plumbing is already in place: the registry pattern, the `item_metadata` table, the order_ref extraction from Chase descriptions, and the matching strategy. The remaining work is the parser function itself plus a decision about how to identify items uniquely — by ASIN (Amazon's internal product code, most reliable) or by normalized item title (works even if ASIN isn't in the export).
3. **Amazon item-level learning UI.** Once Amazon enrichment lands, two pieces of UI are needed. First, a checkbox in the Categorize tab labeled "Apply this category to the N unmapped items in this order" — when you categorize an Amazon order, that checkbox writes per-item category mappings into `item_metadata` so future orders containing the same item auto-categorize. Second, a new tab in Maintenance called "Amazon Items" that lets you view and edit `item_metadata` directly (e.g. to fix a wrong category that got learned from a mixed-category order). Detailed designs are in the Detail-File Enrichment section above.
4. **Auto-fill tax flags from category.** Categories already have a `tax_flag_default` column in the database — for example Medical maps to the "Medical" tax flag, Donations → Deductible maps to "Donations - Deductible". Today the user has to set the tax flag manually after picking a category. The fix: when you pick a category that has a default tax flag, the tax flag field auto-populates with that default. If you've already set the tax flag manually for a row, it's left alone. Saves clicks and reduces the chance of forgetting to set a tax flag on a deductible item.
5. **Near-miss normalization detector.** Over time, the same merchant can accumulate slightly-different normalized payee names (e.g. "Martha's" vs "Martha & Bros" — both are Martha & Bros Coffee). Reports then show them as two separate payees with split totals, which is wrong. This tool would scan all distinct payee names, score pairs by edit-distance or token-overlap similarity, and surface likely duplicates with a "merge these two?" prompt that feeds into the existing Rename / Merge Payees workflow. Keeps the payee list clean without manual auditing.
6. **Verify cross-source missing-account prompt actually works.** When you ingest files for a new period and a Chase account that was present in prior periods isn't in the current batch (e.g. you forgot to download Chase 5616 this month), Abacus is supposed to warn you with four choices: Continue Anyway, Stop Processing, Account Discontinued, or Account Replaced. The code for this prompt exists in `ui/process.py` but hasn't actually fired in practice yet — every test batch so far has included every known account. Worth a deliberate test next time you intentionally skip a file, just to confirm the dialog appears and the four options behave correctly.
7. **Verify account-continuity prompt actually works.** When a Chase card gets reissued under a new number (e.g. Chase6190 → Chase12345), Abacus is supposed to ask "Is this a brand-new account, or a replacement for an existing one?" so the new prefix can be linked to the old one's history. The code is in place but hasn't been exercised because no card has been reissued during testing. Test deliberately by introducing a fake new prefix in the input folder next time you have a chance.
8. **Auto-apply mode for enrichments.** Right now, every enrichment run requires you to click "Apply Enrichments" after reviewing the proposed payee/category patches — a manual confirmation step. The original plan was to flip this to fully automatic once you've used the manual flow long enough to trust it. Hold off until at least a few months of clean enrichment runs, then revisit.
9. **Collapsible Instructions box on every page.** Right now usage instructions live in separate `.docx` files (`INSTRUCTIONS Abacus.docx`, `processing flow for venmo and amazon.docx`, etc.) that the user has to flip to. Goal: put a collapsible "Instructions" panel at the top of each page — Ingest, Normalize & Categorize, Reports, Maintenance — that summarizes what that page does and how to walk through it. Default closed so it doesn't clutter the screen, but always one click away. The Ingest page already has the start of this; extend the pattern to the others.
10. **Audit the UI for sharp objects.** Sweep every page in the app for actions that can destroy or overwrite user work, and put each one behind a confirmation barrier. Known examples already guarded: Purge Transaction Data (typed phrase), Re-run All Normalization (typed phrase). Suspect candidates that may not have guards: Delete Selected on the various Maintenance tabs (payee_normalization, payee_metadata, categories), the Reset Venmo Rows button, anything that bulk-clears state. Pattern to apply: either a two-click confirm (red button on second click) or a typed-phrase like the Purge example. Goal is that no single accidental click loses work.

---

## Future Enhancements (longer-horizon, no firm commitment)

Items that would be nice to have but aren't currently planned. Re-evaluate when one becomes painful enough to upgrade.

1. **Transaction splitting.** Today a transaction has exactly one category. Splitting would let a single row be broken into multiple sub-rows with different categories — useful for things like a restaurant bill that's part personal and part reimbursable business meal, or a grocery run that includes household supplies and prescriptions on the same receipt. The Amazon item-level learning in Next Steps #3 covers the most common case (Amazon orders spanning categories), so general splitting is less urgent than it would otherwise be.
2. **Manual "Add Transaction" form.** A way to add a brand-new transaction by hand, not derived from any ingested file. The use case is money that flowed outside a tracked account — e.g. the Venmo-balance portion of split-funded charges, or any Wells Fargo-funded Venmo payment. Today you can only edit transactions that exist; this would add the ability to create one. Not a priority because the upstream fix (turning on Venmo auto-transfer and making Chase the default funding source) closes most of the gap before it ever reaches Abacus.
3. **Zelle detail import.** Same idea as the Venmo enricher: import a per-transaction detail file from Zelle so Chase's vague "Online Payment To X" rows get filled in with the real recipient and any memo. Blocked because Zelle doesn't currently offer a usable export. If/when they add one, the existing enrichment framework can absorb it as a new parser registration.
4. **Payor reporting.** A new report (or report section) showing spending by category broken out by payor — i.e. how much David spent vs. how much Debra spent vs. shared expenses. The payor field already exists on every transaction; this is purely a reporting addition. Useful for household-budget conversations and for understanding who's contributing what to which categories.
5. **AG Grid for Browse/Search.** The Category Assignment and Maintenance screens already use streamlit-aggrid for rich inline editing. The Browse/Search screen still uses the simpler `st.dataframe`, which means you can sort/filter but can't edit fields inline — you have to go to Maintenance → Edit Transactions instead. Upgrading Browse to AG Grid would unify the editing experience. If Streamlit ever becomes a constraint, NiceGUI is the closest alternative with native AG Grid support.

---

## Explicitly out of scope

Documented here so they don't drift back onto the planning list.

- **Tracking Wells Fargo as a separate source.** Some Venmo transactions are funded directly from Wells Fargo and never touch Chase, so they're invisible to Abacus. Building Wells Fargo ingest would close the gap, but it's a meaningful project (new template, new validation, new reconciliation logic). The user has explicitly accepted this as a known small gap rather than expanding the scope. If the gap ever grows large enough to matter, revisit — but don't add it back to the plan automatically.
- **Per-transaction handling of Venmo-balance / split-funding gaps.** When Venmo splits a charge between balance and Chase (e.g. the observed $29.99 myiq.com = $4.07 Chase + $25.92 Venmo balance), Abacus only sees the Chase portion. Same root cause as Wells Fargo. The fix is upstream — turning on auto-transfer in Venmo and setting Chase as the default funding source, which keeps the Venmo balance at $0 so every charge pulls entirely from Chase. See the `project_venmo_funding_setup` memory for the full settings recipe.
