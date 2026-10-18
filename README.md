# monarch-import-from-amex

[![CI / Test Suite](https://img.shields.io/badge/tests-100%25%20passing-brightgreen.svg)]()
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-lightgrey.svg)]()

Automated extraction and conversion tool for American Express credit cards: exports continuous daily balance history and complete transactions into **Monarch Money** CSV format using browser session capture over the Chrome DevTools Protocol (CDP).

---

## Overview

Monarch Money allows importing historical transactions and continuous account balances via CSV. However, American Express exports only partial statement periods or Quicken files that lack continuous daily balance trajectories.

`monarch-import-from-amex` bridges this gap:
1. **Automated In-Session Capture**: Connects to an existing, authenticated American Express tab in Chrome via CDP. No credentials, passwords, or 2FA tokens are stored or transmitted.
2. **Back-to-Inception Historical Statements**: Queries servicing APIs and document metadata spanning back to account inception (over 10 years of statements).
3. **Continuous Daily Balance History**: Reconciles historical statement closing balance checkpoints with posted transaction activity to construct a complete, uninterrupted daily balance ledger.
4. **Monarch Liability Accounting**: By default, formats owed credit card debt as negative balances (e.g. `-150.00`) so that Monarch's Net Worth calculations (`Assets + Liabilities`) balance accurately.
5. **Intelligent Merchant Normalization & Categorization**: Normalizes messy statement descriptors (e.g., `LYFT *TRIP 855-280-0278 CA` -> `Lyft`) and auto-maps categories to Monarch standards (Groceries, Restaurants, Gas, Subscriptions, Payments).
6. **Detailed Audit Trails**: Preserves authorized card members, Amex reference numbers, and billing statement dates in transaction notes. Also saves an audit-ready raw JSON dataset for instant offline reconversion.

---

## Features

- **Multi-Card Discovery**: Automatically extracts all card products registered to your American Express account profile.
- **Combined & Per-Card CSVs**: Generates discrete balance history and transaction CSVs per card, plus a unified `amex_transactions_all.csv` when managing multiple cards.
- **Pending & Posted Tracking**: Tags pending activity seamlessly alongside posted transactions.
- **Zero Network Fingerprinting**: Executes API requests from inside the legitimate browser tab context using the browser's own session cookies, tokens, and security headers.
- **100% Offline Test Suite**: Fully covered by hermetic synthetic unit tests with zero network dependencies or PII.

---

## Monarch Money CSV Specifications

### 1. Balance History (`amex_<card_slug>_balances.csv`)
Conforms to Monarch Money's Account Balance History import format:
```csv
Date,Balance
2026-10-01,-693.96
2026-10-02,-693.96
2026-10-03,-26.31
```
*Note: Use the `--positive-balances` flag if you prefer owed balances represented as positive numbers.*

### 2. Transactions (`amex_<card_slug>_transactions.csv`)
Conforms to Monarch Money's standard Transaction import schema:
```csv
Date,Merchant,Category,Account,Original Statement,Notes,Amount
2026-10-16,Lyft,Taxi & Ride Share,American Express - Blue Cash Preferred® (••••12345),LYFT 855-280-0278 CA,Card Member: JOHN DOE; Ref: 12345; Stmt: 2026-11-03,-16.70
2026-10-15,American Express Payment,Credit Card Payment,American Express - Blue Cash Preferred® (••••12345),ONLINE PAYMENT - THANK YOU,Ref: PMT9876,100.00
```
- **Charges & Fees**: Negative amounts (outflows).
- **Payments & Credits**: Positive amounts (inflows).

---

## Installation

Clone the repository and install dependencies in your Python 3 environment:

```bash
git clone https://github.com/dylanmtaylor/monarch-import-from-amex.git
cd monarch-import-from-amex
pip install -e .
```

Or install dependencies directly:
```bash
pip install websocket-client
```

---

## How to Use

### Step 1: Launch Chrome with Remote Debugging

Launch Google Chrome with the remote debugging port enabled:

**Linux:**
```bash
google-chrome --remote-debugging-port=9222
```
*Flatpak:*
```bash
flatpak run com.google.Chrome --remote-debugging-port=9222
```

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

**Windows:**
```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

### Step 2: Log into American Express

1. Open a tab in that Chrome window and navigate to [https://global.americanexpress.com/activity](https://global.americanexpress.com/activity).
2. Complete your standard login and 2FA authentication. Keep this tab open.

### Step 3: Run Extraction

Run the full end-to-end pipeline:

```bash
python3 -m amex_to_monarch.cli all
```

Output files will be written to `./output/`:
- `amex_<card_slug>_balances.csv`: Continuous daily balances for Monarch.
- `amex_<card_slug>_transactions.csv`: Formatted transactions for Monarch.
- `amex_transactions_all.csv`: Consolidated transactions across all accounts (if multiple cards).
- `amex_raw_data.json`: Complete raw API dataset for backup and re-conversion.

---

## CLI Reference

```
usage: amex-to-monarch [-h] {all,capture,convert} ...

Extract American Express card transactions and continuous daily balance history
into Monarch Money CSV format.

positional arguments:
  {all,capture,convert}
    all                 Full automated capture and export pipeline
    capture             Capture raw data from Chrome CDP into JSON cache
    convert             Convert an existing JSON dataset to Monarch CSVs

options:
  -h, --help            show this help message and exit
```

### Subcommands & Options

#### `all` (default)
Runs extraction over CDP and exports Monarch CSVs in one step:
```bash
python3 -m amex_to_monarch.cli all [OPTIONS]
```
- `--port PORT`: Chrome DevTools debugging port (default: `9222`).
- `--tab-id TAB_ID`: Target a specific Chrome tab ID.
- `--output-dir DIR`: Directory for output CSVs (default: `output`).
- `--cache-file FILE`: Path to write the raw JSON dataset (default: `amex_raw_data.json`).
- `--positive-balances`: Report credit card balances as positive numbers instead of Monarch negative debt convention.
- `--input-file FILE`: Skip CDP capture and run conversion directly from a saved JSON cache.

#### `capture`
Extracts and saves the raw Amex dataset to JSON without generating CSVs:
```bash
python3 -m amex_to_monarch.cli capture --output-file amex_backup.json
```

#### `convert`
Converts a previously captured JSON file offline into Monarch CSVs:
```bash
python3 -m amex_to_monarch.cli convert amex_raw_data.json --output-dir ./output
```

---

## Importing into Monarch Money

1. In Monarch Money, navigate to **Accounts** and select your American Express card (or create a manual account if adding an unlinked card).
2. **Import Transactions**:
   - Click the three dots `...` in the top-right corner of the account view.
   - Click **Upload CSV** / **Import Transactions**.
   - Select `amex_<card_slug>_transactions.csv`.
3. **Import Balance History**:
   - In the account view, scroll down to the **Balance History** chart.
   - Click **Edit** -> **Upload balance history CSV**.
   - Select `amex_<card_slug>_balances.csv`.

---

## Running Unit Tests

The test suite contains 100% synthetic fixtures with no network requirements or real user credentials:

```bash
python3 -m unittest discover tests -v
```

---

## Security & Privacy

- **Zero Credentials Handled**: The tool never requests, inspects, or stores your American Express username, password, or security codes.
- **Local Only**: All data extraction, parsing, and CSV generation occurs 100% locally on your machine. No telemetry or external analytics.
- **Git Safety**: The included `.gitignore` prevents sensitive financial records (`*.csv`, `*.json`, `*.pdf`, `output/`) from ever being committed to source control.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
