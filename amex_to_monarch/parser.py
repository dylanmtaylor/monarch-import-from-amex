import os
import glob
import subprocess
import re
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional, Tuple, Union
from collections import defaultdict


def parse_date(date_str: str) -> date:
    """Parse date from various string formats (YYYY-MM-DD, MM/DD/YYYY, ISO)."""
    if not date_str:
        raise ValueError("Empty date string")
    clean = date_str.strip()
    if "T" in clean:
        clean = clean.split("T")[0]
    if "-" in clean:
        parts = clean.split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    if "/" in clean:
        parts = clean.split("/")
        return date(int(parts[2]), int(parts[0]), int(parts[1]))
    raise ValueError(f"Unsupported date format: {date_str}")


def normalize_transaction_amount(tx: Dict[str, Any]) -> float:
    """
    Extract signed decimal amount for a transaction.
    Charges / Debits are positive additions to credit card balance (owed amount).
    Payments / Credits are negative reductions to credit card balance (owed amount).
    """
    raw_amount = float(tx.get("amount", 0.0))
    tx_type = str(tx.get("type", "")).upper()
    sub_type = str(tx.get("sub_type", "")).upper()

    # Credit / Payment reduces card liability
    if tx_type == "CREDIT" or "PAYMENT" in sub_type or raw_amount < 0:
        return -abs(raw_amount)
    # Debit / Charge increases card liability
    return abs(raw_amount)


def build_continuous_daily_balances(
    start_date: date,
    end_date: date,
    checkpoints: List[Dict[str, Any]],
    transactions: List[Dict[str, Any]],
    current_balance: Optional[float] = None,
    invert_liability: bool = True,
) -> List[Dict[str, Any]]:
    """
    Reconstruct an uninterrupted daily balance history from start_date to end_date.

    Each day's balance represents the owed credit card balance (positive liability).
    Aligns with statement closing balance checkpoints on statement end dates.
    Interpolates and reconciles between statement checkpoints using daily transactions.
    """
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    # Group net transaction impact by date
    # In liability accounting: debits (+), credits/payments (-)
    tx_by_date = defaultdict(float)
    for tx in transactions:
        d_str = tx.get("post_date") or tx.get("charge_date") or tx.get("date")
        if not d_str:
            continue
        try:
            d = parse_date(d_str)
            impact = normalize_transaction_amount(tx)
            tx_by_date[d] += impact
        except Exception:
            continue

    # Prepare checkpoints sorted by date
    parsed_checkpoints: List[Dict[str, Any]] = []
    for cp in checkpoints:
        end_d_str = cp.get("statement_end_date") or cp.get("date")
        if not end_d_str:
            continue
        try:
            cp_date = parse_date(end_d_str)
            bal = float(cp.get("statement_balance", cp.get("balance", 0.0)))
            last_bal = float(cp.get("last_statement_balance", 0.0)) if "last_statement_balance" in cp else None
            parsed_checkpoints.append({
                "date": cp_date,
                "balance": bal,
                "last_balance": last_bal
            })
        except Exception:
            continue

    parsed_checkpoints.sort(key=lambda x: x["date"])

    # If current_balance provided and end_date is after last checkpoint, add current as checkpoint
    if current_balance is not None:
        if not parsed_checkpoints or parsed_checkpoints[-1]["date"] < end_date:
            parsed_checkpoints.append({
                "date": end_date,
                "balance": float(current_balance),
                "last_balance": None
            })

    # If no checkpoints exist at all, reconstruct entirely from transactions
    if not parsed_checkpoints:
        daily_balances: List[Dict[str, Any]] = []
        curr_bal = 0.0
        curr_d = start_date
        while curr_d <= end_date:
            curr_bal += tx_by_date[curr_d]
            reported = -curr_bal if invert_liability else curr_bal
            if abs(reported) < 0.0001:
                reported = 0.0
            daily_balances.append({
                "date": curr_d.strftime("%Y-%m-%d"),
                "balance": round(reported, 2)
            })
            curr_d += timedelta(days=1)
        return daily_balances

    # Build checkpoint segments
    daily_records: Dict[date, float] = {}

    # 1. Backfill from start_date up to first checkpoint
    first_cp = parsed_checkpoints[0]
    first_cp_date = first_cp["date"]
    first_cp_bal = first_cp["balance"]

    # If first checkpoint specifies last_balance and it has a start_date, we can use it
    # Work backward from first checkpoint
    running_bal = first_cp_bal
    d = first_cp_date
    while d >= start_date:
        daily_records[d] = running_bal
        d -= timedelta(days=1)
        running_bal -= tx_by_date[d + timedelta(days=1)]

    # 2. Iterate between consecutive checkpoints
    for i in range(len(parsed_checkpoints) - 1):
        cp_a = parsed_checkpoints[i]
        cp_b = parsed_checkpoints[i + 1]

        date_a = cp_a["date"]
        bal_a = cp_a["balance"]
        date_b = cp_b["date"]
        bal_b = cp_b["balance"]

        if date_b <= date_a:
            continue

        # Calculate sum of transactions in (date_a, date_b]
        net_tx = 0.0
        curr_d = date_a + timedelta(days=1)
        while curr_d <= date_b:
            net_tx += tx_by_date[curr_d]
            curr_d += timedelta(days=1)

        # Discrepancy between checkpoint A + net_tx and checkpoint B
        expected_b = bal_a + net_tx
        discrepancy = bal_b - expected_b
        num_days = (date_b - date_a).days

        # Forward march applying daily tx plus smooth linear adjustment of discrepancy
        curr_bal = bal_a
        curr_d = date_a + timedelta(days=1)
        step = 1
        while curr_d <= date_b:
            curr_bal += tx_by_date[curr_d]
            adj = (discrepancy * step) / num_days if num_days > 0 else 0.0
            if curr_d == date_b:
                daily_records[curr_d] = bal_b
            else:
                daily_records[curr_d] = curr_bal + adj
            curr_d += timedelta(days=1)
            step += 1

    # 3. If end_date extends past the last checkpoint
    last_cp = parsed_checkpoints[-1]
    last_cp_date = last_cp["date"]
    last_cp_bal = last_cp["balance"]

    curr_bal = last_cp_bal
    curr_d = last_cp_date + timedelta(days=1)
    while curr_d <= end_date:
        curr_bal += tx_by_date[curr_d]
        daily_records[curr_d] = curr_bal
        curr_d += timedelta(days=1)

    # 4. Generate sorted, contiguous array of all days from start_date to end_date
    result: List[Dict[str, Any]] = []
    curr_d = start_date
    while curr_d <= end_date:
        b_val = daily_records.get(curr_d, 0.0)
        # Invert liability for Monarch credit card balance history (debt is negative)
        if invert_liability:
            b_val = -b_val
        # Prevent negative near-zero floats like -0.00
        if abs(b_val) < 0.0001:
            b_val = 0.0
        result.append({
            "date": curr_d.strftime("%Y-%m-%d"),
            "balance": round(b_val, 2)
        })
        curr_d += timedelta(days=1)

    return result


class AmexAccountHistory:
    """Encapsulates the full historical dataset for an American Express card."""

    def __init__(
        self,
        card_name: str,
        display_account_number: str,
        account_token: str = "",
        account_setup_date: Optional[str] = None,
        current_balance: float = 0.0,
        credit_limit: float = 0.0,
    ):
        self.card_name = card_name
        self.display_account_number = display_account_number
        self.account_token = account_token
        self.account_setup_date = account_setup_date
        self.current_balance = current_balance
        self.credit_limit = credit_limit
        self.statement_periods: List[Dict[str, Any]] = []
        self.statement_checkpoints: List[Dict[str, Any]] = []
        self.transactions: List[Dict[str, Any]] = []
        self.pending_transactions: List[Dict[str, Any]] = []
        self.daily_balances: List[Dict[str, Any]] = []

    @property
    def display_name(self) -> str:
        """Formatted display name for Monarch account field."""
        clean_num = self.display_account_number.replace("•", "").replace("*", "").strip()
        if clean_num:
            return f"American Express - {self.card_name} (••••{clean_num})"
        return f"American Express - {self.card_name}"

    @property
    def slug(self) -> str:
        """Safe filename identifier for this account."""
        clean_name = re.sub(r"[^a-zA-Z0-9]+", "_", self.card_name).strip("_").lower()
        clean_num = re.sub(r"[^0-9]+", "", self.display_account_number)
        if clean_num:
            return f"amex_{clean_name}_{clean_num}"
        return f"amex_{clean_name}"

    def compute_daily_balances(
        self,
        end_date: Optional[date] = None,
        invert_liability: bool = True,
    ) -> List[Dict[str, Any]]:
        """Compute the full continuous daily balance history."""
        if not end_date:
            end_date = date.today()

        # Determine start date
        start_date = None
        if self.account_setup_date:
            try:
                start_date = parse_date(self.account_setup_date)
            except Exception:
                pass

        if not start_date and self.statement_checkpoints:
            try:
                start_date = parse_date(self.statement_checkpoints[0]["statement_end_date"])
            except Exception:
                pass

        if not start_date and self.transactions:
            dates = []
            for tx in self.transactions:
                d_str = tx.get("post_date") or tx.get("charge_date")
                if d_str:
                    try:
                        dates.append(parse_date(d_str))
                    except Exception:
                        pass
            if dates:
                start_date = min(dates)

        if not start_date:
            start_date = end_date - timedelta(days=365)

        self.daily_balances = build_continuous_daily_balances(
            start_date=start_date,
            end_date=end_date,
            checkpoints=self.statement_checkpoints,
            transactions=self.transactions,
            current_balance=self.current_balance,
            invert_liability=invert_liability,
        )
        return self.daily_balances

    def merge_statement_pdf_data(
        self,
        checkpoints: List[Dict[str, Any]],
        transactions: List[Dict[str, Any]],
    ) -> None:
        """Merge historical checkpoints and transactions parsed from statement PDFs."""
        # Merge checkpoints by statement_end_date
        existing_cp_dates = {cp.get("statement_end_date") for cp in self.statement_checkpoints if cp.get("statement_end_date")}
        for cp in checkpoints:
            d = cp.get("statement_end_date")
            if d and d not in existing_cp_dates:
                self.statement_checkpoints.append(cp)
                existing_cp_dates.add(d)

        self.statement_checkpoints.sort(key=lambda x: str(x.get("statement_end_date", "")))

        # Merge transactions deduplicating on (date, amount, description)
        existing_tx_keys = set()
        for tx in self.transactions:
            d = tx.get("post_date") or tx.get("charge_date") or tx.get("date")
            amt = round(float(tx.get("amount", 0.0)), 2)
            desc = (tx.get("description") or "").strip().upper()[:25]
            existing_tx_keys.add((d, amt, desc))

        for tx in transactions:
            d = tx.get("date") or tx.get("post_date")
            amt = round(float(tx.get("amount", 0.0)), 2)
            desc = (tx.get("description") or "").strip().upper()[:25]
            if (d, amt, desc) not in existing_tx_keys:
                self.transactions.append(tx)
                existing_tx_keys.add((d, amt, desc))

        self.transactions.sort(key=lambda x: str(x.get("post_date") or x.get("charge_date") or x.get("date", "")))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize account history to dictionary."""
        return {
            "card_name": self.card_name,
            "display_account_number": self.display_account_number,
            "account_token": self.account_token,
            "account_setup_date": self.account_setup_date,
            "current_balance": self.current_balance,
            "credit_limit": self.credit_limit,
            "statement_periods": self.statement_periods,
            "statement_checkpoints": self.statement_checkpoints,
            "transactions": self.transactions,
            "pending_transactions": self.pending_transactions,
            "daily_balances": self.daily_balances,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AmexAccountHistory":
        """Instantiate AmexAccountHistory from dictionary."""
        acct = cls(
            card_name=data.get("card_name", "American Express Card"),
            display_account_number=str(data.get("display_account_number", "")),
            account_token=data.get("account_token", ""),
            account_setup_date=data.get("account_setup_date"),
            current_balance=float(data.get("current_balance", 0.0)),
            credit_limit=float(data.get("credit_limit", 0.0)),
        )
        acct.statement_periods = list(data.get("statement_periods", []))
        acct.statement_checkpoints = list(data.get("statement_checkpoints", []))
        acct.transactions = list(data.get("transactions", []))
        acct.pending_transactions = list(data.get("pending_transactions", []))
        acct.daily_balances = list(data.get("daily_balances", []))
        return acct


def parse_amex_pdf_statement(pdf_source: Any) -> Dict[str, Any]:
    """
    Parse an American Express statement PDF from a file path, raw bytes, or text.
    Extracts closing date, account ending, balances, and itemized transactions.
    """
    text = ""
    if isinstance(pdf_source, (bytes, bytearray)):
        proc = subprocess.Popen(["pdftotext", "-layout", "-", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(input=pdf_source)
        text = out.decode("utf-8", errors="replace")
    elif isinstance(pdf_source, str):
        if pdf_source.startswith("%PDF") or "\n" in pdf_source:
            text = pdf_source
        elif os.path.exists(pdf_source):
            out = subprocess.check_output(["pdftotext", "-layout", pdf_source, "-"], stderr=subprocess.DEVNULL)
            text = out.decode("utf-8", errors="replace")
        else:
            text = pdf_source

    # 1. Closing date
    m_closing = re.search(r"Closing Date\s+(\d{2}/\d{2}/\d{2,4})", text)
    closing_date_str = m_closing.group(1) if m_closing else None

    # 2. Account ending
    m_acct = re.search(r"Account Ending\s+(?:[\d-]*\b)?(\d{5})\b", text)
    if not m_acct:
        m_acct = re.search(r"Account Ending\s+.*?(\d{4,5})\b", text)
    account_ending = m_acct.group(1) if m_acct else ""

    # 3. Balances
    m_prev = re.search(r"Previous Balance\s+([+-]?\$?[\d,]+\.\d{2})", text)
    m_pay = re.search(r"(?:Payments/Credits|Less Payments/Credits)\s+([+-]?\$?[\d,]+\.\d{2})", text)
    m_chg = re.search(r"(?:New Charges|Plus New Charges)\s+([+-]?\$?[\d,]+\.\d{2})", text)
    m_bal = re.search(r"(?:New Balance|Equals New Balance)\s+([+-]?\$?[\d,]+\.\d{2})", text)

    def to_float(val_str: Optional[str]) -> float:
        if not val_str:
            return 0.0
        s = val_str.replace("$", "").replace(",", "").strip()
        return float(s)

    c_date = None
    if closing_date_str:
        parts = closing_date_str.split("/")
        yr = int(parts[2])
        if yr < 100:
            yr += 2000
        c_date = date(yr, int(parts[0]), int(parts[1]))

    # 4. Itemized transactions
    tx_pattern = re.compile(r"^\s*(\d{2}/\d{2}/\d{2})\*?\s+([A-Za-z0-9\s\.\*\#\-\&\'\,]+?)\s+([+-]?\$?[\d,]+\.\d{2})\s*$", re.MULTILINE)
    transactions = []
    for m in tx_pattern.finditer(text):
        tx_d_str, desc, amt_str = m.groups()
        desc_clean = desc.strip()
        if any(w in desc_clean.upper() for w in ["CLOSING DATE", "PREVIOUS BALANCE", "NEW BALANCE", "MINIMUM PAYMENT", "PAYMENT DUE", "LATE PAYMENT WARNING"]):
            continue
        amt = to_float(amt_str)
        tparts = tx_d_str.split("/")
        tyr = int(tparts[2])
        if tyr < 100:
            tyr += 2000
        tx_d = date(tyr, int(tparts[0]), int(tparts[1]))
        
        is_credit = amt < 0 or "CREDIT" in desc_clean.upper() or "PAYMENT" in desc_clean.upper()
        transactions.append({
            "date": tx_d.isoformat(),
            "description": desc_clean,
            "amount": amt,
            "type": "CREDIT" if is_credit else "DEBIT",
            "source": "statement_pdf"
        })

    return {
        "statement_end_date": c_date.isoformat() if c_date else None,
        "account_ending": account_ending,
        "statement_balance": to_float(m_bal.group(1)) if m_bal else 0.0,
        "previous_balance": to_float(m_prev.group(1)) if m_prev else 0.0,
        "payments_credits_amount": to_float(m_pay.group(1)) if m_pay else 0.0,
        "charges_amount": to_float(m_chg.group(1)) if m_chg else 0.0,
        "transactions": transactions,
    }


def parse_amex_statements_dir(directory: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Parse all Amex statement PDFs in a directory.
    Returns (checkpoints, transactions) sorted chronologically.
    """
    if not os.path.exists(directory):
        return [], []

    pdf_files = sorted(glob.glob(os.path.join(directory, "*.pdf")))
    checkpoints = []
    all_transactions = []
    seen_tx_keys = set()

    for p in pdf_files:
        try:
            with open(p, "rb") as fp:
                hdr = fp.read(5)
                if not hdr.startswith(b"%PDF"):
                    continue
            stmt = parse_amex_pdf_statement(p)
            end_date = stmt.get("statement_end_date")
            if end_date:
                checkpoints.append({
                    "statement_end_date": end_date,
                    "statement_balance": stmt.get("statement_balance", 0.0),
                    "previous_balance": stmt.get("previous_balance", 0.0),
                    "charges_amount": stmt.get("charges_amount", 0.0),
                    "payments_credits_amount": stmt.get("payments_credits_amount", 0.0),
                })
            for tx in stmt.get("transactions", []):
                key = (tx.get("date"), round(float(tx.get("amount", 0.0)), 2), (tx.get("description") or "").strip().upper()[:25])
                if key not in seen_tx_keys:
                    seen_tx_keys.add(key)
                    all_transactions.append(tx)
        except Exception:
            continue

    checkpoints.sort(key=lambda x: str(x.get("statement_end_date", "")))
    all_transactions.sort(key=lambda x: str(x.get("date", "")))
    return checkpoints, all_transactions


def parse_amex_dataset(raw_data: Any) -> List[AmexAccountHistory]:
    """
    Parse a raw Amex JSON dataset (single card dict, list of card dicts, or wrapper dict).
    """
    if isinstance(raw_data, list):
        return [AmexAccountHistory.from_dict(item) for item in raw_data if isinstance(item, dict)]
    elif isinstance(raw_data, dict):
        if "cards" in raw_data and isinstance(raw_data["cards"], list):
            return [AmexAccountHistory.from_dict(item) for item in raw_data["cards"] if isinstance(item, dict)]
        if "accounts" in raw_data and isinstance(raw_data["accounts"], list):
            return [AmexAccountHistory.from_dict(item) for item in raw_data["accounts"] if isinstance(item, dict)]
        # Single account dict
        if "card_name" in raw_data or "transactions" in raw_data or "display_account_number" in raw_data:
            return [AmexAccountHistory.from_dict(raw_data)]
    return []


