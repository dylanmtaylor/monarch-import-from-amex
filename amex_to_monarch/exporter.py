"""Exports converted American Express balances and transactions to disk."""

import os
import csv
import json
from typing import Dict, List, Any
from amex_to_monarch.parser import AmexAccountHistory
from amex_to_monarch.converter import convert_balances_to_csv, convert_transactions_to_csv


class Exporter:
    """Exports Monarch CSV files and displays structured summary reports."""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir

    def export_account(
        self, acct: AmexAccountHistory, invert_liability: bool = True
    ) -> Dict[str, Any]:
        """Export balance history and transaction CSVs for a single card."""
        res = self.export_all([acct], invert_liability=invert_liability)
        return res["metrics"].get(acct.slug, {})

    def export_all(
        self, accounts: List[AmexAccountHistory], invert_liability: bool = True
    ) -> Dict[str, Any]:
        """
        Export continuous daily balance and transaction CSVs for all cards.

        Returns:
            Dict containing generated file paths and metrics.
        """
        os.makedirs(self.output_dir, exist_ok=True)
        results: Dict[str, Any] = {
            "balance_files": [],
            "transaction_files": [],
            "raw_data_files": [],
            "combined_transaction_file": None,
            "metrics": {},
        }

        all_transactions_rows = []

        print("\n" + "=" * 75)
        print("American Express to Monarch Money Export Summary")
        print("=" * 75)

        for acct in sorted(accounts, key=lambda x: x.card_name):
            slug = acct.slug
            display_name = acct.display_name

            # 1. Compute and export daily balances
            daily_balances = acct.compute_daily_balances(invert_liability=invert_liability)
            bal_csv = convert_balances_to_csv(daily_balances)
            bal_file = os.path.join(self.output_dir, f"{slug}_balances.csv")
            with open(bal_file, "w", encoding="utf-8") as f:
                f.write(bal_csv)
            results["balance_files"].append(bal_file)

            # 2. Export account transactions (posted + pending)
            all_txns = list(acct.transactions)
            # Tag pending transactions in notes if present
            for p_tx in acct.pending_transactions:
                p_copy = dict(p_tx)
                p_copy["reference_id"] = f"PENDING - {p_copy.get('reference_id', '')}"
                all_txns.append(p_copy)

            # Sort transactions chronologically
            all_txns.sort(key=lambda t: t.get("post_date") or t.get("charge_date") or t.get("date", ""))

            txn_csv = convert_transactions_to_csv(all_txns, display_name)
            txn_file = os.path.join(self.output_dir, f"{slug}_transactions.csv")
            with open(txn_file, "w", encoding="utf-8") as f:
                f.write(txn_csv)
            results["transaction_files"].append(txn_file)

            # 3. Export raw data JSON for audit / re-conversion
            raw_data = {
                "card_name": acct.card_name,
                "display_account_number": acct.display_account_number,
                "account_token": acct.account_token,
                "account_setup_date": acct.account_setup_date,
                "current_balance": acct.current_balance,
                "credit_limit": acct.credit_limit,
                "statement_periods": acct.statement_periods,
                "statement_checkpoints": acct.statement_checkpoints,
                "transactions": acct.transactions,
                "pending_transactions": acct.pending_transactions,
            }
            raw_file = os.path.join(self.output_dir, f"{slug}_raw_data.json")
            with open(raw_file, "w", encoding="utf-8") as f:
                json.dump(raw_data, f, indent=2)
            results["raw_data_files"].append(raw_file)

            # Read rows for combined file
            reader = csv.reader(txn_csv.strip().splitlines())
            _headers = next(reader, None)
            for r in reader:
                all_transactions_rows.append(r)

            first_bal_date = daily_balances[0]["date"] if daily_balances else "N/A"
            last_bal_date = daily_balances[-1]["date"] if daily_balances else "N/A"
            current_bal = daily_balances[-1]["balance"] if daily_balances else acct.current_balance

            results["metrics"][acct.slug] = {
                "card_name": acct.card_name,
                "account_number": acct.display_account_number,
                "open_date": acct.account_setup_date,
                "credit_limit": acct.credit_limit,
                "current_balance": current_bal,
                "statements_count": len(acct.statement_checkpoints),
                "transactions_count": len(all_txns),
                "daily_balance_points": len(daily_balances),
                "daily_balance_range": f"{first_bal_date} to {last_bal_date}",
            }

            print(f"\nCard: {acct.card_name} (••••{acct.display_account_number})")
            if acct.account_setup_date:
                print(f"  Account Open Date: {acct.account_setup_date}")
            if acct.credit_limit > 0:
                print(f"  Credit Limit:      ${acct.credit_limit:,.2f}")
            print(f"  Current Balance:   ${current_bal:,.2f}")
            print(f"  Statements:        {len(acct.statement_checkpoints)}")
            print(f"  Transactions:      {len(all_txns)} total ({len(acct.transactions)} posted, {len(acct.pending_transactions)} pending)")
            print(f"  Daily Balances:    {len(daily_balances):,} continuous daily points ({first_bal_date} to {last_bal_date})")
            print(f"  -> Balances CSV:     {bal_file}")
            print(f"  -> Transactions CSV: {txn_file}")
            print(f"  -> Raw Data JSON:    {raw_file}")

        # Combined transactions if multiple cards or requested
        if len(accounts) > 1 and all_transactions_rows:
            all_transactions_rows.sort(key=lambda r: r[0])
            combined_file = os.path.join(self.output_dir, "amex_transactions_all.csv")
            with open(combined_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Date",
                    "Merchant",
                    "Category",
                    "Account",
                    "Original Statement",
                    "Notes",
                    "Amount"
                ])
                writer.writerows(all_transactions_rows)
            results["combined_transaction_file"] = combined_file
            print(f"\nCombined Transactions CSV ({len(all_transactions_rows)} records across {len(accounts)} cards):")
            print(f"  -> {combined_file}")

        print("\n" + "=" * 75)
        print("American Express Export completed successfully.")
        print("=" * 75 + "\n")

        return results
