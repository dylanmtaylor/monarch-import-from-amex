"""Unit tests for amex_to_monarch.converter."""

import csv
import io
import unittest
from amex_to_monarch.converter import (
    clean_merchant_name,
    infer_category,
    convert_balances_to_csv,
    convert_transactions_to_csv,
)


class TestConverter(unittest.TestCase):
    """Test Monarch CSV conversion, merchant normalization, and categorization rules."""

    def test_clean_merchant_name_known_patterns(self):
        """Test normalization for prominent and standard merchant patterns."""
        cases = [
            ("LYFT *TRIP 855-280-0278 CA", "Lyft"),
            ("UBER EATS PENDING SAN FRANCISCO CA", "Uber"),
            ("PUBLIX SUPER MARKETS #1234 ATLANTA GA", "Publix"),
            ("WHOLE FDS CR 10423 RALEIGH NC", "Whole Foods"),
            ("TRADER JOE'S #521 CARY NC", "Trader Joe's"),
            ("AMAZON.COM*2A3B4C AMZN.COM/BILL WA", "Amazon"),
            ("STARBUCKS STORE 08421 SEATTLE WA", "Starbucks"),
            ("CHIPOTLE 2410 ONLINE CA", "Chipotle"),
            ("AUTOPAY PAYMENT - THANK YOU", "American Express Payment"),
            ("ONLINE PAYMENT - THANK YOU", "American Express Payment"),
            ("MOBILE PAYMENT - THANK YOU", "American Express Payment"),
            ("CASH BACK REWARD APPLIED", "American Express Cash Back"),
            ("AMEX OFFER CREDIT", "Amex Offer Credit"),
            ("ANNUAL MEMBERSHIP FEE", "American Express Annual Fee"),
            ("SHELL OIL 57542109841 CHARLOTTE NC", "Shell"),
            ("DELTA AIR 0062451892012 ATLANTA GA", "Delta Air Lines"),
            ("GFIBER INTERNET BILLING", "Google Fiber"),
        ]
        for raw, expected in cases:
            self.assertEqual(clean_merchant_name(raw), expected, f"Failed for {raw}")

    def test_clean_merchant_name_generic_fallbacks(self):
        """Test regex cleaning of phone numbers, store numbers, states, and empty values."""
        self.assertEqual(clean_merchant_name(""), "Unknown Merchant")
        self.assertEqual(clean_merchant_name(None), "Unknown Merchant")
        self.assertEqual(clean_merchant_name("CORNER BOOKSTORE #9876 NC"), "Corner Bookstore")
        self.assertEqual(clean_merchant_name("LOCAL CAFE 800-555-0199 CA"), "Local Cafe")

    def test_infer_category(self):
        """Test Monarch category inference heuristics."""
        self.assertEqual(infer_category("ONLINE PAYMENT - THANK YOU", 100.0), "Credit Card Payment")
        self.assertEqual(infer_category("CASH BACK REWARD APPLIED", 25.0), "Cash Back")
        self.assertEqual(infer_category("INTEREST CHARGE ON PURCHASES", -5.50), "Interest")
        self.assertEqual(infer_category("ANNUAL MEMBERSHIP FEE", -95.00), "Financial Fees")
        self.assertEqual(infer_category("PUBLIX #1234", -45.20), "Groceries")
        self.assertEqual(infer_category("LYFT RIDE", -16.50), "Taxi & Ride Share")
        self.assertEqual(infer_category("STARBUCKS", -6.75), "Restaurants & Bars")
        self.assertEqual(infer_category("SHELL OIL", -35.00), "Gas & Fuel")
        self.assertEqual(infer_category("AMAZON.COM", -29.99), "Shopping")
        self.assertEqual(infer_category("NETFLIX.COM", -15.49), "Entertainment & Recreation")
        self.assertEqual(infer_category("DELTA AIR", -320.00), "Travel")

    def test_convert_balances_to_csv(self):
        """Test formatting daily balances into Monarch Balance History CSV."""
        daily_balances = [
            {"date": "2026-10-01", "balance": -50.00},
            {"date": "2026-10-02", "balance": -75.50},
        ]
        csv_str = convert_balances_to_csv(daily_balances)
        rows = list(csv.reader(io.StringIO(csv_str)))
        self.assertEqual(rows[0], ["Date", "Balance"])
        self.assertEqual(rows[1], ["2026-10-01", "-50.00"])
        self.assertEqual(rows[2], ["2026-10-02", "-75.50"])

    def test_convert_transactions_to_csv(self):
        """Test formatting transactions into Monarch Transaction CSV."""
        txns = [
            {
                "post_date": "2026-10-15",
                "description": "LYFT 855-280-0278 CA",
                "amount": 16.70,
                "type": "DEBIT",
                "sub_type": "PURCHASE",
                "reference_id": "REF9999",
                "statement_end_date": "2026-11-03",
                "embossed_name": "JANE DOE",
            },
            {
                "post_date": "2026-10-16",
                "description": "ONLINE PAYMENT - THANK YOU",
                "amount": 100.00,
                "type": "CREDIT",
                "sub_type": "ONLINE PAYMENT - THANK YOU",
                "reference_id": "PMT1111",
            },
        ]
        acct_name = "American Express - Blue Cash (••••12345)"
        csv_str = convert_transactions_to_csv(txns, acct_name)
        rows = list(csv.reader(io.StringIO(csv_str)))

        # Header check
        self.assertEqual(
            rows[0],
            ["Date", "Merchant", "Category", "Account", "Original Statement", "Notes", "Amount"],
        )

        # Row 1: Charge is negative in Monarch
        self.assertEqual(rows[1][0], "2026-10-15")
        self.assertEqual(rows[1][1], "Lyft")
        self.assertEqual(rows[1][2], "Taxi & Ride Share")
        self.assertEqual(rows[1][3], acct_name)
        self.assertEqual(rows[1][4], "LYFT 855-280-0278 CA")
        self.assertIn("Card Member: JANE DOE", rows[1][5])
        self.assertIn("Ref: REF9999", rows[1][5])
        self.assertIn("Stmt: 2026-11-03", rows[1][5])
        self.assertEqual(rows[1][6], "-16.70")

        # Row 2: Payment is positive in Monarch
        self.assertEqual(rows[2][0], "2026-10-16")
        self.assertEqual(rows[2][1], "American Express Payment")
        self.assertEqual(rows[2][2], "Credit Card Payment")
        self.assertEqual(rows[2][6], "100.00")


if __name__ == "__main__":
    unittest.main()
