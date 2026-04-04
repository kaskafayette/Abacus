"""Step 3: payee normalization."""

import re
import sqlite3

from db import queries

# Known payment intermediaries — if found in the raw description, extracted into `via`
VIA_PATTERNS = [
    (re.compile(r"^SQ \*", re.IGNORECASE), "Square"),
    (re.compile(r"^TST\*", re.IGNORECASE), "Toast"),
    (re.compile(r"^SP ", re.IGNORECASE), "Shopify"),
    (re.compile(r"^SPO\*", re.IGNORECASE), "SpotOn"),
    (re.compile(r"^EB \*", re.IGNORECASE), "Eventbrite"),
    (re.compile(r"^OTT\*\s*", re.IGNORECASE), "OTT"),
    (re.compile(r"^SG\*V?\*?", re.IGNORECASE), "Spring"),
    (re.compile(r"^DNH\*", re.IGNORECASE), "DNH"),
    (re.compile(r"^TCB\*", re.IGNORECASE), "TCB"),
    (re.compile(r"^D J\*", re.IGNORECASE), "DJ"),
]

# Starter normalization rules derived from real input data.
# Each tuple: (search_pattern, normalized_name)
# These are loaded into payee_normalization on first use.
SEED_NORMALIZATION_RULES = [
    # Amazon family
    ("AMAZON MKTPL", "Amazon"),
    ("AMAZON MKTPLACE", "Amazon"),
    ("Amazon.com", "Amazon"),
    ("AMAZON PRIME", "Amazon Prime"),
    ("Prime Video", "Amazon Prime Video"),
    ("AMZNPharma", "Amazon Pharmacy"),
    ("Audible*", "Audible"),

    # Streaming / subscriptions
    ("Roku for CBS Interactive", "Paramount+ (Roku)"),
    ("Roku for Hulu LLC", "Hulu (Roku)"),
    ("Roku for Starz", "Starz (Roku)"),
    ("Roku for The Criterion Co", "Criterion Channel (Roku)"),
    ("NETFLIX", "Netflix"),
    ("Netflix.com", "Netflix"),
    ("SXM*SIRIUSXM", "SiriusXM"),
    ("GOOGLE *Google One", "Google One"),
    ("OPENAI *CHATGPT", "OpenAI ChatGPT"),
    ("CLAUDE.AI SUBSCRIPTION", "Claude AI"),
    ("ANTHROPIC", "Anthropic"),
    ("discovery+", "Discovery+"),
    ("MEDIUM MONTHLY", "Medium"),
    ("Lumosity.com", "Lumosity"),
    ("Scribd ", "Scribd"),
    ("EDDIEH.SUBSTACK.COM", "Substack (Eddie H)"),

    # Utilities / bills
    ("PG&E/EZ-PAY", "PG&E"),
    ("PG&amp;E/EZ-PAY", "PG&E"),
    ("SONIC.NET", "Sonic.net"),
    ("VZWRLSS*APOCC", "Verizon Wireless"),
    ("BLUE SHIELD CALIFORNIA", "Blue Shield of California"),
    ("ANTHEM BLUE      MED SUPP", "Anthem Blue Cross"),
    ("SUNSET SCAVENGER", "Recology"),
    ("SF SERVICE FEE", "SF Service Fee"),
    ("TTX BUSINESS TAX", "SF Business Tax"),
    ("MEDICAL/RX INS", "Medical/Rx Insurance"),
    ("ZOOM.COM", "Zoom"),
    ("VOIP.MS", "VoIP.ms"),
    ("SF CHRONICLE SUBSCRIPT", "SF Chronicle"),
    ("THE ECONOMIST", "The Economist"),
    ("TIINGO.COM", "Tiingo"),

    # Groceries
    ("WHOLEFDS", "Whole Foods"),
    ("SAFEWAY", "Safeway"),
    ("TRADER JOE", "Trader Joe's"),
    ("CHURCH PRODUCE", "Church Produce"),
    ("CANYON MARKET", "Canyon Market"),
    ("FALLETTI FOODS", "Falletti Foods"),
    ("GOOD EARTH NATURAL", "Good Earth Natural Foods"),
    ("ANDY'S PRODUCE", "Andy's Produce Market"),
    ("BRYANS QUALITY", "Bryan's Quality Meats"),
    ("CHOCOLATE COVERED", "Chocolate Covered"),
    ("NUTRAFOL", "Nutrafol"),

    # Coffee / cafes
    ("MARTHA & BROS", "Martha & Bros Coffee"),
    ("MARTHA &amp; BROS", "Martha & Bros Coffee"),
    ("SQ *MARTHA AND BROTHERS", "Martha & Bros Coffee"),
    ("PEETS ", "Peet's Coffee"),
    ("KULI COFFEE", "Kuli Coffee"),
    ("ZOOMCAFFE", "Zoom Caffe"),
    ("SQ *BERNIE'S COFFEE", "Bernie's Coffee"),
    ("SQ *LA LUCHA COFFEE", "La Lucha Coffee"),
    ("SQ *BONES BAGELS", "Bones Bagels"),
    ("SQ *HOLEY BAGEL", "Holey Bagel"),

    # Restaurants
    ("LA CORNETA TAQUERIA", "La Corneta Taqueria"),
    ("CASA MEXICANA", "Casa Mexicana"),
    ("ERICS RESTAURANT", "Eric's Restaurant"),
    ("ZUNI CAFE", "Zuni Cafe"),
    ("LUPA", "Lupa"),
    ("LEQUY SF", "LeQuy SF"),
    ("DUBLINER BAR", "Dubliner Bar"),
    ("RH MARIN RESTAURANT", "RH Restaurant"),
    ("ALICES RESTAURANT", "Alice's Restaurant"),
    ("HOBIE'S SAND BAR", "Hobie's Sand Bar"),
    ("PEDROS TACOS", "Pedro's Tacos"),
    ("SEASURF SAN CLEMENTE", "SeaSurf"),
    ("KING YEN RESTAURANT", "King Yen Restaurant"),
    ("MITCHELL'S ICE CREAM", "Mitchell's Ice Cream"),
    ("SEES CANDY", "See's Candies"),
    ("TST*NOVY RESTAURANT", "Novy Restaurant"),
    ("TST*FIREFLY RESTAURANT", "Firefly Restaurant"),
    ("TST*VIA DEL CORSO", "Via Del Corso"),
    ("TST*BANGKOK BY THE BAY", "Bangkok by the Bay"),
    ("TST*BARONS QUALITY MEAT", "Baron's Quality Meats"),
    ("TST*CHEZ MAMAN", "Chez Maman"),
    ("TST*HI-WAY BURGER", "Hi-Way Burger"),
    ("TST*JACKS RESTAURANT", "Jack's Restaurant"),
    ("TST*LA BOULANGERIE", "La Boulangerie"),
    ("TST*MOULIN", "Moulin"),
    ("TST*RASA", "Rasa"),
    ("TST*RITUAL COFFEE", "Ritual Coffee"),
    ("TST*TAFFIS CAFE", "Taffi's Cafe"),
    ("TST*WATERSHED", "Watershed"),
    ("TST*ABSINTHE", "Absinthe"),
    ("TST*CHAUPAIN BAKERY", "Chaupain Bakery"),
    ("TST*D BOOKSTORE", "D Bookstore & Cafe"),
    ("TST*GOOD EARTH", "Good Earth Natural Foods"),
    ("TST* ARLEQUIN WINE", "Arlequin Wine Merchant"),
    ("TST*4505 BURGERS", "4505 Burgers & BBQ"),
    ("SQ *NOE VALLEY BAKERY", "Noe Valley Bakery"),
    ("SQ *CHLOE'S CAFE", "Chloe's Cafe"),
    ("SQ *BILLINGSGATE", "Billingsgate"),
    ("SQ *VIVE LA TARTE", "Vive La Tarte"),
    ("SPO*WATERBARRESTAURANT", "Waterbar Restaurant"),
    ("SQ *DANNY'S CLEANERS", "Danny's Cleaners"),

    # Shopping / retail
    ("J CREW", "J.Crew"),
    ("ATHLETA.COM", "Athleta"),
    ("ANTHROPOLOGIE.COM", "Anthropologie"),
    ("Garnet Hill", "Garnet Hill"),
    ("FARMGIRL FLOWERS", "Farmgirl Flowers"),
    ("URBAN FLOWERS", "Urban Flowers"),
    ("OtherStories", "& Other Stories"),
    ("LANDS END", "Lands' End"),
    ("WRAPLONDON.COM", "Wrap London"),
    ("JOCKEY INTERNATIONAL", "Jockey"),
    ("POTTERY BARN", "Pottery Barn"),
    ("COMPUPOD", "Compupod"),
    ("Etsy.com", "Etsy"),
    ("ONEQUINCE", "Quince"),
    ("Noe Valley Books", "Noe Valley Books"),
    ("SNA South Coast News", "South Coast News"),
    ("SP JONES ROAD BEAUTY", "Jones Road Beauty"),
    ("SP JP BODEN", "Boden"),
    ("SP NOMADLANETHANKYOU", "Nomad Lane"),
    ("SP UNBOUND MERINO", "Unbound Merino"),
    ("SP SODASTREAM", "SodaStream"),
    ("SP SIGNSOFJUSTICE", "Signs of Justice"),
    ("SP TINY AND SNAIL", "Tiny and Snail"),
    ("SP READERS CAT", "New York Review of Books"),
    ("SP RUMI COSMETIQUES", "Rumi Cosmetiques"),
    ("SP CHOPSAVER", "ChopSaver"),
    ("SP EVANI", "Evani"),
    ("SP AZIL BOUTIQUE", "Azil Boutique"),
    ("SP WOYUOSN", "Woyuosn"),
    ("SP ZAFIRA", "Zafira"),
    ("SP PENDULUM THERAPEU", "Pendulum Therapeutics"),
    ("SQ *DRESSERS", "Dressers"),
    ("SQ *ACORN SHOP", "Acorn Shop"),
    ("SQ *DOWNTOWN BOUTIQUE", "Downtown Boutique"),
    ("SQ *WINK SF", "Wink SF"),
    ("SQ *VIDEO WAVE", "Video Wave"),
    ("SQ *CHRISTOPHER ELBOW", "Christopher Elbow Chocolates"),
    ("SQ *MANNY'S", "Manny's"),
    ("HARMONY FS&N", "Harmony Farm Supply"),
    ("HARMONY FS&amp;N", "Harmony Farm Supply"),
    ("ALTER", "Alter"),
    ("DEPARTURES @SFO", "Departures (SFO)"),
    ("SG*V*DresslyApp", "Dressly"),

    # Health / wellness
    ("CBT CENTER", "CBT Center"),
    ("COCOON DAY SPA", "Cocoon Day Spa"),
    ("BETTER LIVING THROUGH DEN", "Better Living Through Dentistry"),
    ("WALGREENS", "Walgreens"),
    ("GiftHealth*LillyDirect", "Lilly Direct"),
    ("FANDANGO", "Fandango"),

    # Travel
    ("UNITED      ", "United Airlines"),
    ("ALASKA AIR", "Alaska Airlines"),
    ("FASTRAK CSC", "FasTrak"),
    ("IMPARK", "Impark Parking"),
    ("LAZ PARKING", "LAZ Parking"),
    ("SHC HOOVER PARKING", "Stanford Parking"),
    ("CITY OF MILL VALLEY", "City of Mill Valley"),
    ("CITY OF BURLINGAME", "City of Burlingame"),
    ("WAYMO", "Waymo"),
    ("Europcar.com", "Europcar"),
    ("LYFT   *", "Lyft"),
    ("TCB*MTA METER", "MTA Parking Meter"),

    # Gas
    ("CHEVRON", "Chevron"),
    ("SHELL OIL", "Shell"),
    ("76 - HUSARY", "76 Gas"),
    ("VALENCIA GAS", "Valencia Gas"),

    # Services / personal
    ("THE UPS STORE", "UPS Store"),
    ("USPS PO", "USPS"),
    ("Supercuts", "Supercuts"),
    ("NOE VALLEY AUTO WORKS", "Noe Valley Auto Works"),
    ("OPERA PLAZA", "Opera Plaza Cinema"),
    ("CLASSIC STAGE COMPANY", "Classic Stage Company"),
    ("SAN FRANCISCO SYMPHONY", "SF Symphony"),
    ("ART INSTITUTE-ADMISSIO", "Art Institute"),
    ("GIRL SCOUTS", "Girl Scouts"),
    ("USC CLASSICAL", "USC Classical California"),
    ("SQ *JOY JOY NAIL SPA", "Joy Joy Nail Spa"),
    ("THE LAW MOTHER", "The Law Mother"),
    ("EB *MEET THE CANDIDATE", "Meet the Candidate"),
    ("NCOURT *CASanFrancisco", "City of SF (Court/Tickets)"),
    ("DNH*GODADDY", "GoDaddy"),
    ("D J*WSJ ONLINE", "Wall Street Journal"),

    # Banking / transfers
    ("VENMO            PAYMENT", "Venmo Payment"),
    ("APPLECARD GSBANK PAYMENT", "Apple Card Payment"),
    ("Payment to Chase card ending in 6190", "Chase Card Payment (6190)"),
    ("Payment to Chase card ending in 7529", "Chase Card Payment (7529)"),
    ("Payment to Chase card ending in 7625", "Chase Card Payment (7625)"),
    ("Payment Thank You - Web", "Chase Payment Received"),
    ("MONTHLY SERVICE FEE", "Chase Monthly Service Fee"),
    ("ANNUAL MEMBERSHIP FEE", "Chase Annual Membership Fee"),
    ("FID BKG SVC LLC  MONEYLINE", "Fidelity Transfer"),
    ("FID BKG SVC LLC  ACH", "Fidelity Transfer"),
    ("NOE VALLEY ASSOC PAYROLL", "Noe Valley Association Payroll"),
    ("SSA  TREAS 310   XXSOC SEC", "Social Security"),
    ("MICROSOFT#G", "Microsoft"),
    ("WITHDRAWAL", "Cash Withdrawal"),
]


def seed_normalization_rules(conn: sqlite3.Connection) -> int:
    """Load starter normalization rules if the table is empty. Returns count inserted."""
    existing = queries.get_payee_normalizations(conn)
    if existing:
        return 0
    for pattern, name in SEED_NORMALIZATION_RULES:
        queries.insert_payee_normalization(conn, pattern, name)
    return len(SEED_NORMALIZATION_RULES)


# Zelle patterns: "Zelle payment to NAME REF" or "Zelle payment from NAME REF"
_ZELLE_RE = re.compile(
    r"Zelle payment (?:to|from)\s+(.+?)\s+(?:JPM|CHA|WF|BOA|USB)\w+\s*$",
    re.IGNORECASE,
)
_ZELLE_RE_SIMPLE = re.compile(
    r"Zelle payment (?:to|from)\s+(.+?)$",
    re.IGNORECASE,
)


def _extract_zelle_payee(description: str) -> str | None:
    """Extract the payee name from a Zelle transaction description."""
    m = _ZELLE_RE.search(description)
    if m:
        return m.group(1).strip().title()
    m = _ZELLE_RE_SIMPLE.search(description)
    if m:
        # Strip trailing reference code (alphanumeric chunk at end)
        name = re.sub(r"\s+\S*\d\S*$", "", m.group(1).strip())
        return name.title() if name else m.group(1).strip().title()
    return None


# Online Bill Payment: "Online Payment <ref> To <PAYEE> <date>"
_ONLINE_PMT_RE = re.compile(
    r"Online Payment\s+\d+\s+To\s+(.+?)\s+\d{2}/\d{2}\s*$",
    re.IGNORECASE,
)


def _extract_online_payment_payee(description: str) -> str | None:
    """Extract the payee name from an Online Payment description."""
    m = _ONLINE_PMT_RE.search(description)
    if m:
        return m.group(1).strip().title()
    return None


def detect_via(description: str) -> str | None:
    """Detect a payment intermediary from a raw description."""
    for pattern, via_name in VIA_PATTERNS:
        if pattern.search(description):
            return via_name
    return None


def strip_via_prefix(description: str) -> str:
    """Strip the VIA intermediary prefix from a raw description for display."""
    for pattern, _ in VIA_PATTERNS:
        description = pattern.sub("", description)
    return description.strip()


def auto_suggest_payee(description: str) -> str:
    """Generate a clean suggested payee name from a raw description.

    Strips VIA prefixes, trailing reference numbers, dates, IDs,
    HTML entities, store numbers, and applies title case.
    """
    name = description

    # Strip known VIA prefixes
    for pattern, _ in VIA_PATTERNS:
        name = pattern.sub("", name)

    # Fix HTML entities from Chase CSVs
    name = name.replace("&amp;", "&")

    # Strip trailing reference numbers, dates, IDs, store numbers
    name = re.sub(r"\s+\d{5,}.*$", "", name)          # long trailing numbers (5+ digits)
    name = re.sub(r"\s+\d{2}/\d{2}$", "", name)       # trailing MM/DD
    name = re.sub(r"\s+\d{2}/\d{2}/\d{2,4}$", "", name)  # trailing date
    name = re.sub(r"\s+PPD ID:.*$", "", name)          # ACH PPD IDs
    name = re.sub(r"\s+WEB ID:.*$", "", name)          # WEB IDs
    name = re.sub(r"\s*#\S+$", "", name)               # trailing #ref
    name = re.sub(r"\s*\*\S+$", "", name)              # trailing *ref
    name = re.sub(r"\s+\d{3,}$", "", name)             # trailing 3+ digit store number

    # Strip common prefixes that indicate intermediaries not in VIA_PATTERNS
    name = re.sub(r"^AT\s*\*\s*", "", name, flags=re.IGNORECASE)  # AT * (e.g. AT * Whitney Museum)

    # Clean up extra whitespace and punctuation
    name = name.strip(" ,-.*")
    name = re.sub(r"\s{2,}", " ", name)

    # Title case if it's all caps or all lower
    if name == name.upper() or name == name.lower():
        name = name.title()

    return name if name else description


def normalize_transactions(conn: sqlite3.Connection, transaction_ids: list[int] | None = None):
    """Run payee normalization on pending transactions.

    Returns (matched_count, unmatched_rows) where unmatched_rows is a list of
    dicts with keys: id, description_raw, cleaned_desc, via, date, amount, source.
    """
    if transaction_ids:
        placeholders = ",".join("?" * len(transaction_ids))
        rows = conn.execute(
            f"SELECT * FROM transactions WHERE id IN ({placeholders})",
            transaction_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE payee IS NULL AND status = 'pending'"
        ).fetchall()

    matched = 0
    unmatched = []

    for row in rows:
        raw = row["description_raw"]
        via = detect_via(raw)

        # Special case: Zelle — extract payee name from description
        zelle_payee = _extract_zelle_payee(raw)
        if zelle_payee:
            queries.update_transaction(conn, row["id"], payee=zelle_payee, via="Zelle")
            matched += 1
            continue

        # Special case: Online Bill Payment — extract payee from "To <NAME>"
        online_payee = _extract_online_payment_payee(raw)
        if online_payee:
            queries.update_transaction(conn, row["id"], payee=online_payee, via="Chase BillPay")
            matched += 1
            continue

        match = queries.find_payee_match(conn, raw)
        if match:
            updates = {"payee": match["normalized_name"]}
            if via:
                updates["via"] = via
            queries.update_transaction(conn, row["id"], **updates)
            matched += 1
        else:
            unmatched.append({
                "id": row["id"],
                "description_raw": raw,
                "cleaned_desc": strip_via_prefix(raw),
                "suggested_name": auto_suggest_payee(raw),
                "via": via,
                "date": row["date"],
                "amount": row["amount"],
                "source": row["source"],
            })

    # Sort unmatched alphabetically by cleaned description
    unmatched.sort(key=lambda x: x["cleaned_desc"].lower())

    return matched, unmatched


def apply_pattern_rule(conn: sqlite3.Connection, search_pattern: str,
                       normalized_name: str) -> int:
    """Create a normalization rule and apply it to all matching pending transactions.

    Returns the number of transactions matched and updated.
    """
    # Save the rule
    queries.insert_payee_normalization(conn, search_pattern, normalized_name)

    # Apply to all pending transactions with no payee
    rows = conn.execute(
        "SELECT * FROM transactions WHERE payee IS NULL AND status = 'pending'"
    ).fetchall()

    pattern_lower = search_pattern.lower()
    count = 0
    for row in rows:
        if pattern_lower in row["description_raw"].lower():
            via = detect_via(row["description_raw"])
            updates = {"payee": normalized_name}
            if via:
                updates["via"] = via
            queries.update_transaction(conn, row["id"], **updates)
            count += 1

    return count


def apply_normalization_edits(conn: sqlite3.Connection,
                               edits: list[dict]) -> None:
    """Apply user edits from the normalization review table.

    Each edit dict should have: id (transaction id), normalized_name, via (optional),
    search_pattern (the pattern to save for future matching).
    """
    for edit in edits:
        txn_id = edit["id"]
        normalized_name = edit["normalized_name"]
        via = edit.get("via")
        search_pattern = edit.get("search_pattern", "")

        # Save the normalization rule for future use
        if search_pattern and normalized_name:
            queries.insert_payee_normalization(conn, search_pattern, normalized_name)

        # Update the transaction
        updates = {"payee": normalized_name}
        if via:
            updates["via"] = via
        queries.update_transaction(conn, txn_id, **updates)
