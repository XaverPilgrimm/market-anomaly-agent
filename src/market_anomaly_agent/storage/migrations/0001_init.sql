-- Initial schema for Nasdaq-100 anomaly detection (data-model.md).

CREATE TABLE universe_constituents (
    ticker           TEXT PRIMARY KEY,
    company_name     TEXT NOT NULL,
    snapshot_date    DATE NOT NULL,
    source           TEXT NOT NULL,
    share_class_note TEXT NULL
);

CREATE TABLE detection_runs (
    evaluated_date         DATE PRIMARY KEY,
    detection_rule_version TEXT NOT NULL,
    computed_at            TIMESTAMPTZ NOT NULL,
    benchmark_ticker       TEXT NOT NULL,
    benchmark_return       NUMERIC NOT NULL
);

-- Per-run snapshot of the bars a run fetched and used: adjusted prices are restated for earlier
-- days after every split or dividend, so bars are keyed by the run's evaluated_date.
CREATE TABLE daily_price_bars (
    evaluated_date DATE NOT NULL REFERENCES detection_runs (evaluated_date),
    ticker         TEXT NOT NULL,
    trading_date   DATE NOT NULL,
    open           NUMERIC NOT NULL,
    close          NUMERIC NOT NULL,
    volume         NUMERIC NOT NULL,
    source         TEXT NOT NULL,
    fetched_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (evaluated_date, ticker, trading_date)
);

CREATE TABLE evaluated_stock_records (
    evaluated_date         DATE NOT NULL REFERENCES detection_runs (evaluated_date),
    ticker                 TEXT NOT NULL,
    "return"               NUMERIC NOT NULL,
    expected_return        NUMERIC NOT NULL,
    abnormal_return        NUMERIC NOT NULL,
    z_score                NUMERIC NOT NULL,
    alpha                  NUMERIC NOT NULL,
    beta                   NUMERIC NOT NULL,
    estimation_window_days SMALLINT NOT NULL CHECK (estimation_window_days BETWEEN 60 AND 120),
    PRIMARY KEY (evaluated_date, ticker)
);

CREATE TABLE anomaly_cases (
    evaluated_date                  DATE NOT NULL,
    ticker                          TEXT NOT NULL,
    rank                            SMALLINT NOT NULL CHECK (rank BETWEEN 1 AND 10),
    volume_ratio                    NUMERIC NULL,
    volume_ratio_unavailable_reason TEXT NULL,
    PRIMARY KEY (evaluated_date, ticker),
    FOREIGN KEY (evaluated_date, ticker)
        REFERENCES evaluated_stock_records (evaluated_date, ticker),
    UNIQUE (evaluated_date, rank),
    CHECK ((volume_ratio IS NULL) = (volume_ratio_unavailable_reason IS NOT NULL))
);

CREATE TABLE skipped_stock_records (
    evaluated_date DATE NOT NULL REFERENCES detection_runs (evaluated_date),
    ticker         TEXT NOT NULL,
    reason_code    TEXT NOT NULL
        CHECK (reason_code IN ('insufficient_history', 'missing_data', 'cap_exceeded')),
    reason_detail  TEXT NULL,
    PRIMARY KEY (evaluated_date, ticker)
);
