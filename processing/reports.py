"""Step 5: PDF and Excel report generation."""

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF
import openpyxl
from openpyxl.utils import get_column_letter

from db import queries

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


# ---------------------------------------------------------------------------
# Data gathering helpers
# ---------------------------------------------------------------------------

def _fetch_month_transactions(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """Fetch confirmed transactions for a date range."""
    rows = queries.get_transactions(conn, start_date=start, end_date=end)
    return [dict(r) for r in rows if r["status"] == "confirmed"]


def _fetch_ytd_transactions(conn: sqlite3.Connection, end: str) -> list[dict]:
    """Fetch confirmed transactions from Jan 1 of the end-date year through end."""
    year = end[:4]
    ytd_start = f"{year}-01-01"
    rows = queries.get_transactions(conn, start_date=ytd_start, end_date=end)
    return [dict(r) for r in rows if r["status"] == "confirmed"]


def _sum_by(txns: list[dict], *keys) -> dict[tuple, Decimal]:
    """Group transactions by given keys and sum amounts."""
    totals: dict[tuple, Decimal] = {}
    for t in txns:
        group = tuple(t.get(k) or "" for k in keys)
        totals[group] = totals.get(group, Decimal(0)) + Decimal(t["amount"])
    return dict(sorted(totals.items()))


def _fmt_amt(val) -> str:
    """Format a Decimal/string amount as currency."""
    d = Decimal(str(val))
    if d >= 0:
        return f"${d:,.2f}"
    return f"-${abs(d):,.2f}"


def _safe(val) -> str:
    """Ensure a value is a clean string for PDF output."""
    if val is None:
        return ""
    s = str(val)
    s = s.replace("\u2014", "-")
    s = s.replace("\u2013", "-")
    s = s.replace("\u2018", "'")
    s = s.replace("\u2019", "'")
    s = s.replace("\u201c", '"')
    s = s.replace("\u201d", '"')
    s = s.replace("\u2026", "...")
    s = s.replace("&amp;", "&")
    return s.encode("latin-1", errors="replace").decode("latin-1")


# ---------------------------------------------------------------------------
# PDF helper class
# ---------------------------------------------------------------------------

class AbacusPDF(FPDF):
    """Landscape letter PDF with consistent headers/footers."""

    def __init__(self, title: str = "Abacus Report"):
        super().__init__(orientation="L", unit="mm", format="Letter")
        self._title = _safe(title)
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, self._title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-10)
        self.set_font("Helvetica", "I", 7)
        self.cell(0, 5, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, text: str):
        self.ln(4)
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 6, _safe(text), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(150, 150, 150)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)

    def table_header(self, widths: list[float], headers: list[str]):
        self.set_font("Helvetica", "B", 7)
        self.set_fill_color(235, 235, 235)
        for w, h in zip(widths, headers):
            self.cell(w, 5, h, border=1, fill=True)
        self.ln()

    def table_row(self, widths: list[float], values: list[str],
                  bold: bool = False, fill: bool = False):
        style = "B" if bold else ""
        self.set_font("Helvetica", style, 7)
        if fill:
            self.set_fill_color(245, 245, 245)
        for w, v in zip(widths, values):
            self.cell(w, 4.5, _safe(v), border=1, fill=fill)
        self.ln()

    def group_header(self, text: str, level: int = 0):
        """Print a group header row spanning the full width."""
        indent = "    " * level
        self.set_font("Helvetica", "B", 7 if level > 0 else 8)
        self.set_fill_color(235, 235, 235) if level == 0 else self.set_fill_color(242, 242, 242)
        self.cell(0, 5, f"{indent}{_safe(text)}", border=0, fill=True,
                  new_x="LMARGIN", new_y="NEXT")

    def subtotal_row(self, label: str, amount: str, indent: int = 0):
        """Print a subtotal row."""
        prefix = "    " * indent
        self.set_font("Helvetica", "B", 7)
        self.set_fill_color(248, 248, 248)
        w = self.w - self.l_margin - self.r_margin
        label_w = w - 35
        self.cell(label_w, 4.5, f"{prefix}{_safe(label)}", border=0, fill=True)
        self.cell(35, 4.5, _safe(amount), border=0, fill=True, align="R")
        self.ln()


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _write_category_summary(pdf: AbacusPDF, month_txns, ytd_txns):
    """Section 1: Category Summary (Month & YTD) - grouped by category."""
    pdf.section_title("Category Summary (Month & YTD)")

    month_totals = _sum_by(month_txns, "category", "subcategory")
    ytd_totals = _sum_by(ytd_txns, "category", "subcategory")
    all_keys = sorted(set(month_totals.keys()) | set(ytd_totals.keys()))

    # Get unique categories in order
    categories = []
    seen = set()
    for key in all_keys:
        if key[0] not in seen:
            categories.append(key[0])
            seen.add(key[0])

    widths = [120, 35, 35]
    pdf.table_header(widths, ["", "Month", "YTD"])

    for cat in categories:
        # Category header
        pdf.group_header(cat, level=0)

        cat_keys = [k for k in all_keys if k[0] == cat]
        for key in cat_keys:
            _, subcat = key
            if subcat:
                m = month_totals.get(key, Decimal(0))
                y = ytd_totals.get(key, Decimal(0))
                pdf.table_row(widths, [f"    {subcat}", _fmt_amt(m), _fmt_amt(y)])

        # Category subtotal
        cat_month = sum(v for k, v in month_totals.items() if k[0] == cat)
        cat_ytd = sum(v for k, v in ytd_totals.items() if k[0] == cat)
        pdf.table_row(widths, [f"  SUBTOTAL {cat}", _fmt_amt(cat_month), _fmt_amt(cat_ytd)],
                      bold=True, fill=True)

    # Grand total
    grand_month = sum(month_totals.values(), Decimal(0))
    grand_ytd = sum(ytd_totals.values(), Decimal(0))
    pdf.ln(2)
    pdf.table_row(widths, ["GRAND TOTAL", _fmt_amt(grand_month), _fmt_amt(grand_ytd)],
                  bold=True, fill=True)


def _write_payee_summary(pdf: AbacusPDF, month_txns):
    """Section 2: Payee Summary (Month) - grouped by category/subcategory."""
    pdf.section_title("Payee Summary (Month)")

    # Group by cat, subcat, payee
    groups: dict[tuple, list] = {}
    for t in month_txns:
        key = (t.get("category") or "", t.get("subcategory") or "", t.get("payee") or "")
        groups.setdefault(key, []).append(t)

    widths = [100, 15, 35]
    pdf.table_header(widths, ["Payee", "#", "Total"])

    current_cat = None
    current_subcat = None

    for key in sorted(groups.keys()):
        cat, subcat, payee = key
        txns = groups[key]
        total = sum(Decimal(t["amount"]) for t in txns)

        # Category header
        if cat != current_cat:
            pdf.group_header(cat, level=0)
            current_cat = cat
            current_subcat = None

        # Subcategory header
        if subcat and subcat != current_subcat:
            pdf.group_header(subcat, level=1)
            current_subcat = subcat

        # Payee row
        indent = "        " if subcat else "    "
        pdf.table_row(widths, [f"{indent}{payee}", str(len(txns)), _fmt_amt(total)])

    # Subcategory subtotals and category subtotals
    # Recompute with subtotals
    pdf.ln(2)
    cat_totals = _sum_by(month_txns, "category")
    for cat_key in sorted(cat_totals.keys()):
        cat = cat_key[0]
        pdf.table_row(widths, [f"  SUBTOTAL {cat}", "", _fmt_amt(cat_totals[cat_key])],
                      bold=True, fill=True)

    grand = sum(Decimal(t["amount"]) for t in month_txns)
    pdf.ln(2)
    pdf.table_row(widths, ["GRAND TOTAL", "", _fmt_amt(grand)], bold=True, fill=True)


def _write_transaction_detail(pdf: AbacusPDF, month_txns):
    """Section 3: Transaction Detail (Month) - grouped by category/subcategory."""
    pdf.section_title("Transaction Detail (Month)")

    # Sort by category, subcategory, date
    sorted_txns = sorted(month_txns, key=lambda x: (
        x.get("category") or "", x.get("subcategory") or "", x["date"]
    ))

    widths = [20, 50, 20, 25, 20, 40]
    pdf.table_header(widths, ["Date", "Payee", "Via", "Amount", "Payor", "Note"])

    current_cat = None
    current_subcat = None

    for t in sorted_txns:
        cat = t.get("category") or ""
        subcat = t.get("subcategory") or ""

        if cat != current_cat:
            pdf.group_header(cat, level=0)
            current_cat = cat
            current_subcat = None

        if subcat and subcat != current_subcat:
            pdf.group_header(subcat, level=1)
            current_subcat = subcat

        pdf.table_row(widths, [
            t["date"], t.get("payee") or "", t.get("via") or "",
            _fmt_amt(t["amount"]), t.get("payor") or "", t.get("note") or "",
        ])


def _write_tax_items(pdf: AbacusPDF, month_txns, ytd_txns):
    """Section 4: Tax Items Report (Month & YTD) - grouped by tax flag."""
    pdf.section_title("Tax Items (Month & YTD)")

    def _by_flag(txns):
        by_flag: dict[str, list] = {}
        for t in txns:
            if not t.get("tax_flags"):
                continue
            for flag in t["tax_flags"].split(","):
                flag = flag.strip()
                if flag:
                    by_flag.setdefault(flag, []).append(t)
        return by_flag

    month_by_flag = _by_flag(month_txns)
    ytd_by_flag = _by_flag(ytd_txns)
    all_flags = sorted(set(month_by_flag.keys()) | set(ytd_by_flag.keys()))

    if not all_flags:
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 6, "No tax-flagged transactions in this period.")
        return

    widths = [20, 50, 40, 25, 40]

    for flag in all_flags:
        pdf.group_header(flag, level=0)
        pdf.table_header(widths, ["Date", "Payee", "Category", "Amount", "Note"])

        m_txns = month_by_flag.get(flag, [])
        y_txns = ytd_by_flag.get(flag, [])

        for t in sorted(m_txns, key=lambda x: x["date"]):
            cat_label = t.get("category") or ""
            if t.get("subcategory"):
                cat_label += f" / {t['subcategory']}"
            pdf.table_row(widths, [
                t["date"], t.get("payee") or "", cat_label,
                _fmt_amt(t["amount"]), t.get("note") or "",
            ])

        m_total = sum(Decimal(t["amount"]) for t in m_txns)
        y_total = sum(Decimal(t["amount"]) for t in y_txns)
        pdf.subtotal_row(f"Month subtotal - {flag}", _fmt_amt(m_total), indent=1)
        pdf.subtotal_row(f"YTD subtotal - {flag}", _fmt_amt(y_total), indent=1)
        pdf.ln(2)


def _write_checksums(pdf: AbacusPDF, conn, start, end,
                     month_txns, ytd_txns,
                     month_no_xfer, ytd_no_xfer, month_xfer):
    """Checksums page - cross-reference counts and totals for integrity."""
    pdf.section_title("Checksums - Data Integrity Verification")

    year = end[:4]
    ytd_start = f"{year}-01-01"

    # Database counts (all statuses)
    db_month_all = conn.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total "
        "FROM transactions WHERE date >= ? AND date <= ?",
        (start, end),
    ).fetchone()

    db_ytd_all = conn.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total "
        "FROM transactions WHERE date >= ? AND date <= ?",
        (ytd_start, end),
    ).fetchone()

    # Database counts (confirmed only)
    db_month_confirmed = conn.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total "
        "FROM transactions WHERE date >= ? AND date <= ? AND status = 'confirmed'",
        (start, end),
    ).fetchone()

    db_ytd_confirmed = conn.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total "
        "FROM transactions WHERE date >= ? AND date <= ? AND status = 'confirmed'",
        (ytd_start, end),
    ).fetchone()

    # Pending/needs_review in period
    db_month_pending = conn.execute(
        "SELECT COUNT(*) as cnt FROM transactions "
        "WHERE date >= ? AND date <= ? AND status != 'confirmed'",
        (start, end),
    ).fetchone()["cnt"]

    db_ytd_pending = conn.execute(
        "SELECT COUNT(*) as cnt FROM transactions "
        "WHERE date >= ? AND date <= ? AND status != 'confirmed'",
        (ytd_start, end),
    ).fetchone()["cnt"]

    # Report totals (what's actually in the report sections)
    rpt_month_count = len(month_txns)
    rpt_month_total = sum(Decimal(t["amount"]) for t in month_txns)
    rpt_month_no_xfer_count = len(month_no_xfer)
    rpt_month_no_xfer_total = sum(Decimal(t["amount"]) for t in month_no_xfer)
    rpt_xfer_count = len(month_xfer)
    rpt_xfer_total = sum(Decimal(t["amount"]) for t in month_xfer)

    rpt_ytd_count = len(ytd_txns)
    rpt_ytd_total = sum(Decimal(t["amount"]) for t in ytd_txns)
    rpt_ytd_no_xfer_count = len(ytd_no_xfer)
    rpt_ytd_no_xfer_total = sum(Decimal(t["amount"]) for t in ytd_no_xfer)

    # By source
    month_by_source = {}
    for t in month_txns:
        src = t.get("source") or "Unknown"
        if src not in month_by_source:
            month_by_source[src] = {"count": 0, "total": Decimal(0)}
        month_by_source[src]["count"] += 1
        month_by_source[src]["total"] += Decimal(t["amount"])

    # Render
    widths = [100, 25, 35]
    pdf.table_header(widths, ["Metric", "Count", "Amount"])

    pdf.group_header(f"Month: {start} to {end}", level=0)
    pdf.table_row(widths, ["Database total (all statuses)",
                           str(db_month_all["cnt"]),
                           _fmt_amt(db_month_all["total"])])
    pdf.table_row(widths, ["Database confirmed",
                           str(db_month_confirmed["cnt"]),
                           _fmt_amt(db_month_confirmed["total"])])
    pdf.table_row(widths, ["Database pending/needs_review",
                           str(db_month_pending), ""])
    pdf.table_row(widths, ["Report total (confirmed, incl transfers)",
                           str(rpt_month_count),
                           _fmt_amt(rpt_month_total)])
    pdf.table_row(widths, ["Report excl transfers (used in summaries)",
                           str(rpt_month_no_xfer_count),
                           _fmt_amt(rpt_month_no_xfer_total)])
    pdf.table_row(widths, ["Transfers excluded",
                           str(rpt_xfer_count),
                           _fmt_amt(rpt_xfer_total)])

    # Match check
    month_match = (rpt_month_count == db_month_confirmed["cnt"] and
                   Decimal(str(rpt_month_total)) == Decimal(str(db_month_confirmed["total"])))
    pdf.table_row(widths, [
        "MONTH MATCH" if month_match else "*** MONTH MISMATCH ***",
        "OK" if month_match else "FAIL", ""
    ], bold=True, fill=True)

    pdf.ln(3)
    pdf.group_header(f"YTD: {ytd_start} to {end}", level=0)
    pdf.table_row(widths, ["Database total (all statuses)",
                           str(db_ytd_all["cnt"]),
                           _fmt_amt(db_ytd_all["total"])])
    pdf.table_row(widths, ["Database confirmed",
                           str(db_ytd_confirmed["cnt"]),
                           _fmt_amt(db_ytd_confirmed["total"])])
    pdf.table_row(widths, ["Database pending/needs_review",
                           str(db_ytd_pending), ""])
    pdf.table_row(widths, ["Report total (confirmed, incl transfers)",
                           str(rpt_ytd_count),
                           _fmt_amt(rpt_ytd_total)])
    pdf.table_row(widths, ["Report excl transfers",
                           str(rpt_ytd_no_xfer_count),
                           _fmt_amt(rpt_ytd_no_xfer_total)])

    ytd_match = (rpt_ytd_count == db_ytd_confirmed["cnt"] and
                 Decimal(str(rpt_ytd_total)) == Decimal(str(db_ytd_confirmed["total"])))
    pdf.table_row(widths, [
        "YTD MATCH" if ytd_match else "*** YTD MISMATCH ***",
        "OK" if ytd_match else "FAIL", ""
    ], bold=True, fill=True)

    # By source breakdown
    pdf.ln(3)
    pdf.group_header("Month by Source", level=0)
    src_widths = [80, 25, 35]
    pdf.table_header(src_widths, ["Source", "Count", "Total"])
    for src in sorted(month_by_source.keys()):
        d = month_by_source[src]
        pdf.table_row(src_widths, [src, str(d["count"]), _fmt_amt(d["total"])])


# ---------------------------------------------------------------------------
# Public report generators
# ---------------------------------------------------------------------------

def generate_monthly_pdf(conn: sqlite3.Connection, start: str, end: str,
                         title: str | None = None,
                         sections: list[str] | None = None) -> Path:
    """Generate the monthly PDF report.

    sections controls which sections to include. Options:
    'category_summary', 'payee_summary', 'transaction_detail', 'tax_items'
    If None, all sections are included.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    if sections is None:
        sections = ["category_summary", "payee_summary", "transaction_detail", "tax_items"]

    month_txns = _fetch_month_transactions(conn, start, end)
    ytd_txns = _fetch_ytd_transactions(conn, end)

    # Separate transfers
    month_no_xfer = [t for t in month_txns if t.get("category") != "Transfer"]
    ytd_no_xfer = [t for t in ytd_txns if t.get("category") != "Transfer"]
    month_xfer = [t for t in month_txns if t.get("category") == "Transfer"]

    report_title = title or f"Monthly Report - {start} to {end}"
    pdf = AbacusPDF(title=report_title)
    pdf.alias_nb_pages()

    first_page = True

    if "category_summary" in sections:
        if not first_page:
            pdf.add_page()
        else:
            pdf.add_page()
            first_page = False
        _write_category_summary(pdf, month_no_xfer, ytd_no_xfer)
        if month_xfer:
            xfer_total = sum(Decimal(t["amount"]) for t in month_xfer)
            pdf.set_font("Helvetica", "I", 7)
            pdf.ln(2)
            pdf.cell(0, 4, f"Note: {len(month_xfer)} transfer(s) totaling {_fmt_amt(xfer_total)} excluded from summaries.")

    if "payee_summary" in sections:
        pdf.add_page()
        first_page = False
        _write_payee_summary(pdf, month_no_xfer)

    if "transaction_detail" in sections:
        pdf.add_page()
        first_page = False
        _write_transaction_detail(pdf, month_txns)

    if "tax_items" in sections:
        pdf.add_page()
        first_page = False
        _write_tax_items(pdf, month_no_xfer, ytd_no_xfer)

    # Always add checksums page
    pdf.add_page()
    _write_checksums(pdf, conn, start, end, month_txns, ytd_txns,
                     month_no_xfer, ytd_no_xfer, month_xfer)

    filename = f"Monthly_Report_{start}_to_{end}.pdf"
    out_path = OUTPUT_DIR / filename
    pdf.output(str(out_path))
    return out_path


def generate_pending_items_pdf(conn: sqlite3.Connection) -> Path | None:
    """Generate a simple list of all pending transactions."""
    OUTPUT_DIR.mkdir(exist_ok=True)

    pending = queries.get_pending_transactions(conn)
    if not pending:
        return None

    pdf = AbacusPDF(title=f"Pending Items - {len(pending)} transaction(s)")
    pdf.alias_nb_pages()
    pdf.add_page()

    widths = [25, 40, 28, 120, 50]
    pdf.table_header(widths, ["Date", "Source", "Amount", "Description", "Note"])

    for t in pending:
        pdf.table_row(widths, [
            t["date"], t["source"], _fmt_amt(t["amount"]),
            t["description_raw"], t["note"] or "",
        ])

    out_path = OUTPUT_DIR / "Pending_Items.pdf"
    pdf.output(str(out_path))
    return out_path


def generate_excel_export(conn: sqlite3.Connection, start: str, end: str) -> Path:
    """Generate an Excel export of all transactions in a date range."""
    OUTPUT_DIR.mkdir(exist_ok=True)

    rows = queries.get_transactions(conn, start_date=start, end_date=end)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "All Transactions"

    headers = [
        "Date", "Amount", "Check Number", "Description (Raw)", "Category (Raw)",
        "Payee", "Via", "Payor", "Category", "Subcategory", "Tax Flags",
        "Note", "Source", "Status", "Overridden",
    ]
    field_keys = [
        "date", "amount", "check_number", "description_raw", "category_raw",
        "payee", "via", "payor", "category", "subcategory", "tax_flags",
        "note", "source", "status", "overridden",
    ]

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = openpyxl.styles.Font(bold=True)

    for row_idx, txn in enumerate(rows, 2):
        for col_idx, key in enumerate(field_keys, 1):
            val = txn[key]
            if key == "amount":
                val = float(val) if val else 0.0
            elif key == "overridden":
                val = bool(val)
            ws.cell(row=row_idx, column=col_idx, value=val)

    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"

    for col_idx, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = max(len(h) + 4, 12)

    filename = f"Transactions_{start}_to_{end}.xlsx"
    out_path = OUTPUT_DIR / filename
    wb.save(str(out_path))
    return out_path
