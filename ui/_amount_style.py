"""Shared styling helpers for displaying amounts consistently across the UI.

Convention: credits (positive amounts) appear in green; debits (negative) stay
in the default text color. Used by AG Grid cells, Pandas Styler tables, and
inline markdown wherever a dollar amount appears.
"""

from st_aggrid import JsCode


# GitHub-style bright green that reads well on dark themes.
GREEN_HEX = "#3fb950"


# JsCode for AG Grid cellStyle. Use as:
#     gb.configure_column("Amount", ..., cellStyle=amount_cell_style())
def amount_cell_style() -> JsCode:
    return JsCode(f"""
        function(params) {{
            if (typeof params.value === 'number' && params.value > 0) {{
                return {{color: '{GREEN_HEX}'}};
            }}
            return null;
        }}
    """)


# JsCode for AG Grid valueFormatter. Formats positive as "$X.XX" and negative
# as "-$X.XX" (sign before the dollar sign, not "$-X.XX").
def amount_value_formatter() -> JsCode:
    return JsCode("""
        function(params) {
            if (params.value == null) return '';
            const v = Number(params.value);
            const abs = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
            return v < 0 ? `-$${abs}` : `$${abs}`;
        }
    """)


def format_signed_amount(v) -> str:
    """Format a numeric amount as '$X.XX' or '-$X.XX'."""
    if v is None or v == "":
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v < 0:
        return f"-${abs(v):,.2f}"
    return f"${v:,.2f}"


def styler_for_amount_column(df, column: str = "Amount"):
    """Return a Pandas Styler that formats `column` as signed currency and
    colors positive values green. Apply to st.dataframe via:

        st.dataframe(styler_for_amount_column(df), ...)
    """
    def _color(v):
        try:
            return f"color: {GREEN_HEX}" if float(v) > 0 else ""
        except (TypeError, ValueError):
            return ""

    return df.style.format({column: format_signed_amount}).map(_color, subset=[column])
