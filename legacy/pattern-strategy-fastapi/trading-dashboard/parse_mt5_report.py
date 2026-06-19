"""
parse_mt5_report.py
-------------------
Cross-platform (Mac / Linux / Windows) parser for MetaTrader 5 account-history
reports. Reads the HTML, HTM, or CSV file that MT5 produces from
History → Report → ..., and writes a trades.json (and optionally data.js) in
the format the dashboard expects.

Uses only the Python 3 standard library — no MetaTrader5 package required.

Usage:
    python3 parse_mt5_report.py /path/to/ReportHistory.html
    python3 parse_mt5_report.py /path/to/Report.csv --out trades.json
    python3 parse_mt5_report.py report.html --datajs       # also writes data.js
"""

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


VALID_SETUPS = {"DT", "DB", "HS", "IHS", "CHS", "TT", "TB", "TRI", "REC", "TL", "FK"}

COMMENT_RE = re.compile(
    r"^\s*(?P<setup>[A-Za-z]{1,3})\s*[-_]\s*(?P<tf>M\d{1,2}|H\d|D\d)\s*(?:\|(?P<note>.*))?$",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------------
# HTML table extractor
# ----------------------------------------------------------------------------
class TableExtractor(HTMLParser):
    """Pulls every <table> into a list of [ [cellText, ...], ... ]."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []          # list of tables, each = list of rows
        self.current_table = None
        self.current_row = None
        self.current_cell = None  # accumulates text
        self.in_cell = False
        self.in_row = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self.current_table = []
        elif tag == "tr" and self.current_table is not None:
            self.current_row = []
            self.in_row = True
        elif tag in ("td", "th") and self.in_row:
            self.current_cell = []
            self.in_cell = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self.in_cell:
            text = "".join(self.current_cell).strip()
            # Collapse internal whitespace
            text = re.sub(r"\s+", " ", text)
            self.current_row.append(text)
            self.current_cell = None
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.current_row:
                self.current_table.append(self.current_row)
            self.current_row = None
            self.in_row = False
        elif tag == "table":
            if self.current_table:
                self.tables.append(self.current_table)
            self.current_table = None

    def handle_data(self, data):
        if self.in_cell and self.current_cell is not None:
            self.current_cell.append(data)


# ----------------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------------
def parse_setup_tag(comment):
    if not comment:
        return None, None, "LIVE"
    m = COMMENT_RE.match(comment.strip())
    if not m:
        return None, None, "LIVE"
    setup = m.group("setup").upper()
    if setup not in VALID_SETUPS:
        setup = None
    tf = m.group("tf").upper()
    note = (m.group("note") or "").lower()
    if "tg" in note or "telegram" in note:
        cat = "TELEGRAM"
    elif "rf" in note or "red" in note:
        cat = "RED_FLAG"
    else:
        cat = "LIVE"
    return setup, tf, cat


def to_number(s):
    if s is None:
        return 0.0
    s = re.sub(r"[\s,]", "", str(s))
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def parse_mt5_date(s):
    if not s:
        return datetime.fromtimestamp(0, tz=timezone.utc).isoformat()
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          int(m.group(4)), int(m.group(5)), int(m.group(6) or 0),
                          tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc).isoformat()


def map_columns(header_row):
    """Map an MT5 column-header row to a {field_name: column_index} dict."""
    cols = {}
    seen_time = False
    seen_price = False
    for i, raw in enumerate(header_row):
        h = raw.lower().strip()
        if h == "time":
            cols["closeTime" if seen_time else "time"] = i
            if seen_time is False:
                seen_time = True
            continue
        if "time" in h and seen_time and "closeTime" not in cols:
            cols["closeTime"] = i
            continue
        if h in ("position", "ticket", "order", "deal"):
            cols.setdefault("ticket", i)
        elif h == "symbol":
            cols["symbol"] = i
        elif h == "type":
            cols["type"] = i
        elif h in ("volume", "size", "lots"):
            cols["volume"] = i
        elif "s/l" in h.replace(" ", "") or "stop" in h:
            cols["sl"] = i
        elif "t/p" in h.replace(" ", "") or "take" in h:
            cols["tp"] = i
        elif h == "commission":
            cols["commission"] = i
        elif h in ("swap", "rollover"):
            cols["swap"] = i
        elif h in ("profit", "p&l", "p/l"):
            cols["profit"] = i
        elif h in ("comment", "note"):
            cols["comment"] = i
        elif h == "price":
            if not seen_price:
                cols["price"] = i
                seen_price = True
            else:
                cols.setdefault("closePrice", i)
    return cols


def row_to_trade(cells, m):
    def txt(key):
        i = m.get(key)
        if i is None or i >= len(cells):
            return ""
        return cells[i]

    typ = txt("type").lower()
    side = "sell" if ("sell" in typ or "short" in typ) else "buy"

    entry = to_number(txt("price"))
    close = to_number(txt("closePrice"))
    sl    = to_number(txt("sl"))
    tp    = to_number(txt("tp"))
    profit = to_number(txt("profit")) + to_number(txt("commission")) + to_number(txt("swap"))

    comment = txt("comment")
    setup, tf, category = parse_setup_tag(comment)

    achieved_r = 0.0
    if sl and entry and sl != entry:
        risk = abs(entry - sl)
        move = abs(close - entry)
        sign = 1 if ((side == "buy" and close >= entry) or (side == "sell" and close <= entry)) else -1
        if risk > 0:
            achieved_r = sign * (move / risk)
    elif profit != 0:
        achieved_r = 1.0 if profit > 0 else -1.0

    trade = {
        "ticket": int(re.sub(r"\D", "", txt("ticket")) or 0),
        "symbol": txt("symbol").upper(),
        "setup": setup,
        "timeframe": tf,
        "category": category,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "openTime": parse_mt5_date(txt("time")),
        "closeTime": parse_mt5_date(txt("closeTime")),
        "pnl": round(profit, 2),
        "rr": round(achieved_r, 2),
        "comment": comment,
    }
    trade["adherence"] = score_adherence(trade)
    return trade


def score_adherence(t):
    broken = []
    longs = {"DB", "IHS", "TB"}
    shorts = {"DT", "HS", "TT", "CHS"}
    if t["setup"] in longs and t["side"] != "buy":
        broken.append("Direction matches setup")
    if t["setup"] in shorts and t["side"] != "sell":
        broken.append("Direction matches setup")
    if not t["setup"]:
        broken.append("Setup tagged in comment")
    if t["timeframe"] in ("M1", "M5"):
        broken.append("TF ≥ M15")
    if t["sl"] and t["tp"] and t["entry"]:
        risk = abs(t["entry"] - t["sl"])
        reward = abs(t["tp"] - t["entry"])
        if risk > 0 and reward / risk < 1.99:
            broken.append("Min 1:2 R:R")
    return {"score": max(0, 100 - len(broken) * 15), "broken": broken}


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------
def parse_html(path: Path):
    parser = TableExtractor()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))

    best = []
    for table in parser.tables:
        # Find a section header that mentions positions / deals / transactions
        header_idx = -1
        for i, row in enumerate(table):
            joined = " ".join(row).lower()
            if any(k in joined for k in ("position", "deal", "trade", "transaction")) and len(joined) < 80:
                header_idx = i
                break
        if header_idx < 0:
            continue
        # Column-header row = next row with ≥ 8 cells
        col_idx = -1
        for j in range(header_idx + 1, min(header_idx + 4, len(table))):
            if len(table[j]) >= 8:
                col_idx = j
                break
        if col_idx < 0:
            continue
        col_map = map_columns(table[col_idx])
        if "symbol" not in col_map or "type" not in col_map:
            continue
        data_rows = []
        for r in table[col_idx + 1:]:
            if len(r) < 8:
                break
            # Skip totals
            if r[0] and re.match(r"^(total|balance|summary|profit)", r[0], re.I):
                continue
            data_rows.append(r)
        if len(data_rows) > len(best):
            best = data_rows
            best_map = col_map

    trades = [row_to_trade(r, best_map) for r in best] if best else []
    return trades


def parse_csv(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    first = text.splitlines()[0] if text else ""
    delim = "\t" if "\t" in first else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if r and any(c.strip() for c in r)]

    header_idx = -1
    for i, r in enumerate(rows):
        low = " ".join(r).lower()
        if "symbol" in low and "profit" in low and ("time" in low or "price" in low):
            header_idx = i
            break
    if header_idx < 0:
        raise SystemExit("Could not find a closed-positions header row in the CSV")

    col_map = map_columns(rows[header_idx])
    trades = []
    for r in rows[header_idx + 1:]:
        if len(r) < 6:
            break
        trades.append(row_to_trade(r, col_map))
    return trades


def extract_account_info(text):
    out = {"currency": "USD", "balance": 0.0, "broker": ""}
    m = re.search(r"(?:Currency|Deposit Currency)\s*[:\s]\s*([A-Z]{3})", text, re.I)
    if m:
        out["currency"] = m.group(1)
    m = re.search(r"(?:Balance|Closing\s*balance)\s*[:\s]\s*([\d,]+\.?\d*)", text, re.I)
    if m:
        try:
            out["balance"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = re.search(r"Account\s*[:\s]\s*(\d+)", text, re.I)
    if m:
        out["login"] = int(m.group(1))
    m = re.search(r"(?:Broker|Company)\s*[:\s]\s*([^\n<]+)", text, re.I)
    if m:
        out["broker"] = m.group(1).strip()[:60]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report", help="Path to the MT5 .html / .htm / .csv account-history report")
    ap.add_argument("--out", default="trades.json", help="Output JSON path (default: trades.json)")
    ap.add_argument("--datajs", action="store_true",
                    help="Also write data.js next to index.html so the dashboard can be opened via file://")
    args = ap.parse_args()

    path = Path(args.report).expanduser().resolve()
    if not path.exists():
        sys.exit(f"File not found: {path}")

    ext = path.suffix.lower()
    if ext in (".html", ".htm"):
        trades = parse_html(path)
    elif ext == ".csv":
        trades = parse_csv(path)
    else:
        sys.exit(f"Unsupported file type: {ext}. Use .html, .htm, or .csv")

    if not trades:
        sys.exit("No closed trades found — is this an Account History report?")

    text = path.read_text(encoding="utf-8", errors="replace")
    account = extract_account_info(text)

    trades.sort(key=lambda t: t["closeTime"], reverse=True)

    payload = {
        "account": account,
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "isMockData": False,
        "trades": trades,
        "openPositions": [],
        "_source": f"parse_mt5_report.py ({path.name})",
    }

    out_path = Path(args.out).resolve()
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(trades)} trades → {out_path}")

    if args.datajs:
        js_path = out_path.with_name("data.js")
        js_path.write_text(
            "// Auto-generated by parse_mt5_report.py — do not edit by hand.\n"
            "window.tradeData = " + json.dumps(payload, indent=2) + ";\n",
            encoding="utf-8",
        )
        print(f"Wrote data.js → {js_path}")

    # Quick stats
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    total_pnl = sum(t["pnl"] for t in trades)
    avg_rr = sum(t["rr"] for t in trades) / len(trades) if trades else 0
    wr = len(wins) / len(trades) * 100 if trades else 0
    print()
    print(f"  Trades:   {len(trades)}  ({len(wins)} wins · {len(losses)} losses)")
    print(f"  Win rate: {wr:.1f}%")
    print(f"  Avg R:R:  {avg_rr:.2f}")
    print(f"  Net P&L:  {total_pnl:.2f} {account.get('currency','')}")


if __name__ == "__main__":
    main()
