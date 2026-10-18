"""Unit tests for amex_to_monarch.cli."""

import json
import os
import subprocess
import sys
import tempfile
import unittest


class TestCLI(unittest.TestCase):
    """Test CLI argument parsing, subcommands, and conversion pipeline."""

    def test_cli_help(self):
        """Test that CLI displays help and available subcommands."""
        res = subprocess.run(
            [sys.executable, "-m", "amex_to_monarch.cli", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("amex-to-monarch", res.stdout)
        self.assertIn("all", res.stdout)
        self.assertIn("capture", res.stdout)
        self.assertIn("convert", res.stdout)

    def test_cli_convert_command(self):
        """Test convert subcommand using synthetic JSON dataset."""
        synthetic_data = [
            {
                "card_name": "Synthetic Blue Cash",
                "display_account_number": "99999",
                "current_balance": 150.00,
                "credit_limit": 5000.00,
                "statement_checkpoints": [
                    {"statement_end_date": "2026-08-01", "statement_balance": 100.00},
                    {"statement_end_date": "2026-09-01", "statement_balance": 150.00},
                ],
                "transactions": [
                    {
                        "post_date": "2026-08-15",
                        "description": "SAMPLE BOOKSTORE",
                        "amount": 50.00,
                        "type": "DEBIT",
                    }
                ],
                "pending_transactions": [],
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "synthetic_amex.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(synthetic_data, f)

            output_dir = os.path.join(tmpdir, "output")
            res = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "amex_to_monarch.cli",
                    "convert",
                    json_path,
                    "--output-dir",
                    output_dir,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"CLI error: {res.stderr}")
            self.assertIn("Export completed", res.stdout)

            bal_file = os.path.join(output_dir, "amex_synthetic_blue_cash_99999_balances.csv")
            txn_file = os.path.join(output_dir, "amex_synthetic_blue_cash_99999_transactions.csv")

            self.assertTrue(os.path.exists(bal_file))
            self.assertTrue(os.path.exists(txn_file))

    def test_cli_convert_missing_file(self):
        """Test convert subcommand fails gracefully when file is missing."""
        res = subprocess.run(
            [sys.executable, "-m", "amex_to_monarch.cli", "convert", "non_existent_file.json"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("not found", res.stderr)

    def test_cli_all_with_input_file(self):
        """Test all subcommand with --input-file bypasses CDP network calls."""
        synthetic_data = {
            "card_name": "Synthetic Platinum",
            "display_account_number": "88888",
            "current_balance": 0.00,
            "statement_checkpoints": [],
            "transactions": [],
            "pending_transactions": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "synthetic_amex_single.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(synthetic_data, f)

            output_dir = os.path.join(tmpdir, "output")
            res = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "amex_to_monarch.cli",
                    "all",
                    "--input-file",
                    json_path,
                    "--output-dir",
                    output_dir,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"CLI error: {res.stderr}")
            self.assertIn("Export completed", res.stdout)

            bal_file = os.path.join(output_dir, "amex_synthetic_platinum_88888_balances.csv")
            self.assertTrue(os.path.exists(bal_file))


if __name__ == "__main__":
    unittest.main()
