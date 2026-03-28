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
    # fpdf2 uses latin-1 by default with built-in fonts; replace problem chars
    return str(val).encode("latin-1", errors="replace").decode("latin-1")


# ---------------------------------------------------------------------------
# PDF helper class
# ---------------------------------------------------------------------------

class AbacusPDF(FPDF):
    """Landscape letter PDF with consistent headers/footers."""

    def __init__(self, title: str = "Abacus Report"):
        super().__init__(orientation="L", unit="mm", format="Letter")
        self._title = title
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
        self.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
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


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _write_category_summary(pdf: AbacusPDF, month_txns, ytd_txns):
    """Section 1: Category Summary (Month & YTD)."""
    pdf.section_title("Section 1 - Category Summary (Month & YTD)")

    widths = [60, 60, 35, 35]
    pdf.table_header(widths, ["Category", "Subcategory", "Month Total", "YTD Total"])

    month_totals = _sum_by(month_txns, "category", "subcategory")
    ytd_totals = _sum_by(ytd_txns, "category", "subcategory")
    all_keys = sorted(set(month_totals.keys()) | set(ytd_totals.keys()))

    current_cat = None
    for key in all_keys:
        cat, subcat = key
        if cat != current_cat:
            cat_month = sum(v for k, v in month_totals.items() if k[0] == cat)
            cat_ytd = sum(v for k, v in ytd_totals.items() if k[0] == cat)
            pdf.table_row(widths, [cat, "", _fmt_amt(cat_month), _fmt_amt(cat_ytd)],
                          bold=True, fill=True)
            current_cat = cat
        if subcat:
            m = month_totals.get(key, Decimal(0))
            y = ytd_totals.get(key, Decimal(0))
            pdf.table_row(widths, ["", subcat, _fmt_amt(m), _fmt_amt(y)])

    grand_month = sum(month_totals.values(), Decimal(0))
    grand_ytd = sum(ytd_totals.values(), Decimal(0))
    pdf.table_row(widths, ["GRAND TOTAL", "", _fmt_amt(grand_month), _fmt_amt(grand_ytd)],
                  bold=True, fill=True)


def _write_payee_summary(pdf: AbacusPDF, month_txns):
    """Section 2: Payee Summary (Month)."""
    pdf.section_title("Section 2 - Payee Summary (Month)")

    widths = [45, 45, 55, 15, 30]
    pdf.table_header(widths, ["Category", "Subcategory", "Payee", "# Txns", "Total"])

    groups: dict[tuple, list] = {}
    for t in month_txns:
        key = (t.get("category") or "", t.get("subcategory") or "", t.get("payee") or "")
        groups.setdefault(key, []).append(t)

    for key in sorted(groups.keys()):
        cat, subcat, payee = key
        txns = groups[key]
        total = sum(Decimal(t["amount"]) for t in txns)
        pdf.table_row(widths, [cat, subcat, payee, str(len(txns)), _fmt_amt(total)])


def _write_transaction_detail(pdf: AbacusPDF, month_txns):
    """Section 3: Transaction Detail (Month)."""
    pdf.section_title("Section 3 - Transaction Detail (Month)")

    widths = [20, 30, 30, 30, 45, 20, 25, 20, 40]
    pdf.table_header(widths, [
        "Date", "Source", "Category", "Subcategory", "Payee", "Via", "Amount", "Payor", "Note"
    ])

    for t in sorted(month_txns, key=lambda x: (x["date"], x["source"])):
        pdf.table_row(widths, [
            t["date"], t.get("source") or "", t.get("category") or "",
            t.get("subcategory") or "", t.get("payee") or "", t.get("via") or "",
            _fmt_amt(t["amount"]), t.get("payor") or "", t.get("note") or "",
        ])


def _write_tax_items(pdf: AbacusPDF, month_txns, ytd_txns):
    """Section 4: Tax Items Report (Month & YTD)."""
    pdf.section_title("Section 4 - Tax Items (Month & YTD)")

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

    widths = [20, 45, 35, 35, 28, 30, 40]

    for flag in all_flags:
        pdf.set_font("Helvetica", "B", 8)
        pdf.ln(2)
        pdf.cell(0, 5, flag, new_x="LMARGIN", new_y="NEXT")

        pdf.table_header(widths, [
            "Date", "Payee", "Category", "Subcategory", "Amount", "Tax Flag", "Note"
        ])

        m_txns = month_by_flag.get(flag, [])
        y_txns = ytd_by_flag.get(flag, [])

        for t in sorted(m_txns, key=lambda x: x["date"]):
            pdf.table_row(widths, [
                t["date"], t.get("payee") or "", t.get("category") or "",
                t.get("subcategory") or "", _fmt_amt(t["amount"]), flag,
                t.get("note") or "",
            ])

        m_total = sum(Decimal(t["amount"]) for t in m_txns)
        y_total = sum(Decimal(t["amount"]) for t in y_txns)
        pdf.table_row(widths, ["", "", "", f"Month subtotal:", _fmt_amt(m_total), "", ""],
                      bold=True, fill=True)
        pdf.table_row(widths, ["", "", "", f"YTD subtotal:", _fmt_amt(y_total), "", ""],
                      bold=True, fill=True)


# ---------------------------------------------------------------------------
# Public report generators
# ---------------------------------------------------------------------------

def generate_monthly_pdf(conn: sqlite3.Connection, start: str, end: str,
                         title: str | None = None) -> Path:
    """Generate the full monthly PDF report. Returns the output file path."""
    OUTPUT_DIR.mkdir(exist_ok=True)

    month_txns = _fetch_month_transactions(conn, start, end)
    ytd_txns = _fetch_ytd_transactions(conn, end)

    # Separate transfers
    month_no_xfer = [t for t in month_txns if t.get("category") != "Transfer"]
    ytd_no_xfer = [t for t in ytd_txns if t.get("category") != "Transfer"]
    month_xfer = [t for t in month_txns if t.get("category") == "Transfer"]

    report_title = title or f"Monthly Report - {start} to {end}"
    pdf = AbacusPDF(title=report_title)
    pdf.alias_nb_pages()
    pdf.add_page()

    _write_category_summary(pdf, month_no_xfer, ytd_no_xfer)

    if month_xfer:
        xfer_total = sum(Decimal(t["amount"]) for t in month_xfer)
        pdf.set_font("Helvetica", "I", 7)
        pdf.ln(2)
        pdf.cell(0, 4, f"Note: {len(month_xfer)} transfer(s) totaling {_fmt_amt(xfer_total)} excluded from summaries.")
        pdf.ln(2)

    pdf.add_page()
    _write_payee_summary(pdf, month_no_xfer)

    pdf.add_page()
    _write_transaction_detail(pdf, month_txns)

    pdf.add_page()
    _write_tax_items(pdf, month_no_xfer, ytd_no_xfer)

    filename = f"Monthly_Report_{start}_to_{end}.pdf"
    out_path = OUTPUT_DIR / filename
    pdf.output(str(out_path))
    return out_path


def generate_pending_items_pdf(conn: sqlite3.Connection) -> Path | None:
    """Generate a simple list of all pending transactions. Returns output path or None."""
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
            t["description_raw"], t.get("note") or "",
        ])

    out_path = OUTPUT_DIR / "Pending_Items.pdf"
    pdf.output(str(out_path))
    return out_path


def generate_excel_export(conn: sqlite3.Connection, start: str, end: str) -> Path:
    """Generate an Excel export of all transactions in a date range. Returns output path."""
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

    # Header row
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = openpyxl.styles.Font(bold=True)

    # Data rows
    for row_idx, txn in enumerate(rows, 2):
        for col_idx, key in enumerate(field_keys, 1):
            val = txn[key]
            if key == "amount":
                val = float(val) if val else 0.0
            elif key == "overridden":
                val = bool(val)
            ws.cell(row=row_idx, column=col_idx, value=val)

    # Auto-filter
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"

    # Adjust column widths
    for col_idx, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = max(len(h) + 4, 12)

    filename = f"Transactions_{start}_to_{end}.xlsx"
    out_path = OUTPUT_DIR / filename
    wb.save(str(out_path))
    return out_path
