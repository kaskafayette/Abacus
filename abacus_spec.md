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
| Household | Groceries, Utilities, Auto, Mortgage, Maintenance, Subscriptions, Capital Improvements, Office, Gifts |
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
4. **Unmatched descriptions** are shown in a scrollable table, de-duplicated with a count column, VIA prefixes stripped, sorted alphabetically.
5. **Inline assignment** - each unmatched row has a text input and "Assign" button for one-off payee names (e.g. checks).
6. **Add Normalization Rule** - a form below for creating reusable pattern-based rules. Matched items disappear from the list above.
7. **Re-run All Normalization** - clears payee/via on all pending/needs_review transactions and re-applies all rules. Available even when all payees are matched.

### Category Assignment Tab

1. Auto-categorization applies saved `payee_metadata` defaults.
2. **Summary table** at top shows all pending payees (de-duplicated) with transaction count, total, and current category. Click a row to select it.
3. **Detail panel** below shows the selected payee's transactions and a category assignment form:
   - Category dropdown
   - Subcategory dropdown (dynamically filtered by selected category)
   - Tax Flags multi-select (auto-populated from category defaults)
   - Payor dropdown (David / Debra / Both / Unknown)
   - Note text input
   - All widget keys are tied to the selected payee so fields reset when switching payees
4. **Apply to All** - sets category on all transactions for that payee, saves as payee_metadata default for future months.
5. **Skip (Needs Review)** - marks transactions as needs_review.
6. **Print Pending Items** - generates a PDF list of all pending transactions.

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

Six tabs: Source Accounts, Payee Normalization, Payee Metadata, Category Master, Edit Transactions, Database.

All forms include Save and Cancel buttons.

### Source Accounts
- Table showing all accounts: prefix, label, nickname, account type
- Edit form to update nickname and account type (prefix/label not editable)

### Payee Normalization
- Table of all rules: search pattern, normalized name, payee suffix
- Add New Rule form
- Edit / Delete form
- **Apply Rules to All Transactions** button - re-runs all normalization rules against every transaction (including confirmed), to fix inconsistencies after editing rules

### Payee Metadata
- Table of all payee defaults
- Add / Update form with category/subcategory dropdowns, tax flags, payor, note
- Delete option

### Category Master
- Table of all categories with subcategories and tax flag defaults
- Add New Category form
- Warning that renaming does not cascade to existing transactions
- Delete option

### Edit Transactions
- Search, date range, source, and status filters
- Paginated results table
- Edit panel for individual transactions (payee, category, subcategory, tax flags, payor, note, status)

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

## Future Enhancements (Out of Scope for v1)

1. **Amazon order matching:** Order reference codes are already extracted and stored in `order_ref`. Future: scrape Amazon order detail and match by reference code to categorize individual items within an Amazon order.
2. **Supplemental source ingestion:** Import Venmo transaction detail and Zelle detail to resolve ambiguous descriptions.
3. **Transaction splitting:** Allow a single transaction to be split into multiple sub-rows with different categories.
4. **Payor reporting:** Report showing spending by category broken out by payor.
5. **Inline grid editing with AG Grid:** Replace `st.data_editor` / `st.dataframe` with `streamlit-aggrid` (pip package) to get proper spreadsheet-style inline editing. AG Grid supports editable cells with per-column dropdown selectors, dynamic dropdowns (e.g. subcategory filtered by selected category), multi-select for tax flags, and inline save on cell change. This is a drop-in replacement within Streamlit - no framework change needed. Applies to Browse/Search and Categorize screens. If a full framework change is ever warranted, NiceGUI is the closest alternative to Streamlit with native AG Grid support.
