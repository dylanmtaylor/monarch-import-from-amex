"""Unit tests for amex_to_monarch.exporter."""

import os
import json
import tempfile
import unittest
from amex_to_monarch.parser import AmexAccountHistory
from amex_to_monarch.exporter import Exporter


class TestExporter(unittest.TestCase):
    """Test CSV and JSON file generation to disk."""

    def test_export_single_account(self):
        """Test exporting a single account history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            acct = AmexAccountHistory(
                card_name="Blue Cash Preferred",
                display_account_number="12345",
                current_balance=45.00,
                credit_limit=12000.0,
            )
            acct.statement_checkpoints = [
                {"statement_end_date": "2026-09-01", "statement_balance": 45.00}
            ]
            acct.transactions = [
                {
                    "post_date": "2026-09-10",
                    "description": "SAMPLE STORE",
                    "amount": 45.00,
                    "type": "DEBIT",
                }
            ]
            acct.pending_transactions = [
                {
                    "charge_date": "2026-09-12",
                    "description": "PENDING PURCHASE",
                    "amount": 10.00,
                    "type": "DEBIT",
                }
            ]

            exporter = Exporter(output_dir=tmpdir)
            res = exporter.export_all([acct], invert_liability=True)

            self.assertEqual(len(res["balance_files"]), 1)
            self.assertEqual(len(res["transaction_files"]), 1)
            self.assertEqual(len(res["raw_data_files"]), 1)
            self.assertIsNone(res["combined_transaction_file"])

            bal_file = res["balance_files"][0]
            txn_file = res["transaction_files"][0]
            raw_file = res["raw_data_files"][0]

            self.assertTrue(os.path.exists(bal_file))
            self.assertTrue(os.path.exists(txn_file))
            self.assertTrue(os.path.exists(raw_file))

            # Inspect balance CSV
            with open(bal_file, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("Date,Balance", content)
                self.assertIn("-45.00", content)

            # Inspect transaction CSV
            with open(txn_file, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("Date,Merchant,Category,Account,Original Statement,Notes,Amount", content)
                self.assertIn("Sample Store", content)
                self.assertIn("PENDING", content)

            # Inspect raw JSON
            with open(raw_file, "r", encoding="utf-8") as f:
                raw_json = json.load(f)
                self.assertEqual(raw_json["card_name"], "Blue Cash Preferred")

            # Convenience method
            metric = exporter.export_account(acct)
            self.assertEqual(metric["card_name"], "Blue Cash Preferred")

    def test_export_multiple_accounts_combined_file(self):
        """Test exporting multiple cards creates a sorted combined transactions CSV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            acct1 = AmexAccountHistory(
                card_name="Card One",
                display_account_number="11111",
                current_balance=10.00,
            )
            acct1.transactions = [
                {"post_date": "2026-01-01", "description": "FIRST TX", "amount": 10.00, "type": "DEBIT"}
            ]

            acct2 = AmexAccountHistory(
                card_name="Card Two",
                display_account_number="22222",
                current_balance=20.00,
            )
            acct2.transactions = [
                {"post_date": "2026-01-02", "description": "SECOND TX", "amount": 20.00, "type": "DEBIT"}
            ]

            exporter = Exporter(output_dir=tmpdir)
            res = exporter.export_all([acct1, acct2])

            self.assertEqual(len(res["balance_files"]), 2)
            self.assertIsNotNone(res["combined_transaction_file"])
            combined_path = res["combined_transaction_file"]
            self.assertTrue(os.path.exists(combined_path))

            with open(combined_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                self.assertEqual(len(lines), 3)  # header + 2 transactions
                self.assertIn("Card One", lines[1])
                self.assertIn("Card Two", lines[2])


if __name__ == "__main__":
    unittest.main()
