CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id VARCHAR PRIMARY KEY,
    provider VARCHAR NOT NULL,
    dataset VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status VARCHAR NOT NULL,
    rows_received BIGINT DEFAULT 0,
    detail VARCHAR
);

CREATE TABLE IF NOT EXISTS securities (
    security_id VARCHAR PRIMARY KEY,
    display_name VARCHAR,
    security_type VARCHAR,
    cik VARCHAR,
    active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS security_identifiers (
    security_id VARCHAR NOT NULL,
    identifier_type VARCHAR NOT NULL,
    identifier_value VARCHAR NOT NULL,
    valid_from DATE,
    valid_to DATE,
    source VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    UNIQUE(identifier_type, identifier_value, valid_from, source)
);

-- Append-only observations preserve provider revisions and retrieval times.
CREATE TABLE IF NOT EXISTS daily_price_observations (
    observation_id VARCHAR PRIMARY KEY,
    security_id VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    adj_close DOUBLE,
    volume BIGINT,
    dividend DOUBLE,
    stock_split DOUBLE,
    source VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    run_id VARCHAR NOT NULL,
    raw_snapshot_path VARCHAR NOT NULL,
    raw_sha256 VARCHAR NOT NULL
);

CREATE VIEW IF NOT EXISTS latest_daily_prices AS
SELECT * EXCLUDE (row_number) FROM (
    SELECT *, row_number() OVER (
        PARTITION BY security_id, trade_date, source
        ORDER BY retrieved_at DESC
    ) AS row_number
    FROM daily_price_observations
) WHERE row_number = 1;

CREATE TABLE IF NOT EXISTS sec_filings (
    accession_number VARCHAR PRIMARY KEY,
    cik VARCHAR NOT NULL,
    form VARCHAR NOT NULL,
    filed_date DATE NOT NULL,
    report_date DATE,
    accepted_at TIMESTAMP,
    primary_document VARCHAR,
    source_url VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    raw_snapshot_path VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS company_facts_observations (
    fact_observation_id VARCHAR PRIMARY KEY,
    cik VARCHAR NOT NULL,
    taxonomy VARCHAR NOT NULL,
    concept VARCHAR NOT NULL,
    unit VARCHAR NOT NULL,
    period_start DATE,
    period_end DATE NOT NULL,
    filed_date DATE NOT NULL,
    available_at TIMESTAMP NOT NULL,
    form VARCHAR,
    accession_number VARCHAR,
    fiscal_year INTEGER,
    fiscal_period VARCHAR,
    value DOUBLE NOT NULL,
    source_url VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    raw_snapshot_path VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS insider_transactions (
    transaction_id VARCHAR PRIMARY KEY,
    accession_number VARCHAR NOT NULL,
    cik VARCHAR NOT NULL,
    security_id VARCHAR,
    owner_name VARCHAR,
    owner_role VARCHAR,
    transaction_date DATE,
    filed_date DATE NOT NULL,
    available_at TIMESTAMP NOT NULL,
    transaction_code VARCHAR,
    shares DOUBLE,
    price_per_share DOUBLE,
    dollar_value DOUBLE,
    ownership_type VARCHAR,
    is_open_market_purchase BOOLEAN,
    source_url VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    raw_payload JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_observations (
    signal_id VARCHAR PRIMARY KEY,
    strategy_name VARCHAR NOT NULL,
    strategy_version VARCHAR NOT NULL,
    security_id VARCHAR NOT NULL,
    observation_date DATE NOT NULL,
    available_at TIMESTAMP NOT NULL,
    entry_price DOUBLE,
    market_regime VARCHAR,
    signal_strength DOUBLE,
    features JSON NOT NULL,
    source_data_version VARCHAR NOT NULL,
    recorded_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id VARCHAR NOT NULL,
    horizon_days INTEGER NOT NULL,
    measured_at TIMESTAMP NOT NULL,
    stock_return DOUBLE,
    benchmark_return DOUBLE,
    excess_return DOUBLE,
    max_drawdown DOUBLE,
    max_favorable_excursion DOUBLE,
    max_adverse_excursion DOUBLE,
    PRIMARY KEY(signal_id, horizon_days)
);
