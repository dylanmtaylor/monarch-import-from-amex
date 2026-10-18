"""American Express Financial Servicing and Statements API client."""

import time
import logging
from typing import Dict, Any, List, Optional
from amex_to_monarch.cdp import CDPClient, CDPError, extract_accounts_from_page

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Raised when American Express API request fails."""
    pass


class AmexAPI:
    """Client for querying American Express Servicing APIs via CDP in-page session."""

    def __init__(self, client: CDPClient):
        self.client = client

    def get_cards(self) -> List[Dict[str, Any]]:
        """Discover all American Express cards for the authenticated user."""
        cards = extract_accounts_from_page(self.client)
        if not cards:
            # Fallback: check switcher or DOM if Redux was not yet populated
            fallback_cards = self.client.evaluate("""
            (() => {
                let options = Array.from(document.querySelectorAll('[data-testid*=\"simple_switcher_product_option\"]'));
                if (!options.length) {
                    let switcher = document.querySelector('[data-testid=\"simple_switcher_selected_option_display\"]');
                    if (switcher) {
                        let label = switcher.getAttribute('aria-label') || '';
                        let match = label.match(/(.*?)(?:ending in|••••)(\\d+)/i);
                        return [{
                            account_token: '',
                            card_name: match ? match[1].trim() : 'American Express Card',
                            display_account_number: match ? match[2].trim() : ''
                        }];
                    }
                }
                return options.map(opt => {
                    let id = opt.getAttribute('data-testid').replace('simple_switcher_product_option_CARD_PRODUCT_', '');
                    let text = opt.innerText.trim();
                    let match = text.match(/(.*?)(?:ending in|••••)(\\d+)/i);
                    return {
                        account_token: id,
                        card_name: match ? match[1].trim() : text,
                        display_account_number: match ? match[2].trim() : ''
                    };
                });
            })()
            """)
            if isinstance(fallback_cards, list) and fallback_cards:
                return fallback_cards
        return cards

    def _get_headers(self, account_token: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Construct standard Amex Servicing API headers."""
        headers = {
            "account_token": account_token,
            "account_tokens": account_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def get_balances(
        self, account_token: str, statement_end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch balance information for an account.
        If statement_end_date is provided, returns historical statement balance.
        """
        url = "/api/servicing/v1/financials/balances"
        if statement_end_date:
            url += f"?statement_end_date={statement_end_date}"

        res = self.client.call_fetch(url, headers=self._get_headers(account_token))
        if res.get("status") != 200:
            raise APIError(
                f"Failed to fetch balances (status {res.get('status')}): {res.get('text')}"
            )

        data = res.get("json")
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        elif isinstance(data, dict):
            return data
        return {}

    def get_credit_limits(self, account_token: str) -> Dict[str, Any]:
        """Fetch total credit limit and available credit."""
        url = "/api/servicing/v1/financials/credit_limits"
        res = self.client.call_fetch(url, headers=self._get_headers(account_token))
        if res.get("status") == 200:
            data = res.get("json")
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            if isinstance(data, dict):
                return data
        return {}

    def get_payment_due(self, account_token: str) -> Dict[str, Any]:
        """Fetch payment due date, minimum payment, and remaining days to pay."""
        url = "/api/servicing/v1/financials/payments"
        res = self.client.call_fetch(url, headers=self._get_headers(account_token))
        if res.get("status") == 200:
            data = res.get("json")
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("payment_due_details", {})
        return {}

    def get_statement_periods(self, account_token: str) -> List[Dict[str, Any]]:
        """Fetch all available statement periods."""
        url = "/api/servicing/v1/financials/statement_periods"
        res = self.client.call_fetch(url, headers=self._get_headers(account_token))
        if res.get("status") != 200:
            raise APIError(
                f"Failed to fetch statement periods (status {res.get('status')}): {res.get('text')}"
            )
        data = res.get("json")
        if isinstance(data, list):
            return data
        return []

    def get_statement_documents(self, account_token: str) -> List[Dict[str, Any]]:
        """Fetch statement PDF metadata and document IDs."""
        url = "/api/servicing/v1/documents/statements"
        headers = self._get_headers(account_token, extra={"client_id": "OneAmex"})
        res = self.client.call_fetch(url, headers=headers)
        if res.get("status") == 200:
            data = res.get("json")
            if isinstance(data, list):
                return data
        return []

    def get_transactions(
        self,
        account_token: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        statement_end_date: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Fetch posted transactions."""
        params = [f"limit={limit}"]
        if start_date:
            params.append(f"start_date={start_date}")
        if end_date:
            params.append(f"end_date={end_date}")
        if statement_end_date:
            params.append(f"statement_end_date={statement_end_date}")

        url = f"/api/servicing/v1/financials/transactions?{'&'.join(params)}"
        res = self.client.call_fetch(url, headers=self._get_headers(account_token))
        if res.get("status") != 200:
            raise APIError(
                f"Failed to fetch transactions (status {res.get('status')}): {res.get('text')}"
            )

        data = res.get("json")
        if isinstance(data, dict) and "transactions" in data:
            return data["transactions"]
        return []

    def get_pending_transactions(self, account_token: str) -> List[Dict[str, Any]]:
        """Fetch currently pending transactions."""
        url = "/api/servicing/v1/financials/transactions?status=pending"
        res = self.client.call_fetch(url, headers=self._get_headers(account_token))
        if res.get("status") == 200:
            data = res.get("json")
            if isinstance(data, dict) and "transactions" in data:
                return data["transactions"]
        return []

    def get_transaction_summary(
        self, account_token: str, statement_end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch transaction summary totals for an account or specific statement."""
        url = "/api/servicing/v1/financials/transaction_summary"
        if statement_end_date:
            url += f"?statement_end_date={statement_end_date}"
        res = self.client.call_fetch(url, headers=self._get_headers(account_token))
        if res.get("status") == 200:
            data = res.get("json")
            if isinstance(data, list) and len(data) > 0:
                return data[0]
        return {}

    def collect_all_statement_balances(
        self, account_token: str, statement_periods: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Query historical statement balances for all statement periods.
        Returns list of statement checkpoint records:
        [{
            "statement_end_date": "YYYY-MM-DD",
            "statement_start_date": "YYYY-MM-DD",
            "statement_balance": float,
            "last_statement_balance": float,
            "charges_amount": float,
            "payments_credits_amount": float
        }, ...]
        """
        checkpoints: List[Dict[str, Any]] = []
        for period in statement_periods:
            end_date = period.get("statement_end_date")
            start_date = period.get("statement_start_date")
            if not end_date:
                continue

            try:
                bal_data = self.get_balances(account_token, statement_end_date=end_date)
                if bal_data:
                    checkpoints.append({
                        "statement_end_date": end_date,
                        "statement_start_date": start_date or "",
                        "statement_balance": float(bal_data.get("statement_balance_amount", 0.0)),
                        "last_statement_balance": float(bal_data.get("last_statement_balance_amount", 0.0)),
                        "charges_amount": float(bal_data.get("charges_amount", 0.0)),
                        "payments_credits_amount": float(bal_data.get("total_payments_credits_amount", 0.0)),
                        "credits_amount": float(bal_data.get("total_credits_amount", 0.0)),
                        "index": period.get("index", 0)
                    })
            except Exception as e:
                logger.warning(f"Could not fetch historical balance for statement {end_date}: {e}")

        # Sort chronologically by statement_end_date
        checkpoints.sort(key=lambda x: x["statement_end_date"])
        return checkpoints
