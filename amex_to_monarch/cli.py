"""Command Line Interface for American Express to Monarch Money export."""

import argparse
import json
import logging
import os
import sys
from typing import Optional, List, Dict, Any

from amex_to_monarch.cdp import find_amex_tab, CDPClient, CDPError
from amex_to_monarch.api import AmexAPI, APIError
from amex_to_monarch.parser import AmexAccountHistory, parse_amex_dataset, parse_amex_statements_dir
from amex_to_monarch.exporter import Exporter

logger = logging.getLogger("amex_to_monarch")


def extract_card_data(api: AmexAPI, card_info: Dict[str, Any]) -> AmexAccountHistory:
    """Extract complete transactions and statement history for an Amex card."""
    card_name = card_info.get("card_name", "American Express Card")
    display_num = str(card_info.get("display_account_number", "")).strip()
    token = card_info.get("account_token", "")
    setup_date = card_info.get("account_setup_date")

    print(f"\nExtracting card: {card_name} (••••{display_num})...")

    # 1. Balances
    current_bal = 0.0
    try:
        bal_data = api.get_balances(token)
        current_bal = float(bal_data.get("statement_balance_amount", 0.0))
    except Exception as e:
        logger.warning(f"Could not fetch current balance: {e}")

    # 2. Credit limits
    credit_limit = 0.0
    try:
        limits = api.get_credit_limits(token)
        credit_limit = float(limits.get("total_credit_amount", 0.0))
    except Exception as e:
        logger.warning(f"Could not fetch credit limit: {e}")

    history = AmexAccountHistory(
        card_name=card_name,
        display_account_number=display_num,
        account_token=token,
        account_setup_date=setup_date,
        current_balance=current_bal,
        credit_limit=credit_limit,
    )

    # 3. Statement periods
    try:
        periods = api.get_statement_periods(token)
        history.statement_periods = periods
        print(f"  Found {len(periods)} statement periods.")
    except Exception as e:
        logger.warning(f"Could not fetch statement periods: {e}")
        periods = []

    # 4. Statement documents (for historical end dates back to inception)
    doc_end_dates = []
    try:
        docs = api.get_statement_documents(token)
        doc_end_dates = [d.get("end_date") for d in docs if d.get("end_date")]
        oldest_doc = doc_end_dates[-1] if doc_end_dates else "N/A"
        print(f"  Found {len(docs)} historical statement documents dating back to {oldest_doc}.")
    except Exception as e:
        logger.warning(f"Could not fetch statement documents: {e}")

    # 5. Pending transactions
    try:
        pending = api.get_pending_transactions(token)
        history.pending_transactions = pending
        if pending:
            print(f"  Found {len(pending)} pending transactions.")
    except Exception as e:
        logger.warning(f"Could not fetch pending transactions: {e}")

    # 6. Posted transactions (open cycle + statement periods)
    seen_tx_keys = set()
    all_txns = []

    # Fetch open cycle
    try:
        open_txns = api.get_transactions(token, limit=1000)
        for tx in open_txns:
            key = tx.get("identifier") or (
                tx.get("post_date") or tx.get("charge_date"),
                tx.get("amount"),
                tx.get("description"),
                tx.get("reference_id"),
            )
            if key not in seen_tx_keys:
                seen_tx_keys.add(key)
                all_txns.append(tx)
    except Exception as e:
        logger.warning(f"Could not fetch open cycle transactions: {e}")

    # Fetch for statement periods
    for p in periods:
        stmt_end = p.get("statement_end_date")
        if not stmt_end:
            continue
        try:
            stmt_txns = api.get_transactions(token, statement_end_date=stmt_end, limit=1000)
            for tx in stmt_txns:
                key = tx.get("identifier") or (
                    tx.get("post_date") or tx.get("charge_date"),
                    tx.get("amount"),
                    tx.get("description"),
                    tx.get("reference_id"),
                )
                if key not in seen_tx_keys:
                    seen_tx_keys.add(key)
                    all_txns.append(tx)
        except Exception as e:
            logger.debug(f"Could not fetch transactions for statement {stmt_end}: {e}")

    history.transactions = all_txns
    print(f"  Extracted {len(all_txns)} total posted transactions.")

    # 7. Collect historical statement balance checkpoints
    # Merge statement period dates and doc end dates
    all_stmt_dates = set()
    for p in periods:
        if p.get("statement_end_date"):
            all_stmt_dates.add(p["statement_end_date"])
    for d in doc_end_dates:
        all_stmt_dates.add(d)

    sorted_stmt_dates = sorted(list(all_stmt_dates), reverse=True)
    checkpoints = []
    print(f"  Querying statement balance checkpoints across {len(sorted_stmt_dates)} periods...")
    for end_d in sorted_stmt_dates:
        try:
            b_info = api.get_balances(token, statement_end_date=end_d)
            if b_info and "statement_balance_amount" in b_info:
                checkpoints.append({
                    "statement_end_date": end_d,
                    "statement_balance": float(b_info.get("statement_balance_amount", 0.0)),
                    "last_statement_balance": float(b_info.get("last_statement_balance_amount", 0.0)),
                    "charges_amount": float(b_info.get("charges_amount", 0.0)),
                    "payments_credits_amount": float(b_info.get("total_payments_credits_amount", 0.0)),
                    "credits_amount": float(b_info.get("total_credits_amount", 0.0)),
                })
        except Exception:
            # Reached date range where API no longer returns JSON balance
            pass

    history.statement_checkpoints = sorted(checkpoints, key=lambda x: x["statement_end_date"])
    print(f"  Collected {len(history.statement_checkpoints)} validated statement balance checkpoints.")

    return history


def run_pipeline(
    port: int = 9222,
    tab_id: Optional[str] = "98068A30C1D5D79CCA1185C58DD4465E",
    output_dir: str = "output",
    cache_file: Optional[str] = "amex_raw_data.json",
    invert_liability: bool = True,
    input_file: Optional[str] = None,
    statements_dir: Optional[str] = "statements",
):
    """Run full automated capture and export pipeline."""
    os.makedirs(output_dir, exist_ok=True)
    accounts: List[AmexAccountHistory] = []

    if input_file and os.path.exists(input_file):
        print(f"Loading raw American Express dataset from '{input_file}'...")
        with open(input_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        accounts = parse_amex_dataset(raw_data)
    else:
        print(f"Connecting to Chrome DevTools Protocol on port {port}...")
        try:
            tab = find_amex_tab(port=port, tab_id=tab_id)
            print(f"Connected to Amex tab: {tab.get('title', '')} ({tab.get('url', '')})")
            client = CDPClient(tab.get("webSocketDebuggerUrl"))
            try:
                api = AmexAPI(client)
                cards = api.get_cards()
                print(f"Discovered {len(cards)} American Express card(s).")
                for c in cards:
                    acct = extract_card_data(api, c)
                    accounts.append(acct)
            finally:
                client.close()
        except CDPError as e:
            print(f"CDP Error: {e}", file=sys.stderr)
            sys.exit(1)

    # Ingest historical statement PDFs if available
    if statements_dir and os.path.exists(statements_dir):
        print(f"\nParsing statement PDFs from '{statements_dir}'...")
        pdf_checkpoints, pdf_txns = parse_amex_statements_dir(statements_dir)
        print(f"  Parsed {len(pdf_checkpoints)} statement checkpoints and {len(pdf_txns)} transactions from PDFs.")
        for acct in accounts:
            acct.merge_statement_pdf_data(pdf_checkpoints, pdf_txns)

    # Cache raw data if requested
    if cache_file and accounts:
        print(f"\nSaving raw dataset cache to '{cache_file}'...")
        try:
            raw_payload = [a.to_dict() for a in accounts]
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(raw_payload, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to save raw cache file: {e}")

    print("\nExporting continuous daily balances and transactions to Monarch CSVs...")
    exporter = Exporter(output_dir=output_dir)
    exporter.export_all(accounts, invert_liability=invert_liability)


def run_capture(
    port: int = 9222,
    tab_id: Optional[str] = "98068A30C1D5D79CCA1185C58DD4465E",
    output_file: str = "amex_raw_data.json",
):
    """Capture raw data from Chrome CDP and write to JSON file."""
    print(f"Connecting to Chrome on port {port}...")
    try:
        tab = find_amex_tab(port=port, tab_id=tab_id)
        print(f"Connected to Amex tab: {tab.get('title', '')} ({tab.get('url', '')})")
        client = CDPClient(tab.get("webSocketDebuggerUrl"))
        try:
            api = AmexAPI(client)
            cards = api.get_cards()
            print(f"Discovered {len(cards)} American Express card(s).")
            accounts = []
            for c in cards:
                acct = extract_card_data(api, c)
                accounts.append(acct)

            raw_payload = [a.to_dict() for a in accounts]
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(raw_payload, f, indent=2)
            print(f"\nRaw American Express data captured successfully to '{output_file}'.")
        finally:
            client.close()
    except CDPError as e:
        print(f"CDP Error: {e}", file=sys.stderr)
        sys.exit(1)


def run_convert(
    input_file: str,
    output_dir: str = "output",
    invert_liability: bool = True,
    statements_dir: Optional[str] = "statements",
):
    """Convert an existing JSON cache file to Monarch CSVs."""
    if not os.path.exists(input_file):
        print(f"Error: File '{input_file}' not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Reading American Express dataset from '{input_file}'...")
    with open(input_file, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    accounts = parse_amex_dataset(raw_data)
    if not accounts:
        print(f"Error: No valid account data found in '{input_file}'.", file=sys.stderr)
        sys.exit(1)

    # Ingest historical statement PDFs if available
    if statements_dir and os.path.exists(statements_dir):
        print(f"\nParsing statement PDFs from '{statements_dir}'...")
        pdf_checkpoints, pdf_txns = parse_amex_statements_dir(statements_dir)
        print(f"  Parsed {len(pdf_checkpoints)} statement checkpoints and {len(pdf_txns)} transactions from PDFs.")
        for acct in accounts:
            acct.merge_statement_pdf_data(pdf_checkpoints, pdf_txns)

    exporter = Exporter(output_dir=output_dir)
    exporter.export_all(accounts, invert_liability=invert_liability)


def main():
    """Main CLI entry point for amex-to-monarch."""
    parser = argparse.ArgumentParser(
        prog="amex-to-monarch",
        description="Extract American Express card transactions and continuous daily balance history into Monarch Money CSV format.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: all
    p_all = subparsers.add_parser("all", help="Full automated capture and export pipeline")
    p_all.add_argument("--port", type=int, default=9222, help="Chrome debugging port (default: 9222)")
    p_all.add_argument("--tab-id", type=str, default="98068A30C1D5D79CCA1185C58DD4465E", help="Specific Amex tab ID")
    p_all.add_argument("--output-dir", type=str, default="output", help="Directory for Monarch CSVs (default: output)")
    p_all.add_argument("--cache-file", type=str, default="amex_raw_data.json", help="Path to cache raw captured data")
    p_all.add_argument("--positive-balances", action="store_true", help="Do not invert credit card liability balances")
    p_all.add_argument("--input-file", type=str, default=None, help="Path to existing JSON dataset (skip CDP)")
    p_all.add_argument("--statements-dir", type=str, default="statements", help="Directory containing downloaded statement PDFs")

    # Command: capture
    p_capture = subparsers.add_parser("capture", help="Capture raw data from Chrome CDP into JSON cache")
    p_capture.add_argument("--port", type=int, default=9222, help="Chrome debugging port (default: 9222)")
    p_capture.add_argument("--tab-id", type=str, default="98068A30C1D5D79CCA1185C58DD4465E", help="Specific Amex tab ID")
    p_capture.add_argument("--output-file", type=str, default="amex_raw_data.json", help="Path for captured JSON")

    # Command: convert
    p_convert = subparsers.add_parser("convert", help="Convert an existing JSON dataset to Monarch CSVs")
    p_convert.add_argument("input_file", type=str, help="Path to raw JSON dataset")
    p_convert.add_argument("--output-dir", type=str, default="output", help="Directory for Monarch CSVs (default: output)")
    p_convert.add_argument("--positive-balances", action="store_true", help="Do not invert credit card liability balances")
    p_convert.add_argument("--statements-dir", type=str, default="statements", help="Directory containing downloaded statement PDFs")

    args = parser.parse_args()

    if args.command in (None, "all"):
        run_pipeline(
            port=getattr(args, "port", 9222),
            tab_id=getattr(args, "tab_id", "98068A30C1D5D79CCA1185C58DD4465E"),
            output_dir=getattr(args, "output_dir", "output"),
            cache_file=getattr(args, "cache_file", "amex_raw_data.json"),
            invert_liability=not getattr(args, "positive_balances", False),
            input_file=getattr(args, "input_file", None),
            statements_dir=getattr(args, "statements_dir", "statements"),
        )
    elif args.command == "capture":
        run_capture(
            port=args.port,
            tab_id=args.tab_id,
            output_file=args.output_file,
        )
    elif args.command == "convert":
        run_convert(
            input_file=args.input_file,
            output_dir=args.output_dir,
            invert_liability=not args.positive_balances,
            statements_dir=args.statements_dir,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
