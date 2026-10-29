"""Detection rule parameters.

Changing any parameter below RULE_VERSION MUST bump RULE_VERSION (spec Assumptions), except
BENCHMARK_TICKER, which is stored separately on every detection run.
"""

from typing import Final

RULE_VERSION: Final = "1"  # stored with every run (FR-014)

Z_THRESHOLD: Final = 2.0  # flag when |z| >= this (FR-009)
DAILY_CAP: Final = 10  # most anomaly cases kept per evaluated day (FR-010)
MAX_WINDOW_DAYS: Final = 120  # most trading days in the market-model window (FR-006)
MIN_WINDOW_DAYS: Final = 60  # fewer trading days of history -> skip the stock (FR-006, FR-012)
VOLUME_WINDOW_DAYS: Final = 20  # preceding trading days averaged for the volume ratio (FR-011)
MIN_VOLUME_DAYS: Final = 10  # fewer days with volume -> volume ratio unavailable (FR-011)
# more missing_data skips than this share of the universe -> fail the run (FR-023)
MAX_MISSING_DATA_PERCENT: Final = 20

BENCHMARK_TICKER: Final = "QQQ"  # Nasdaq-100 benchmark instrument (FR-004, FR-015)
