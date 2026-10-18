"""Chrome DevTools Protocol (CDP) connection and session extraction for American Express."""

import json
import itertools
import time
import urllib.request
from typing import Optional, Dict, Any, List
import websocket


class CDPError(Exception):
    """Raised when CDP connection, tab discovery, or session capture fails."""
    pass


def find_amex_tab(port: int = 9222, tab_id: Optional[str] = None) -> Dict[str, Any]:
    """Find the American Express tab in Chrome via CDP."""
    try:
        url = f"http://127.0.0.1:{port}/json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            tabs = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise CDPError(
            f"Could not connect to Chrome debugging port {port}: {e}. "
            f"Ensure Chrome is running with --remote-debugging-port={port}"
        )

    # If specific tab_id requested, check for it
    if tab_id:
        for tab in tabs:
            if tab.get("id") == tab_id:
                return tab
        raise CDPError(f"Specified tab ID '{tab_id}' was not found in Chrome.")

    # Look for active global.americanexpress.com page tab
    for tab in tabs:
        tab_url = tab.get("url", "")
        if "global.americanexpress.com" in tab_url and tab.get("type") == "page":
            return tab

    # Look for any americanexpress.com page tab
    for tab in tabs:
        tab_url = tab.get("url", "")
        if "americanexpress.com" in tab_url and tab.get("type") == "page":
            return tab

    raise CDPError(
        "No open American Express tab found. Please log in to https://global.americanexpress.com in Chrome."
    )


class CDPClient:
    """Manages WebSocket CDP connection and script execution within tab context."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._ws: Optional[websocket.WebSocket] = None
        self._req_counter = itertools.count(1)
        self.connect()

    def connect(self):
        """Establish WebSocket connection."""
        self._ws = websocket.create_connection(self.ws_url, suppress_origin=True, timeout=15)

    def close(self):
        """Close WebSocket connection."""
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a CDP command and await its specific response."""
        if not self._ws:
            raise CDPError("WebSocket connection is not open.")

        cmd_id = next(self._req_counter)
        msg = {"id": cmd_id, "method": method}
        if params:
            msg["params"] = params

        self._ws.send(json.dumps(msg))

        start_time = time.time()
        while time.time() - start_time < 30:
            try:
                self._ws.settimeout(5.0)
                raw = self._ws.recv()
                res = json.loads(raw)
                if res.get("id") == cmd_id:
                    if "error" in res:
                        raise CDPError(f"CDP command error ({method}): {res['error']}")
                    return res.get("result", {})
            except websocket.WebSocketTimeoutException:
                pass

        raise CDPError(f"CDP command timed out awaiting response for method {method}")

    def evaluate(self, expr: str, await_promise: bool = False) -> Any:
        """Evaluate a JavaScript expression and return the evaluated value."""
        params: Dict[str, Any] = {
            "expression": expr,
            "returnByValue": True,
        }
        if await_promise:
            params["awaitPromise"] = True

        result = self.send("Runtime.evaluate", params)
        if "exceptionDetails" in result:
            err = result["exceptionDetails"].get("text", "Unknown evaluation exception")
            raise CDPError(f"JavaScript evaluation error: {err}")

        return result.get("result", {}).get("value")

    def call_fetch(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute fetch() inside the page context to retain cookies, auth, and Akamai context."""
        js_headers = json.dumps(headers or {})
        fetch_options: Dict[str, Any] = {"method": method}
        if headers:
            fetch_options["headers"] = headers
        if body:
            fetch_options["body"] = body

        opts_json = json.dumps(fetch_options)

        expr = f"""
        (async () => {{
            try {{
                let resp = await fetch({json.dumps(url)}, {opts_json});
                let text = await resp.text();
                let contentType = resp.headers.get('content-type') || '';
                let jsonParsed = null;
                if (contentType.includes('application/json') || text.startsWith('{{') || text.startsWith('[')) {{
                    try {{
                        jsonParsed = JSON.parse(text);
                    }} catch (e) {{}}
                }}
                return {{
                    status: resp.status,
                    statusText: resp.statusText,
                    headers: Object.fromEntries(resp.headers.entries()),
                    text: text,
                    json: jsonParsed
                }};
            }} catch (err) {{
                return {{
                    error: err.toString(),
                    status: 0
                }};
            }}
        }})()
        """
        res = self.evaluate(expr, await_promise=True)
        if not res or res.get("error"):
            err_msg = res.get("error") if res else "Unknown fetch error"
            raise CDPError(f"In-page fetch error for {url}: {err_msg}")
        return res


def extract_accounts_from_page(client: CDPClient) -> List[Dict[str, Any]]:
    """Extract card account information from the Redux store in the active Amex tab."""
    extract_script = """
    (() => {
        let root = document.getElementById('root');
        if (!root) return [];
        let reactKey = Object.keys(root).find(k => k.startsWith('__react'));
        let fiber = root[reactKey];
        let curr = fiber;
        let store = null;
        let max = 60;
        while (curr && max-- > 0) {
            if (curr.memoizedProps && curr.memoizedProps.store) {
                store = curr.memoizedProps.store;
                break;
            }
            if (curr.stateNode && curr.stateNode.store) {
                store = curr.stateNode.store;
                break;
            }
            curr = curr.child;
        }
        if (!store || !store.getState) return [];
        let state = store.getState();
        let jsState = state.toJS ? state.toJS() : state;

        let mycaRoot = jsState.modules ? jsState.modules['axp-myca-root'] : null;
        if (!mycaRoot || !mycaRoot.products) return [];

        let products = mycaRoot.products;
        let registry = products.registry?.types?.CARD_PRODUCT || [];
        let detailsList = products.details?.types?.CARD_PRODUCT?.productsList || {};

        let accounts = [];
        for (let item of registry) {
            let token = item.accountToken;
            let details = detailsList[token] || {};
            let acctInfo = details.account || {};
            let prodInfo = details.product || {};
            let statusInfo = details.status || {};

            accounts.push({
                account_token: token,
                account_key: item.accountKey || '',
                opaque_account_id: item.opaqueAccountId || token,
                hiped_account_token: item.hipedAccountToken || '',
                card_name: prodInfo.description || 'American Express Card',
                display_account_number: acctInfo.display_account_number || '',
                relationship: acctInfo.relationship || 'BASIC',
                account_setup_date: statusInfo.account_setup_date || '',
                account_status: statusInfo.account_status || ['Active'],
                payment_type: prodInfo.payment_type || 'REVOLVE',
                digital_asset_id: prodInfo.digital_asset_id || ''
            });
        }
        return accounts;
    })()
    """
    accounts = client.evaluate(extract_script)
    if not isinstance(accounts, list):
        return []
    return accounts
