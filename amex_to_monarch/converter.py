"""Monarch Money CSV conversion for American Express transactions and daily balances."""

import csv
import io
import re
from typing import List, Dict, Any, Optional
from amex_to_monarch.parser import parse_date


# Known merchant normalization map
KNOWN_MERCHANTS = [
    (r"\bLYFT\b", "Lyft"),
    (r"\bUBER\b(?:\s*TRIP|\s*EATS)?", "Uber"),
    (r"\bPUBLIX\b", "Publix"),
    (r"\bFOOD\s*LION\b", "Food Lion"),
    (r"\bDOLLAR\s*GENERAL\b", "Dollar General"),
    (r"\bGFIBER\b|GOOGLE\s*FIBER", "Google Fiber"),
    (r"\bTRADER\s*JOE'?S\b", "Trader Joe's"),
    (r"\bWHOLE\s*FDS\b|WHOLE\s*FOODS", "Whole Foods"),
    (r"\bHARRIS\s*TEETER\b", "Harris Teeter"),
    (r"\bKROGER\b", "Kroger"),
    (r"\bALDI\b", "Aldi"),
    (r"\bWEGMANS\b", "Wegmans"),
    (r"\bCOSTCO\b", "Costco"),
    (r"\bTARGET\b", "Target"),
    (r"\bWAL-?MART\b", "Walmart"),
    (r"\bAMAZON\b|AMZN\b", "Amazon"),
    (r"\bSTARBUCKS\b", "Starbucks"),
    (r"\bCHIPOTLE\b", "Chipotle"),
    (r"\bPANERA\b", "Panera Bread"),
    (r"\bMCDONALD'?S\b", "McDonald's"),
    (r"\bCHICK-?FIL-?A\b", "Chick-fil-A"),
    (r"\bNETFLIX\b", "Netflix"),
    (r"\bSPOTIFY\b", "Spotify"),
    (r"\bHULU\b", "Hulu"),
    (r"\bAPPLE\.COM\b|\bAPPLE\s*STORE\b", "Apple"),
    (r"\bDELTA\s*AIR\b", "Delta Air Lines"),
    (r"\bUNITED\s*AIR\b", "United Airlines"),
    (r"\bAMERICAN\s*AIR\b", "American Airlines"),
    (r"\bMARRIOTT\b", "Marriott"),
    (r"\bHILTON\b", "Hilton"),
    (r"\bAIRBNB\b", "Airbnb"),
    (r"\bSHELL\s*OIL\b|\bSHELL\b", "Shell"),
    (r"\bEXXON\b|\bMOBIL\b", "ExxonMobil"),
    (r"\bBP\s*CONNECT\b|\bBP\b", "BP"),
    (r"ONLINE\s*PAYMENT\s*-\s*THANK\s*YOU", "American Express Payment"),
    (r"MOBILE\s*PAYMENT\s*-\s*THANK\s*YOU", "American Express Payment"),
    (r"AUTOPAY\s*PAYMENT\s*-\s*THANK\s*YOU", "American Express Payment"),
    (r"PAYMENT\s*RECEIVED\s*-\s*THANK\s*YOU", "American Express Payment"),
    (r"CASH\s*BACK\s*REWARD\s*APPLIED", "American Express Cash Back"),
    (r"AMEX\s*OFFER\s*CREDIT", "Amex Offer Credit"),
    (r"ANNUAL\s*MEMBERSHIP\s*FEE", "American Express Annual Fee"),
    (r"LATE\s*PAYMENT\s*FEE", "Late Payment Fee"),
    (r"FOREIGN\s*TRANSACTION\s*FEE", "Foreign Transaction Fee"),
    (r"INTEREST\s*CHARGE", "Interest Charge"),
]


def clean_merchant_name(desc: str) -> str:
    """Extract a human-friendly merchant name from raw statement description."""
    if not desc:
        return "Unknown Merchant"

    # Normalize whitespace
    raw = re.sub(r"\s+", " ", desc).strip()

    # Match against known merchant patterns
    for pattern, name in KNOWN_MERCHANTS:
        if re.search(pattern, raw, re.IGNORECASE):
            return name

    # Strip telephone numbers (e.g. 800-123-4567 or 855-280-0278)
    clean = re.sub(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b", "", raw)

    # Strip store or transaction IDs like #1234 or 0000018
    clean = re.sub(r"#\s*\d+", "", clean)
    clean = re.sub(r"\b0{4,}\d+\b", "", clean)

    # Strip US state 2-letter codes and common suffixes at end
    clean = re.sub(r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b\s*$", "", clean)

    # Clean whitespace and trailing punctuation
    clean = re.sub(r"\s+", " ", clean).strip(" ,-.*")

    if not clean:
        return raw[:40].strip().title()

    # Fallback to title case
    return clean[:45].strip().title()


def infer_category(desc: str, amount: float, sub_type: str = "") -> str:
    """Infer Monarch category based on description, amount, and transaction sub_type."""
    desc_upper = (desc or "").upper()
    sub_upper = (sub_type or "").upper()

    # Payments & Transfers
    if "PAYMENT" in desc_upper or "PAYMENT" in sub_upper:
        return "Credit Card Payment"
    if "CASH BACK" in desc_upper or "REWARD" in desc_upper or "OFFER CREDIT" in desc_upper:
        return "Cash Back"
    if "INTEREST CHARGE" in desc_upper or "INTEREST" in desc_upper:
        return "Interest"
    if "ANNUAL" in desc_upper and "FEE" in desc_upper:
        return "Financial Fees"
    if "FEE" in desc_upper or "FEE" in sub_upper:
        return "Financial Fees"

    # Groceries
    if any(k in desc_upper for k in ["PUBLIX", "FOOD LION", "TRADER JOE", "WHOLE FDS", "WHOLE FOODS", "HARRIS TEETER", "KROGER", "ALDI", "WEGMANS", "SUPERMARKET", "GROCERY"]):
        return "Groceries"

    # Taxi & Rideshare
    if any(k in desc_upper for k in ["LYFT", "UBER", "TAXI", "CAB"]):
        return "Taxi & Ride Share"

    # Restaurants & Dining
    if any(k in desc_upper for k in ["STARBUCKS", "CHIPOTLE", "PANERA", "MCDONALD", "CHICK-FIL-A", "RESTAURANT", "CAFE", "PIZZA", "BURGER", "DINER", "BAKERY", "COFFEE"]):
        return "Restaurants & Bars"

    # Internet & Cable
    if any(k in desc_upper for k in ["GFIBER", "GOOGLE FIBER", "AT&T", "VERIZON", "SPECTRUM", "COMCAST", "XFINITY"]):
        return "Internet & Cable"

    # General Merchandise & Shopping
    if any(k in desc_upper for k in ["DOLLAR GENERAL", "TARGET", "WAL-MART", "WALMART", "AMAZON", "COSTCO"]):
        return "Shopping"

    # Entertainment & Subscriptions
    if any(k in desc_upper for k in ["NETFLIX", "SPOTIFY", "HULU", "APPLE.COM", "STEAM", "NINTENDO", "PLAYSTATION"]):
        return "Entertainment & Recreation"

    # Gas & Fuel
    if any(k in desc_upper for k in ["SHELL", "EXXON", "MOBIL", "BP", "CHEVRON", "SPEEDWAY", "WAWA", "GAS"]):
        return "Gas & Fuel"

    # Travel & Flights
    if any(k in desc_upper for k in ["DELTA", "UNITED", "AMERICAN AIR", "SOUTHWEST", "AIRLINE", "HOTEL", "MARRIOTT", "HILTON", "AIRBNB"]):
        return "Travel"

    # Default based on amount
    if amount > 0:
        return "Credit Card Payment"
    return "Shopping"


def convert_balances_to_csv(daily_balances: List[Dict[str, Any]]) -> str:
    """
    Format continuous daily balances into Monarch Money CSV format.
    Header: Date,Balance
    Date in YYYY-MM-DD format.
    Balance as decimal string with 2 decimal places.
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["Date", "Balance"])

    for row in daily_balances:
        d = row.get("date", "")
        b = float(row.get("balance", 0.0))
        writer.writerow([d, f"{b:.2f}"])

    return output.getvalue()


def convert_transactions_to_csv(
    transactions: List[Dict[str, Any]], account_display_name: str
) -> str:
    """
    Format transactions into Monarch Money CSV format.
    Header: Date,Merchant,Category,Account,Original Statement,Notes,Amount

    Monarch Credit Card Conventions:
    - Debits / Purchases / Fees are negative (outflows, e.g. -16.70).
    - Credits / Payments / Cash back are positive (inflows, e.g. +100.00).
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "Date",
        "Merchant",
        "Category",
        "Account",
        "Original Statement",
        "Notes",
        "Amount"
    ])

    for tx in transactions:
        raw_date = tx.get("post_date") or tx.get("charge_date") or tx.get("date", "")
        if not raw_date:
            continue

        try:
            iso_date = parse_date(raw_date).strftime("%Y-%m-%d")
        except Exception:
            iso_date = str(raw_date)[:10]

        desc = (tx.get("description") or "").strip()
        merchant = clean_merchant_name(desc)
        sub_type = tx.get("sub_type", "")
        tx_type = str(tx.get("type", "")).upper()
        raw_amount = float(tx.get("amount", 0.0))

        # In Monarch: charges are negative, payments/credits are positive
        if tx_type == "CREDIT" or "PAYMENT" in str(sub_type).upper():
            signed_amount = abs(raw_amount)
        else:
            signed_amount = -abs(raw_amount)

        category = infer_category(desc, signed_amount, sub_type)

        # Build informative notes
        notes_parts = []
        card_member = tx.get("embossed_name") or f"{tx.get('first_name', '')} {tx.get('last_name', '')}".strip()
        if card_member:
            notes_parts.append(f"Card Member: {card_member}")
        if tx.get("reference_id"):
            notes_parts.append(f"Ref: {tx.get('reference_id')}")
        if tx.get("statement_end_date"):
            notes_parts.append(f"Stmt: {tx.get('statement_end_date')}")

        notes = "; ".join(notes_parts)

        writer.writerow([
            iso_date,
            merchant,
            category,
            account_display_name,
            desc,
            notes,
            f"{signed_amount:.2f}",
        ])

    return output.getvalue()
