# Abacus — Software Specification

## Overview

Abacus is a personal bookkeeping tool for a household with both personal and business expenses. It ingests monthly bank and credit card CSV exports, normalizes and categorizes transactions using configurable lookup tables, and produces PDF and Excel reports. The user interacts through a Streamlit web UI. All data is stored locally in SQLite.

---

## Technology Stack

| Layer | Technology |
|---|---|
| UI | Streamlit |
| Backend / scripting | Python |
| Database | SQLite (local file) |
| Report output | PDF via WeasyPrint, Excel (.xlsx) |

---

## Database Schema

### `transactions`
Stores all processed transactions. One row per transaction.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `date` | DATE | |
| `amount` | DECIMAL | |
| `check_number` | TEXT | Check number if applicable, otherwise NULL |
| `description_raw` | TEXT | Original text from source file |
| `category_raw` | TEXT | Category suggested by source file, if any — preserved for reference, never used in calculations |
| `payee` | TEXT | Normalized payee name |
| `via` | TEXT | Payment intermediary (Square, Venmo, ActBlue, etc.) |
| `payor` | TEXT | Person within household |
| `category` | TEXT | |
| `subcategory` | TEXT | |
| `tax_flags` | TEXT | Comma-separated flags, or NULL |
| `note` | TEXT | Free-text override note |
| `source` | TEXT | Normalized account label (e.g. "Chase5678", "PersonalVisa4321") — derived from source_file_map |
| `status` | TEXT | `"pending"` = imported but not fully confirmed; `"confirmed"` = all fields resolved; `"needs_review"` = user flagged for follow-up |
| `overridden` | BOOLEAN | True if any field was manually edited |

---

### `payee_normalization`
Maps raw description strings to normalized payee names.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `search_pattern` | TEXT | Case-insensitive match string |
| `normalized_name` | TEXT | Canonical payee name |
| `payee_suffix` | TEXT | Order/suffix info to strip |

Multiple search patterns may map to the same `normalized_name`.

---

### `payee_metadata`
Maps normalized payee names to category and tax metadata. Overrides category-level defaults.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `normalized_name` | TEXT | FK → payee_normalization.normalized_name |
| `category_override` | TEXT | NULL = use category default |
| `subcategory_override` | TEXT | NULL = use category default |
| `tax_flags_override` | TEXT | NULL = use category default |
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

---

### `source_file_map`
Maps filename prefix to human-readable source label. The filename convention is `<prefix> MM-DD-YYYY to MM-DD-YYYY.csv`, e.g. `Chase5616 01-01-2026 to 02-28-2026.csv`. The prefix is the lookup key. Extra whitespace between the prefix and the date range is tolerated.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `source_prefix` | TEXT | e.g. `"chase12345"`, `"wellschecking"` |
| `source_label` | TEXT | Human-readable label, e.g. `"Chase Checking 1234"` |

---

### `column_templates`
Stores a parsing template per source account. Created interactively on first encounter of a new prefix; reused silently thereafter. Handles the full variation in how different banks format their CSV exports — all variation is resolved here, before data enters the canonical internal format.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `source_prefix` | TEXT | FK → source_file_map.source_prefix |
| `date_column` | TEXT | CSV column name for **post date** — if source has both post date and settlement date, this must point to post date; settlement date column is ignored |
| `check_number_column` | TEXT | CSV column name for check number, or NULL if source does not include one |
| `date_format` | TEXT | Python strptime string, e.g. `"%m/%d/%Y"` |
| `amount_mode` | TEXT | `"single"` or `"split"` |
| `amount_column` | TEXT | Column name if amount_mode = "single" |
| `debit_column` | TEXT | Column name if amount_mode = "split" |
| `credit_column` | TEXT | Column name if amount_mode = "split" |
| `description_column` | TEXT | CSV column name for raw description |
| `category_raw_column` | TEXT | CSV column name for the source-provided category (e.g. Chase credit card "Category" field), or NULL if source does not include one. Stored in `transactions.category_raw` for reference only |
| `sign_convention` | TEXT | `"negative_is_debit"` or `"positive_is_debit"` — single-column mode only |
| `card_column` | TEXT | CSV column name that identifies the card/sub-account within a single file, or NULL. When present, each distinct value in this column is treated as a separate source — the value is appended to the file's prefix to form the source key (e.g. prefix `Chase7625` with card value `4669` → source `Chase7625-4669`). The corresponding `source_file_map` entries must exist for each sub-source. |

---

### `processed_files`
Records every source file that has been successfully ingested. Used to detect accidental re-uploads before any data is written.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `filename` | TEXT | Original filename as it appeared in `input/` |
| `source_prefix` | TEXT | FK → source_file_map.source_prefix |
| `file_hash` | TEXT | SHA-256 hash of file contents |
| `date_range_start` | DATE | Start date parsed from filename |
| `date_range_end` | DATE | End date parsed from filename |
| `ingested_at` | DATETIME | Timestamp of successful ingestion |

---

## Category Taxonomy

### General Account Categories

| Category | Subcategories |
|---|---|
| Income | Social Security (net), W2 and 1099 (net) |
| Transfer | *(no subcategories)* |
| Household | Groceries, Utilities, Auto, Mortgage, Maintenance, Subscriptions, Capital Improvements, Office |
| Fun | Restaurants, Tickets and Other, Travel |
| Health and Wellness | *(no subcategories)* |
| Medical | Prescription Medicines, Medical Insurance, Annual Plan Charges, Medical Devices and Supplies, Long Term Care, Travel and Lodging, Hospitals, Lab Fees, Doctors and Dentists, Therapists, Misc Medical |
| Business Expenses NEC | *(see tax section)* |
| Shopping | *(no subcategories)* |
| Payments | Venmo and Zelle |
| Cash | Deposited or Withdrawn |
| Donations | *(see tax section)* |
| Tax and Investment Expenses | Taxes Paid or Refunded, Tax Preparation, Safe Deposit, Financial Subscriptions |

**Transfer** is a top-level category for money moving between accounts the user owns (e.g. a checking account payment to a credit card). Both sides of the movement — the debit from checking and the credit on the card — should be categorized as Transfer. This ensures transfers net to zero and do not inflate income or expense totals in reports. Reports should exclude or subtotal Transfer separately so it does not distort spending summaries.

### Tax / Business Subcategories

**Business Expense Categories:**
Travel, Meals, Subcontractors, Office Equipment, Subscriptions and Services, Miscellaneous

**Business Expenses (BsnsExp):**
- Bsns Exp Reimbursable
- Bsns Exp Non-reimbursable
- Bsns Exp Reimbursement

**Business Use of Home (BUH):**
Deductible Interest, Real Estate Tax, Insurance, Repairs and Maintenance, Utilities

**Taxes Paid:**
Personal Property, Vehicle Taxes

**Donations:**
Deductible – Cash, Deductible – Merchandise, Non-Deductible

### Tax Flags
Tax flags are boolean tags orthogonal to the category hierarchy. They are set at the category level (default), payee level (override), or transaction level (override). Flags include:

- Tax-reportable
- Reimbursable
- Capital Improvements
- Home Office
- Donations – Deductible
- Medical
- Business Expense

---

## Processing Flow

The system is designed to be run once per month.

### Startup Checks (on every launch)
1. **Master file check:** On launch, the app checks whether `abacus.db` exists.
   - If it exists: open and use it. Display a confirmation in the UI (e.g. "Using existing database: abacus.db").
   - If it does not exist: create a new empty database with the full schema. Inform the user that a new database has been created and that lookup tables should be populated before processing.
2. **Pending session check:** If any transactions are in `pending` status, display a prominent banner: "You have N transactions from a previous session awaiting review. [Resume]"

### Setup — Process Latest
On navigating to **Process Latest**, before accepting any files:
1. Display the required filename format prominently:
   > Files must be named: `<account-prefix> MM-DD-YYYY to MM-DD-YYYY.csv`
   > Example: `Chase5616 01-01-2026 to 01-31-2026.csv`
   > The prefix must match a known account or a new one will be set up. Extra spaces between the prefix and the date range are OK.
2. Prompt the user to specify the **processing period** (start date, end date) they intend to import.
3. Ask the user to confirm that all relevant CSV files have been placed in the `input/` folder.

---

### Step 1 — File Validation (all-or-nothing before any data is written)

All files are validated before any transactions are written. If any file has a problem, the user is shown a complete list of all issues and asked to fix them (rename files, add missing files) and re-run validation. The pipeline does not advance until all files pass.

Validation checks per file:
1. **Filename format:** Confirm the filename matches `<prefix> MM-DD-YYYY to MM-DD-YYYY.csv` (extra whitespace between prefix and date range is tolerated). If not, show an error with the expected format and stop.
2. **Prefix lookup:** Look up the prefix in `source_file_map`.
   - **Known prefix:** load its `column_templates` record.
   - **Unknown prefix:** queue for template setup (Step 2). Do not block validation — unknown prefixes are handled in sequence after all known files pass.
3. **Date range match:** Confirm the date range in each filename matches the processing period the user specified. Flag any mismatch.
4. **Date continuity:** Compare each file's date range against the most recent transaction in `transactions` for that source.
   - **Clean append:** OK.
   - **Gap:** Flag with description of the gap. User must upload the missing file or explicitly acknowledge the gap before proceeding.
   - **Overlap:** Flag with the overlapping date range. User must confirm which data is authoritative (existing DB or new file).
5. **No silent skips:** Every file in `input/` is accounted for. Any file that cannot be parsed or matched is surfaced as an error, not silently ignored.

Once all issues are resolved and the user clicks **Proceed**, files are parsed using their `column_templates` and transactions enter the pipeline. After all transactions from a file are successfully committed to `transactions`, the source file is moved from `input/` to `processed/` and a record is written to `processed_files`. If the commit fails, the file is not moved and no record is written.

---

### Step 2 — Template Setup (new accounts only)

Triggered when a filename prefix has no entry in `column_templates`. For known accounts this step is skipped entirely.

1. Display the actual CSV column headers from the file to the user.
2. Ask the user to identify:
   - Which column is the **date**, and what format it uses
   - Whether amounts are in a **single signed column** or **split debit/credit columns**, and which column(s)
   - For single-column mode: whether negative values represent debits or credits
   - Which column is the **raw description**
3. Save the completed template to `column_templates`.
4. From this point forward, this account is handled automatically.

**Canonical internal format:** Once ingested through the template, every transaction is represented as:
- `date` — post date as Python `datetime.date`. Transaction date (if present) is ignored entirely; the template specifies which column is the post date and all other date columns are discarded. All date continuity checks, date-range filtering, and report grouping operate on post date only. This is consistent with how banks filter downloads by date.
- `amount` — `Decimal`, always positive for debits, negative for credits (i.e. money leaving the account is positive, money entering is negative)
- `description_raw` — raw string, untouched
- `check_number` — string or NULL; populated if the source file includes a check number column, otherwise NULL
- `source` — the source label from `source_file_map`

All downstream processing operates on this canonical form only.

---

### Step 3 — Payee Normalization

1. Attempt to match each raw `description` value against `payee_normalization.search_pattern` (case-insensitive).
2. For matched rows, populate `payee` with `normalized_name` and `via` with any identified payment intermediary (e.g., Square, ActBlue, Venmo, Zelle).
3. Collect all unmatched descriptions and present them to the user in an editable Streamlit table with columns:
   - Raw description (read-only)
   - Suggested normalized name (editable)
   - Via (editable, optional)
4. User reviews, edits as needed, and clicks **Confirm**.
5. New normalization rules are written to `payee_normalization`.
6. All transactions are re-normalized.

---

### Step 4 — Category Assignment & Tax Flagging

1. For each normalized payee, look up category/subcategory/tax_flags using `payee_metadata` (if present) or `categories` defaults.
2. Present the user with an editable Streamlit table of all new transactions with columns:
   - Date, Source, Payee, Via, Amount (read-only)
   - Category (dropdown — values drawn from `categories` table; no free-text entry permitted)
   - Subcategory (dropdown — filtered to subcategories belonging to the selected category; no free-text entry permitted)
   - Tax Flags (editable multi-select)
   - Payor (editable)
   - Note (editable)
3. For any transaction where the correct category is unknown, the user sets status to `needs_review` and leaves category blank. These rows are written to `transactions` with `status = "pending"`.
4. User clicks **Save & Continue** or **Pause for Now**.
   - **Save & Continue:** all transactions written to `transactions` (confirmed rows as `"confirmed"`, unresolved rows as `"pending"`). Pipeline advances to Step 5 only if zero pending rows remain.
   - **Pause for Now:** same write behavior, but the app returns to the home screen. A banner on the home screen indicates an in-progress session with a count of pending transactions.
5. Overridden rows (any field changed from the default) are flagged with `overridden = TRUE`.

#### Resuming a paused session
When the user returns and clicks **Resume**, the app loads all `pending` transactions from the most recent processing batch, presents them in the same editable table, and allows the user to resolve the remaining items. Once all are resolved, the user can proceed to Step 5.

#### Pending items report
At any point during Step 4 (or from the Reports menu), the user can print a **Pending Items Report** — a simple list of all transactions currently in `pending` status, showing Date, Source, Amount, Description Raw, and a Note field. Intended to be handed to a spouse or advisor for identification.

---

### Step 5 — Report Generation

Step 5 is only reachable when zero `pending` transactions remain in the current batch. If pending items exist, the user is shown a warning with the count and must either resolve them or explicitly choose to proceed with gaps acknowledged.

The user is presented with a checklist of available reports and an optional date range override (defaults to the current processing month). The user checks the reports they want and clicks **Generate Selected Reports**. All selected reports are saved to `output/`.

#### Available Reports

**Monthly PDF Report** (multi-section, single file)

**Section 1 — Category Summary (Month & YTD)**
- Columns: Category, Subcategory, Month Total, YTD Total
- One row per category/subcategory
- No transaction detail

**Section 2 — Payee Summary (Month)**
- Columns: Category, Subcategory, Payee, # Transactions, Month Total
- One row per payee

**Section 3 — Transaction Detail (Month)**
- Columns: Date, Source, Category, Subcategory, Payee, Via, Amount, Payor, Note
- One row per transaction

**Section 4 — Tax Items Report (Month & YTD)**
- Only transactions where any tax flag is set
- Grouped by tax flag
- Columns: Date, Payee, Category, Subcategory, Amount, Tax Flag, Note
- Subtotals per tax category
- Month and YTD totals

---

## Other Functions

### Ad-Hoc Reports
User specifies a date range and can generate any of the standard report types:
- Category summary
- Payee summary
- Transaction detail
- Tax items

All reports exported to `output/`.

---

### Excel Export
User specifies a date range. Output is a `.xlsx` file with two tabs:

**Single tab — All Transactions**
- All transaction fields as columns
- Auto-filter on all column headers
- Sorted by date ascending
- No pivot tab — user can build their own pivot table in Excel as needed

---

## Streamlit UI Structure

Navigation is via a left sidebar. The sidebar always shows the current database filename and a status indicator — either "Ready" or "N transactions pending review" in amber.

---

### Home Screen
Shown on launch. Displays:
- Database status (filename, record counts for transactions and each lookup table)
- A prominent amber banner if any transactions are in `pending` status, with a **Resume** button
- Quick-links to Process Latest, Reports, and Maintenance

---

### Process Latest

A stepped workflow — one step visible at a time, with a progress indicator at the top (Step 1 of 5 etc.). The user cannot skip forward; they can only go back to review.

**Step 1 — File Validation**
- Instructional text at top showing the required filename format with an example
- Fields for the user to enter the processing period (start date, end date)
- A file listing table showing every file found in `input/`, with columns: Filename, Detected Prefix, Date Range (from filename), Status (OK / Unknown Prefix / Date Mismatch / Overlap / Gap)
- Errors shown inline in the Status column in red; a summary error count above the table
- **Proceed** button is disabled until all rows show OK
- Any unknown prefixes trigger an inline prompt in the table row for the user to enter a label and complete template setup before the row clears to OK

**Step 2 — Template Setup (new accounts only)**
- Only shown if one or more unknown prefixes were encountered
- One sub-section per new account, each showing: the detected CSV column headers as pills/tags, and dropdown fields for the user to map each required field (post date, amount or debit/credit, description, check number if present)
- A date format field with a live preview showing the first date value parsed using the entered format string
- **Save Template** button per account; all must be saved before advancing

**Step 3 — Payee Normalization**
- Two-column layout: left side is a read-only list of unrecognized raw descriptions with their amounts and dates; right side is an editable table where the user enters the normalized name and optional Via value for each
- Auto-suggest: if the raw description partially matches an existing normalized name, pre-populate the field with that suggestion (user can override)
- Row count shown at top ("23 new payees to review")
- **Confirm** button at bottom; disabled until all rows have a normalized name

**Step 4 — Category Assignment**
- Full-width editable table, one row per transaction
- Read-only columns: Date, Source, Payee, Via, Amount
- Editable columns: Category (dropdown), Subcategory (dropdown filtered by category), Tax Flags (multi-select), Payor (dropdown: David / Debra / Both / Unknown), Note (text)
- Rows with no category assigned are highlighted in yellow
- A **Mark as Needs Review** button per row sets status to `needs_review` and clears the highlight requirement for that row
- Summary bar at bottom: "N confirmed, N pending review"
- Two buttons: **Save & Continue** (advances to Step 5 if zero unresolved rows) and **Pause for Now** (saves and returns to Home)
- A **Print Pending Items** link generates a minimal PDF list of all `needs_review` rows

**Step 5 — Generate Reports**
- Shown only when zero pending rows remain
- Checklist of available reports (each with a checkbox):
  - Category Summary (Month & YTD)
  - Payee Summary
  - Transaction Detail
  - Tax Items
  - Pending Items (greyed out if none pending)
- Date range fields defaulting to the current processing period, overridable
- **Generate Selected Reports** button; progress indicator while generating
- On completion: list of generated filenames with their output paths

---

### Reports

**Run Ad-Hoc Report**
- Same report checklist and date range fields as Step 5
- **Generate** button; on completion shows output filenames

**Export to Excel**
- Date range fields (default: current month)
- **Export** button; on completion shows output filename

---

### Maintenance

All maintenance screens follow the same pattern: a read-only table showing all existing records, an **Add New** button that opens an inline form row at the top of the table, and **Edit** / **Delete** buttons per row. Deletions require a confirmation click.

**Payee Normalization**
- Table columns: Search Pattern, Normalized Name, Payee Suffix
- Sorted by Normalized Name

**Payee Metadata**
- Table columns: Normalized Name, Category Override, Subcategory Override, Tax Flags Override, Payor, Note
- Category and Subcategory fields are dropdowns; no free-text

**Category Master**
- Table columns: Category, Subcategory, Tax Flag Default
- Sorted by Category then Subcategory
- Note: editing category or subcategory names here does not cascade to existing transactions — user is warned of this on any edit

**Edit Transactions**
- Search bar at top (searches across Payee, Description Raw, Note)
- Date range filter and Source filter (dropdown)
- Results in a paginated table; clicking a row opens an edit panel showing all fields
- Editable fields: Payee, Category, Subcategory, Tax Flags, Payor, Note, Status
- Non-editable: Date, Amount, Description Raw, Source, Check Number
- Saves flagged with `overridden = TRUE`

**Database**
- Shows database file path, size, and record counts per table
- **Purge Transaction Data** button — requires user to type confirmation phrase before executing

---

## Folder Structure

```
abacus/
├── input/              ← User drops CSV files here each month
├── processed/          ← Source files moved here after successful ingestion
├── output/             ← Reports and exports deposited here
├── abacus.db           ← SQLite database
├── app.py              ← Streamlit entry point
├── processing/
│   ├── ingest.py       ← Step 1–2: file parsing and date validation
│   ├── normalize.py    ← Step 3: payee normalization
│   ├── categorize.py   ← Step 4: category assignment
│   └── reports.py      ← Step 5: PDF and Excel generation
├── db/
│   ├── schema.py       ← Table definitions
│   └── queries.py      ← All DB access functions
└── ui/
    ├── process.py      ← Process Latest page
    ├── reports.py      ← Reports page
    └── maintenance.py  ← Maintenance pages
```

---

## Database Management

### First Launch — Database Creation
On first launch (no `abacus.db` present), the app creates a new empty database with the full schema and seeds the `categories` table with the standard taxonomy defined in this spec. The user is directed to populate `source_file_map` and `column_templates` via the Maintenance screens before processing any files.

### Purge & Recreate Master File
Available under **Maintenance → Database → Purge Transaction Data**.

This operation:
- Deletes all rows from `transactions`
- Does **not** touch any lookup tables (`payee_normalization`, `payee_metadata`, `categories`, `source_file_map`, `column_templates`)
- Requires the user to type a confirmation phrase (e.g. `"DELETE ALL TRANSACTIONS"`) before executing
- Is logged with a timestamp in a `db_audit_log` table

This allows the user to start fresh with transaction data (e.g. after a corrupted import) without losing the lookup configuration that took effort to build.

**There is no UI option to drop lookup tables.** Lookup table edits are done row-by-row through the Maintenance CRUD screens only.

---

## Error Handling & Edge Cases

- **Duplicate transactions:** Two-layer check:
  1. **File-level:** Before ingesting any file, compute a fingerprint of the file (SHA-256 hash) and check it against a `processed_files` table. If the file has already been ingested, reject it immediately with a clear error and do not proceed. The file remains in `input/` for the user to remove.
  2. **Row-level:** After parsing, check each transaction against existing rows in `transactions` using (date + source + amount + description_raw). Any row-level duplicates are surfaced to the user as an error. The user must resolve before the pipeline advances — there is no "import anyway" option. Row-level duplicates most likely indicate a file-level problem that the hash check missed (e.g. same data in a differently-named file).
- **Missing column template:** Trigger template setup flow; never guess at column mappings.
- **Empty input folder:** Display a clear message; do not run the pipeline.
- **Database write failure:** Roll back the entire batch write; never write partial data.
- **All validation errors are shown to the user explicitly.** Nothing is silently skipped or auto-resolved.

---

## Future Enhancements (Out of Scope for v1)

1. **Supplemental source ingestion:** Import Amazon order detail, Venmo transaction detail, and Zelle detail to resolve ambiguous descriptions.
2. **Transaction splitting:** Allow a single transaction to be split into multiple sub-rows with different categories (e.g., split a Safeway charge into Groceries and Medical).
3. **Payor reporting:** Add a report showing spending by category broken out by payor (David / Debra / Both / Unknown).
