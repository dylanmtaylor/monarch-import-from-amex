"""Unit tests for amex_to_monarch.parser."""

import unittest
from datetime import date, timedelta
from amex_to_monarch.parser import (
    parse_date,
    normalize_transaction_amount,
    build_continuous_daily_balances,
    AmexAccountHistory,
    parse_amex_dataset,
)


class TestParser(unittest.TestCase):
    """Test Amex parser and continuous balance reconstruction with synthetic fixtures."""

    def test_parse_date_formats(self):
        """Test parsing diverse date formats."""
        self.assertEqual(parse_date("2026-10-18"), date(2026, 10, 18))
        self.assertEqual(parse_date("2026-10-18T14:30:00Z"), date(2026, 10, 18))
        self.assertEqual(parse_date("10/18/2026"), date(2026, 10, 18))
        self.assertEqual(parse_date(" 2025-01-01 "), date(2025, 1, 1))

        with self.assertRaises(ValueError):
            parse_date("")
        with self.assertRaises(ValueError):
            parse_date("invalid-date")

    def test_normalize_transaction_amount(self):
        """Test signing and liability classification of transactions."""
        # Charges / Debits add to liability (positive impact on balance owed)
        debit_tx = {"amount": 25.50, "type": "DEBIT", "sub_type": "PURCHASE"}
        self.assertEqual(normalize_transaction_amount(debit_tx), 25.50)

        # Credits reduce liability (negative impact on balance owed)
        credit_tx = {"amount": 50.00, "type": "CREDIT", "sub_type": "REFUND"}
        self.assertEqual(normalize_transaction_amount(credit_tx), -50.00)

        # Payments reduce liability
        payment_tx = {"amount": 100.00, "type": "DEBIT", "sub_type": "ONLINE PAYMENT - THANK YOU"}
        self.assertEqual(normalize_transaction_amount(payment_tx), -100.00)

        # Already negative amount
        neg_tx = {"amount": -15.00, "type": "DEBIT", "sub_type": "ADJUSTMENT"}
        self.assertEqual(normalize_transaction_amount(neg_tx), -15.00)

    def test_build_continuous_daily_balances_transactions_only(self):
        """Test continuous reconstruction when no statement checkpoints exist."""
        start_d = date(2026, 1, 1)
        end_d = date(2026, 1, 5)
        txs = [
            {"post_date": "2026-01-02", "amount": 10.00, "type": "DEBIT"},
            {"post_date": "2026-01-04", "amount": 20.00, "type": "DEBIT"},
            {"post_date": "2026-01-05", "amount": 5.00, "type": "CREDIT"},
        ]

        # Inverted liability (default in Monarch: money owed is negative)
        res_inverted = build_continuous_daily_balances(
            start_date=start_d,
            end_date=end_d,
            checkpoints=[],
            transactions=txs,
            invert_liability=True,
        )
        self.assertEqual(len(res_inverted), 5)
        self.assertEqual(res_inverted[0], {"date": "2026-01-01", "balance": 0.00})
        self.assertEqual(res_inverted[1], {"date": "2026-01-02", "balance": -10.00})
        self.assertEqual(res_inverted[2], {"date": "2026-01-03", "balance": -10.00})
        self.assertEqual(res_inverted[3], {"date": "2026-01-04", "balance": -30.00})
        self.assertEqual(res_inverted[4], {"date": "2026-01-05", "balance": -25.00})

        # Non-inverted liability (positive balances)
        res_pos = build_continuous_daily_balances(
            start_date=start_d,
            end_date=end_d,
            checkpoints=[],
            transactions=txs,
            invert_liability=False,
        )
        self.assertEqual(res_pos[4]["balance"], 25.00)

    def test_build_continuous_daily_balances_with_checkpoints(self):
        """Test continuous reconstruction with statement checkpoints and smooth reconciliation."""
        start_d = date(2026, 1, 1)
        end_d = date(2026, 1, 10)
        checkpoints = [
            {"statement_end_date": "2026-01-03", "statement_balance": 50.00},
            {"statement_end_date": "2026-01-07", "statement_balance": 100.00},
        ]
        txs = [
            {"post_date": "2026-01-05", "amount": 30.00, "type": "DEBIT"},
            {"post_date": "2026-01-09", "amount": 20.00, "type": "DEBIT"},
        ]

        res = build_continuous_daily_balances(
            start_date=start_d,
            end_date=end_d,
            checkpoints=checkpoints,
            transactions=txs,
            current_balance=120.00,
            invert_liability=False,
        )
        self.assertEqual(len(res), 10)
        # Checkpoint A date (Jan 3) matches statement balance 50.00
        jan3 = next(r for r in res if r["date"] == "2026-01-03")
        self.assertEqual(jan3["balance"], 50.00)

        # Checkpoint B date (Jan 7) matches statement balance 100.00
        jan7 = next(r for r in res if r["date"] == "2026-01-07")
        self.assertEqual(jan7["balance"], 100.00)

        # End date matches current balance
        jan10 = next(r for r in res if r["date"] == "2026-01-10")
        self.assertEqual(jan10["balance"], 120.00)

    def test_build_continuous_daily_balances_date_swapped(self):
        """Test date swapping when start_date > end_date."""
        start_d = date(2026, 1, 5)
        end_d = date(2026, 1, 1)
        res = build_continuous_daily_balances(
            start_date=start_d,
            end_date=end_d,
            checkpoints=[],
            transactions=[],
        )
        self.assertEqual(len(res), 5)
        self.assertEqual(res[0]["date"], "2026-01-01")
        self.assertEqual(res[-1]["date"], "2026-01-05")

    def test_amex_account_history_properties(self):
        """Test display name, slug formatting, and balance computation."""
        acct = AmexAccountHistory(
            card_name="Blue Cash Everyday®",
            display_account_number="••••12345",
            account_setup_date="2020-05-15",
            current_balance=75.50,
            credit_limit=10000.0,
        )
        self.assertEqual(acct.display_name, "American Express - Blue Cash Everyday® (••••12345)")
        self.assertEqual(acct.slug, "amex_blue_cash_everyday_12345")

        # Without number
        acct_no_num = AmexAccountHistory(
            card_name="Platinum Card",
            display_account_number="",
        )
        self.assertEqual(acct_no_num.display_name, "American Express - Platinum Card")
        self.assertEqual(acct_no_num.slug, "amex_platinum_card")

    def test_amex_account_history_serialization(self):
        """Test to_dict, from_dict, and parse_amex_dataset roundtrip."""
        acct = AmexAccountHistory(
            card_name="Gold Card",
            display_account_number="54321",
            account_token="TOKEN123",
            account_setup_date="2021-02-01",
            current_balance=250.00,
            credit_limit=15000.0,
        )
        acct.statement_checkpoints = [{"statement_end_date": "2026-09-01", "statement_balance": 200.00}]
        acct.transactions = [{"post_date": "2026-09-10", "amount": 50.00, "type": "DEBIT"}]

        d = acct.to_dict()
        reconstituted = AmexAccountHistory.from_dict(d)
        self.assertEqual(reconstituted.card_name, "Gold Card")
        self.assertEqual(reconstituted.display_account_number, "54321")
        self.assertEqual(reconstituted.account_token, "TOKEN123")
        self.assertEqual(reconstituted.current_balance, 250.00)
        self.assertEqual(len(reconstituted.statement_checkpoints), 1)
        self.assertEqual(len(reconstituted.transactions), 1)

        # parse_amex_dataset variants
        list_parsed = parse_amex_dataset([d])
        self.assertEqual(len(list_parsed), 1)

        dict_cards = parse_amex_dataset({"cards": [d]})
        self.assertEqual(len(dict_cards), 1)

        dict_accounts = parse_amex_dataset({"accounts": [d]})
        self.assertEqual(len(dict_accounts), 1)

        single_dict = parse_amex_dataset(d)
        self.assertEqual(len(single_dict), 1)

        empty_parsed = parse_amex_dataset({})
        self.assertEqual(len(empty_parsed), 0)


if __name__ == "__main__":
    unittest.main()
