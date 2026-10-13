# Local Market Research Warehouse

This is the local-first foundation for the market intelligence dashboard. The database and raw snapshots stay on this machine. DuckDB stores queryable history; provider snapshots are retained separately so later provider revisions do not erase what was previously observed.

## First data source: daily prices

The editable `universe.csv` tracks 104 instruments: 77 equities, broad-market ETFs, sector ETFs, and macro proxies. It is a research starter list, not a full point-in-time universe. It is not a point-in-time or survivorship-aware universe. Only 103 names currently have price histories; one symbol did not return data from the provider. Coverage is not comprehensive or survivorship-aware. Do not use results from this list to claim historical strategy performance across the full U.S. equity market.

The price adapter uses `yfinance` for research convenience. It is an unofficial interface to Yahoo Finance and may have gaps, revised history, rate limits, or changing terms. It is not a real-time feed. Validate important observations against an authoritative provider before relying on them.

Each run appends observations to `daily_price_observations`, writes a per-symbol CSV snapshot under `data/raw/prices/`, calculates a SHA-256 digest, and records an ingestion run. The `latest_daily_prices` view selects the most recently retrieved provider version for each security/date. Both the old observations and the raw snapshot files remain available.

## Setup

From this directory, create an isolated environment and install the two data packages:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Initialize the schema and inspect the empty warehouse:

```sh
python -m market_data.warehouse init
python -m market_data.warehouse status
```

Load the starter universe from 2015 onward (end date is exclusive):

```sh
python -m market_data.warehouse prices --start 2015-01-01
```

Update a smaller group, for example:

```sh
python -m market_data.warehouse prices --start 2026-10-01 --symbols SPY,QQQ,IWM,AAPL,NVDA
```

For a daily refresh, overlap the latest stored date by one week to catch late corrections and missed sessions:

```sh
python -m market_data.warehouse prices --update
```

After loading prices, regenerate the browser-readable snapshot used by the dashboard:

```sh
python -m market_data.warehouse export-dashboard
```

This writes `universe-data.js` beside `index.html`. It includes adjusted daily close and volume history, 20-session, 60-session, and 1-year annualized realized volatility, and the latest 20-session volatility's percentile versus its recent year of observations. These are price-derived measures, not options implied volatility. The CSV now tracks 104 instruments across 77 equities, broad benchmarks, sector funds, and macro proxies. The current exported snapshot has prices for 103 of those symbols; the unavailable symbol is listed in the scanner coverage note. The scanner does not screen valuation.


## Portfolio workspace

The Portfolio Risk tab accepts shares, cash-secured puts, and covered calls against symbols in the local price snapshot. Positions and the cash balance are stored only in this browser's local storage; use **Export backup** to save a JSON copy and **Import backup** to restore it. Share values and share P/L use the latest stored close and any cost basis entered. Put collateral is strike × 100 × contracts; the page checks call coverage against shares entered for the same ticker. It does not mark option positions to market, connect to a broker, or submit orders.

The Watchlist is also saved in local browser storage. A browser privacy setting or cleared site data can remove local records, so export backups for anything you want to preserve.

## Hypothesis Lab

The Hypothesis Lab supports a research-only workflow: preserve the original idea, define candidates, benchmark, direction, horizon, price rule, catalyst, and invalidation condition; review current technical evidence; and run a non-overlapping historical event study using adjusted daily closes. Saved hypotheses and revisions persist in this browser's local storage. Use **Export research log** to make a JSON backup and **Import backup** to restore or merge it. The sample cybersecurity candidates are CIBR, HACK, CRWD, and PANW; their price histories are included in the current local snapshot. Rerun the price collector and export command to refresh them.

The event study is descriptive rather than a validated forecast: it has no transaction costs, point-in-time universe reconstruction, options, filings, macro, or fundamental evidence. Do not interpret the hit rate as a calibrated probability or recommendation.

## Insider Form 4 snapshot

The Insider Flows tab reads `insider-data.js`. To collect recent non-derivative purchases and sales for tracked equities, set `SEC_USER_AGENT` to an identifying app name and a real contact email (required by SEC fair-access guidance), then run:

```sh
export SEC_USER_AGENT='Market Dashboard research your-email@example.com'
python -m market_data.warehouse insiders --days 45
```

The collector writes the browser snapshot to `insider-data.js` and a retained JSON copy under `data/raw/insiders/sec/`. The view distinguishes Form 4 transaction codes P (purchase) and S (sale), and links each row to its SEC filing. Filings can include compensation and planned transactions; review the source filing before interpreting a row as a signal. Reload the dashboard after generating either snapshot.

The Insider Flows page now puts the largest reported purchase, the most recently filed transaction, and the largest reported sale above the transaction table. If no snapshot exists, it says so at the top rather than implying that no insider trades occurred.

## Pre-generated research snapshots

The Opportunity Scanner places 36 distinct price-history screens above its filters: 12 trend-continuation names, 12 constructive pullbacks, and 12 weak-trend downside watches. Cards show multi-period returns, relative strength versus SPY, RSI, moving-average levels, liquidity, realized volatility, and strategy and risk notes. Each stock card also includes a company-specific business/theme narrative, likely operating drivers, thesis risks, and a link to SEC company filings. These are durable context lenses, not live news or catalyst alerts; check current filings, dates, and policy developments before relying on a catalyst. The **Refresh scan** button reloads the local data files; run the price collector first if you want newer prices. These are research screens, not live entries or personalized recommendations.

Insider Flows shows up to nine actual reported transactions, ranked by disclosed transaction value. It displays fewer when fewer filings are present and never invents activity to reach nine.

Options Signals puts nine names with the highest historical realized-volatility percentiles and SPY/QQQ realized volatility above the detailed table. This local price snapshot does not contain option chains, implied volatility, contract volume, open interest, or dealer gamma exposure (GEX); the page marks those feeds as unavailable and does not infer them from stock prices.

Database location: `data/database/market.duckdb`. Raw snapshots: `data/raw/`. Use `--db /path/to/market.duckdb` to relocate the database without changing the code.

## Point-in-time and audit design

- Price rows retain `retrieved_at`, provider, ingestion run, raw snapshot path, and snapshot digest.
- Filing and financial fact schemas keep filing dates, acceptance times where available, accession numbers, source URLs, and retrieval timestamps.
- Signal observations are versioned and carry an `available_at` time, feature payload, and source-data version.
- Signal outcomes are stored separately so a signal definition and its later measured returns are not conflated.
- Insider transactions include filing/availability fields and a transaction-code field so grants, exercises, and open-market purchases can be distinguished.

For filing data that only exposes a filing date and not an exact public timestamp, downstream strategies must not assume they could trade on that same day's close. Use a conservative next-session availability rule and retain the original filed date.

## Current scope and next increments

Implemented now: persistent DuckDB schema, ingestion-run ledger, starter security universe, append-only daily OHLCV observations, immutable provider CSV snapshots, a price-loader CLI, a realized-volatility dashboard export, and an SEC Form 4 snapshot collector.

Schema is prepared for SEC filing metadata, XBRL company facts, insider transactions, versioned signals, and measured outcomes. The Form 4 collector exports a separate snapshot for the dashboard; institutional holdings, company facts, delisted-security history, point-in-time index membership, news, and historical options chains remain future data-source work.

The static dashboard reads the exported local price snapshot rather than querying DuckDB in the browser. After a price update, rerun `python -m market_data.warehouse export-dashboard` and reload `index.html` to refresh its market, scanner, company, and realized-volatility views. The week-ahead calendar is a manually curated snapshot; verify linked official sources and refresh it after the displayed week.
