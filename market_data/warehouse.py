from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import time
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import yfinance as yf
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "database" / "market.duckdb"
RAW_DIR = ROOT / "data" / "raw"
SCHEMA_PATH = ROOT / "market_data" / "schema.sql"
UNIVERSE_PATH = ROOT / "universe.csv"
SOURCE = "yfinance"


def connect(path: Path = DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA_PATH.read_text())
    return con


def load_universe(path: Path = UNIVERSE_PATH) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def init_db(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        print(f"Initialized DuckDB warehouse: {args.db}")


def status(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        tables = ["securities", "daily_price_observations", "sec_filings",
                  "company_facts_observations", "insider_transactions", "signal_observations"]
        for table in tables:
            count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"{table:30} {count:>10,} rows")
        latest = con.execute("SELECT max(trade_date) FROM latest_daily_prices").fetchone()[0]
        print(f"Latest stored price date: {latest or 'no prices loaded'}")
        runs = con.execute("""SELECT started_at, provider, dataset, status, rows_received
                              FROM ingestion_runs ORDER BY started_at DESC LIMIT 5""").fetchall()
        if runs:
            print("\nRecent ingestion runs:")
            for row in runs:
                print("  " + " | ".join(str(x) for x in row))


def export_dashboard(args: argparse.Namespace) -> None:
    """Export a self-contained price snapshot consumed by the static dashboard."""
    with connect(args.db) as con:
        records = con.execute("""SELECT p.security_id, s.display_name, s.security_type,
                                      p.trade_date, p.close, p.adj_close, p.volume
                               FROM latest_daily_prices p
                               JOIN securities s USING (security_id)
                               WHERE p.source=?
                               ORDER BY p.security_id, p.trade_date""", [SOURCE]).fetchall()
    grouped: dict[str, list[tuple]] = {}
    names: dict[str, tuple[str, str]] = {}
    for symbol, name, kind, trade_date, close, adj_close, volume in records:
        grouped.setdefault(symbol, []).append((trade_date, close, adj_close, volume))
        names[symbol] = (name or symbol, kind or "equity")

    exported = []
    for symbol, bars in grouped.items():
        bars.sort(key=lambda x: x[0])
        dates = [b[0] for b in bars]
        closes = [float(b[2] if b[2] is not None else b[1]) if (b[2] is not None or b[1] is not None) else None for b in bars]
        valid_closes = [v for v in closes if v is not None]
        if not valid_closes:
            continue
        latest_i = len(bars) - 1
        latest_close = closes[latest_i]
        if latest_close is None:
            continue
        actual_close = float(bars[latest_i][1]) if bars[latest_i][1] is not None else latest_close
        def pct_change(lookback: int):
            prior_i = max(0, latest_i - lookback)
            prior = closes[prior_i]
            return round((latest_close / prior - 1) * 100, 2) if prior else None
        ma50 = [v for v in closes[max(0, latest_i - 49):latest_i + 1] if v is not None]
        ma200 = [v for v in closes[max(0, latest_i - 199):latest_i + 1] if v is not None]
        volumes = [int(b[3]) for b in bars[max(0, latest_i - 19):latest_i + 1] if b[3] is not None]
        avg_volume = round(sum(volumes) / len(volumes)) if volumes else None
        price_vs_50 = round((latest_close / (sum(ma50) / len(ma50)) - 1) * 100, 2) if ma50 else None
        price_vs_200 = round((latest_close / (sum(ma200) / len(ma200)) - 1) * 100, 2) if ma200 else None
        # Realized-volatility context is derived from adjusted daily closes.
        # It is useful for research, but it is not implied volatility or an
        # options premium: those require an options quote/history provider.
        returns = [math.log(closes[i] / closes[i - 1])
                   for i in range(1, len(closes))
                   if closes[i] is not None and closes[i - 1] is not None
                   and closes[i] > 0 and closes[i - 1] > 0]

        def annualized_vol(window: list[float]):
            if len(window) < 2:
                return None
            mean = sum(window) / len(window)
            variance = sum((value - mean) ** 2 for value in window) / (len(window) - 1)
            return math.sqrt(variance * 252) * 100

        vol20 = annualized_vol(returns[-20:]) if len(returns) >= 20 else None
        vol60 = annualized_vol(returns[-60:]) if len(returns) >= 60 else None
        vol252 = annualized_vol(returns[-252:]) if len(returns) >= 252 else None
        rolling_vol20 = [annualized_vol(returns[i - 20:i])
                         for i in range(max(20, len(returns) - 251), len(returns) + 1)]
        rolling_vol20 = [value for value in rolling_vol20 if value is not None]
        vol20_rank = (round(sum(value <= vol20 for value in rolling_vol20)
                            / len(rolling_vol20) * 100)
                      if vol20 is not None and rolling_vol20 else None)
        name, kind = names[symbol]
        exported.append({
            "ticker": symbol, "name": name, "kind": kind,
            "as_of": dates[latest_i].isoformat(), "close": round(actual_close, 4),
            "return_1m_pct": pct_change(21), "return_1y_pct": pct_change(252),
            "price_vs_50d_pct": price_vs_50, "price_vs_200d_pct": price_vs_200,
            "realized_vol_20d_pct": round(vol20, 2) if vol20 is not None else None,
            "realized_vol_60d_pct": round(vol60, 2) if vol60 is not None else None,
            "realized_vol_1y_pct": round(vol252, 2) if vol252 is not None else None,
            "realized_vol_20d_1y_rank_pct": vol20_rank,
            "avg_volume_20d": avg_volume, "history_start": dates[0].isoformat(),
            "bars": len(bars),
            "series": [[dates[i].isoformat(), round(closes[i], 4),
                        int(bars[i][3]) if bars[i][3] is not None else None]
                       for i in range(len(bars)) if closes[i] is not None],
        })
    if not exported:
        raise SystemExit("No price history is available to export. Run the prices command first.")
    tracked = [row["symbol"].upper() for row in load_universe()]
    loaded = {row["ticker"] for row in exported}
    missing = sorted(set(tracked) - loaded)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "provider": SOURCE, "universe_count": len(tracked),
               "symbol_count": len(exported), "missing_symbols": missing,
               "symbols": exported}
    args.output.write_text("window.MARKET_UNIVERSE = " + json.dumps(payload, separators=(",", ":")) + ";\n")
    print(f"Exported {len(exported)} symbols to {args.output}")


def ingest_insiders(args: argparse.Namespace) -> None:
    """Collect recent non-derivative Form 4 transactions for tracked equities."""
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent or "@" not in user_agent:
        raise SystemExit(
            "Set SEC_USER_AGENT to an identifying app name and contact email, "
            "for example 'Market Dashboard research contact@example.com'."
        )

    def get_json(url: str):
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def get_bytes(url: str):
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    universe = load_universe(args.universe)
    equities = [item["symbol"].upper() for item in universe if item.get("kind") == "equity"]
    ticker_rows = get_json("https://www.sec.gov/files/company_tickers.json")
    cik_by_ticker = {str(item["ticker"]).upper(): str(item["cik_str"]).zfill(10)
                     for item in ticker_rows.values()}
    cutoff = date.today() - timedelta(days=args.days)
    transactions: list[dict] = []
    examined = 0

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def text_under(node, path: str):
        parts = path.split("/")
        current = [node]
        for part in parts:
            current = [child for parent in current for child in parent
                       if local(child.tag) == part]
            if not current:
                return None
        return current[0].text.strip() if current[0].text else None

    for ticker in equities:
        cik = cik_by_ticker.get(ticker)
        if not cik:
            continue
        time.sleep(0.12)
        submissions = get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
        recent = submissions.get("filings", {}).get("recent", {})
        rows = zip(recent.get("form", []), recent.get("filingDate", []),
                   recent.get("accessionNumber", []), recent.get("primaryDocument", []))
        eligible = [(form, filed, accession, primary) for form, filed, accession, primary in rows
                    if form in {"4", "4/A"} and filed >= cutoff.isoformat()]
        for form, filed, accession, primary in eligible:
            if examined >= args.max_filings:
                break
            examined += 1
            accession_path = accession.replace("-", "")
            archive = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}"
            time.sleep(0.12)
            if primary.lower().endswith(".xml"):
                xml_bytes = get_bytes(f"{archive}/{primary}")
            else:
                index = get_json(f"{archive}/index.json")
                xml_files = [item["name"] for item in index.get("directory", {}).get("item", [])
                             if item.get("name", "").lower().endswith(".xml")
                             and "xsd" not in item.get("name", "").lower()]
                if not xml_files:
                    continue
                time.sleep(0.12)
                xml_bytes = get_bytes(f"{archive}/{xml_files[0]}")
            try:
                root = ET.fromstring(xml_bytes)
            except ET.ParseError:
                continue
            owner = text_under(root, "reportingOwner/reportingOwnerId/rptOwnerName") or "Unknown reporting owner"
            relationship = next((node for node in root.iter()
                                 if local(node.tag) == "reportingOwnerRelationship"), None)
            role = "Insider"
            if relationship is not None:
                title = text_under(relationship, "officerTitle")
                if title:
                    role = title
                elif text_under(relationship, "isDirector") == "1":
                    role = "Director"
            for txn in (node for node in root.iter() if local(node.tag) == "nonDerivativeTransaction"):
                code = text_under(txn, "transactionCoding/transactionCode")
                if code not in {"P", "S"}:
                    continue
                trade_date = text_under(txn, "transactionDate/value")
                shares = text_under(txn, "transactionAmounts/transactionShares/value")
                price = text_under(txn, "transactionAmounts/transactionPricePerShare/value")
                acquired = text_under(txn, "transactionAmounts/transactionAcquiredDisposedCode/value")
                try:
                    qty = float(shares) if shares else None
                    px = float(price) if price else None
                except ValueError:
                    qty, px = None, None
                transactions.append({
                    "ticker": ticker, "company": next((x["name"] for x in universe if x["symbol"].upper() == ticker), ticker),
                    "owner": owner, "role": role, "form": form, "filed": filed,
                    "date": trade_date, "code": code, "acquired_disposed": acquired,
                    "shares": qty, "price": px,
                    "value": round(qty * px, 2) if qty is not None and px is not None else None,
                    "accession": accession,
                    "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/{primary}",
                })

    transactions.sort(key=lambda item: (item["filed"], item["ticker"]), reverse=True)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    snapshot = {"generated_at": retrieved_at, "provider": "SEC EDGAR Form 4", "days": args.days,
                "filings_examined": examined, "transaction_count": len(transactions),
                "transactions": transactions}
    raw_dir = RAW_DIR / "insiders" / "sec"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"form4-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    raw_path.write_text(json.dumps(snapshot, indent=2))
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    args.output.write_text("window.MARKET_INSIDER_FLOWS = " + json.dumps(snapshot, separators=(",", ":")) + ";\n")
    print(f"Saved {len(transactions)} Form 4 purchase/sale lines from {examined} filings to {args.output}")
    print(f"Raw snapshot: {raw_path.relative_to(ROOT)} (sha256 {digest})")


def ingest_prices(args: argparse.Namespace) -> None:
    universe = load_universe(args.universe)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else [u["symbol"] for u in universe]
    info = {u["symbol"].upper(): u for u in universe}
    missing = sorted(set(symbols) - set(info))
    if missing:
        raise SystemExit(f"Symbols are not in {args.universe}: {', '.join(missing)}")

    run_id = str(uuid.uuid4())
    retrieved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    day_tag = retrieved_at.strftime("%Y%m%dT%H%M%SZ")
    raw_run_dir = RAW_DIR / "prices" / SOURCE / f"retrieved_at={day_tag}" / f"run={run_id}"
    raw_run_dir.mkdir(parents=True, exist_ok=True)
    con = connect(args.db)
    con.execute("INSERT INTO ingestion_runs VALUES (?, ?, ?, ?, NULL, 'running', 0, NULL)",
                [run_id, SOURCE, "daily_prices", retrieved_at])
    total = 0
    errors: list[str] = []
    try:
        for symbol in symbols:
            try:
                start = args.start
                if args.update:
                    latest = con.execute(
                        "SELECT max(trade_date) FROM latest_daily_prices WHERE security_id=? AND source=?",
                        [symbol, SOURCE],
                    ).fetchone()[0]
                    start = (latest - timedelta(days=7)).isoformat() if latest else "2015-01-01"
                start = start or "2015-01-01"
                frame = yf.Ticker(symbol).history(
                    start=start,
                    end=args.end,
                    interval="1d",
                    auto_adjust=False,
                    actions=True,
                    repair=False,
                )
                if frame is None or frame.empty:
                    errors.append(f"{symbol}: provider returned no rows")
                    continue
                raw_path = raw_run_dir / f"{symbol}.csv"
                frame.to_csv(raw_path, index=True)
                digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                snapshot = str(raw_path.relative_to(ROOT))
                security = info[symbol]
                con.execute("""INSERT INTO securities VALUES (?, ?, ?, NULL, TRUE, ?)
                               ON CONFLICT (security_id) DO UPDATE SET
                                 display_name=excluded.display_name,
                                 security_type=excluded.security_type,
                                 active=TRUE, updated_at=excluded.updated_at""",
                            [symbol, security["name"], security["kind"], retrieved_at])
                con.execute("""INSERT INTO security_identifiers
                               VALUES (?, 'ticker', ?, NULL, NULL, ?, ?)
                               ON CONFLICT DO NOTHING""",
                            [symbol, symbol, SOURCE, retrieved_at])
                for idx, row in frame.iterrows():
                    trade_date = idx.date()
                    def number(key: str):
                        value = row.get(key)
                        return None if value is None or str(value) == "nan" else float(value)
                    vol = number("Volume")
                    observation_id = hashlib.sha256(
                        f"{run_id}|{symbol}|{trade_date.isoformat()}".encode()).hexdigest()
                    con.execute("""INSERT INTO daily_price_observations VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", [
                        observation_id, symbol, trade_date, number("Open"), number("High"),
                        number("Low"), number("Close"), number("Adj Close"),
                        int(vol) if vol is not None else None, number("Dividends"),
                        number("Stock Splits"), SOURCE, retrieved_at, run_id, snapshot, digest,
                    ])
                    total += 1
                print(f"{symbol}: saved {len(frame):,} bars")
            except Exception as exc:  # retain per-symbol progress for provider failures
                errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
        finished = datetime.now(timezone.utc).replace(tzinfo=None)
        state = "partial" if errors and total else "failed" if errors else "completed"
        con.execute("UPDATE ingestion_runs SET finished_at=?, status=?, rows_received=?, detail=? WHERE run_id=?",
                    [finished, state, total, json.dumps(errors) if errors else None, run_id])
        print(f"Ingestion {state}: {total:,} price observations from {len(symbols)} symbols.")
        if errors:
            print("Provider issues:")
            for error in errors:
                print(f"  - {error}")
            if not total:
                raise SystemExit(1)
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local market research warehouse")
    p.add_argument("--db", type=Path, default=DB_PATH, help="DuckDB file path")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create the local schema").set_defaults(func=init_db)
    sub.add_parser("status", help="Show warehouse row counts").set_defaults(func=status)
    export = sub.add_parser("export-dashboard", help="Generate the static dashboard's price snapshot")
    export.add_argument("--output", type=Path, default=ROOT / "universe-data.js")
    export.set_defaults(func=export_dashboard)
    insiders = sub.add_parser("insiders", help="Collect recent SEC Form 4 purchases and sales for tracked equities")
    insiders.add_argument("--days", type=int, default=45, help="Lookback window for filing dates (default: 45)")
    insiders.add_argument("--max-filings", type=int, default=120, help="Maximum Form 4 filings to inspect")
    insiders.add_argument("--output", type=Path, default=ROOT / "insider-data.js")
    insiders.add_argument("--universe", type=Path, default=UNIVERSE_PATH)
    insiders.set_defaults(func=ingest_insiders)
    prices = sub.add_parser("prices", help="Append daily OHLCV history from yfinance")
    prices.add_argument("--start", help="Inclusive start date (YYYY-MM-DD); defaults to 2015-01-01")
    prices.add_argument("--end", default=date.today().isoformat(), help="Exclusive end date")
    prices.add_argument("--update", action="store_true", help="Refresh the last week for each symbol, or backfill from 2015 if missing")
    prices.add_argument("--symbols", help="Comma-separated subset from universe.csv; default is full starter universe")
    prices.add_argument("--universe", type=Path, default=UNIVERSE_PATH)
    prices.set_defaults(func=ingest_prices)
    return p


if __name__ == "__main__":
    parser = build_parser()
    parsed = parser.parse_args()
    parsed.func(parsed)
