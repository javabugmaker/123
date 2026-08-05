"""
InstitutionScanner — config.py

Central configuration for the Institutional Accumulation Scanner.
All tunable parameters live here so no magic numbers appear in application code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR: Final[Path] = Path(__file__).resolve().parent
CACHE_DIR: Final[Path] = BASE_DIR / "cache"
OUTPUT_DIR: Final[Path] = BASE_DIR / "output"
LOG_DIR: Final[Path] = BASE_DIR / "logs"
FUNDAMENTAL_DATA_PATH: str = ""
FUNDAMENTAL_REFRESH_FORCE: bool = False

# Ensure directories exist
for _d in (CACHE_DIR, OUTPUT_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ======================================================================
# Ticker & Market Filters
# ======================================================================
MIN_PRICE: float = 5.0  # Minimum close price (CNY) — ignore penny stocks
MAX_PRICE: float = 800.0  # Maximum close price for A-shares
MIN_VOLUME: int = 200_000  # Minimum daily volume (shares)
MIN_MARKET_CAP: float = 1e8  # Minimum market cap (CNY) — ignore micro-caps
EXCLUDED_SECURITY_KEYWORDS: tuple[str, ...] = (
    "债",
    "货币",
    "同业存单",
    "短融",
    "中票",
    "REIT",
    "浙商沪",
)

# ======================================================================
# Data Download
# ======================================================================
HISTORY_YEARS: int = 10  # Years of daily data to pull

DOWNLOAD_THREADS: int = 10
DOWNLOAD_RATE_LIMIT_PAUSE: float = 0.1
DOWNLOAD_RETRIES: int = 2  # retries on transient errors (401s, 429s, timeouts) — don't waste time retrying dead URLs
DOWNLOAD_TIMEOUT: int = (
    10  # seconds per ticker (lower = less accumulated delay on dead URLs)
)
MARKET_CAP_CACHE_TTL_DAYS: int = 1
MAX_DOWNLOAD_ERRORS: int = 2000  # abort if this many consecutive errors (harmless 404s from delisted tickers are common)

# Ticker list sources (free, no API key required)
TICKER_SOURCES: list[str] = [
    # NASDAQ official FTP lists
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt",
    # Alternative free sources (used as fallback)
]

# ETF list sources
ETF_SOURCES: list[str] = [
    # Common free ETF lists
]

# ======================================================================
# Indicator Parameters
# ======================================================================
MA_PERIODS: tuple[int, ...] = (20, 50, 100, 200)
EMA_PERIODS: tuple[int, ...] = (20, 50, 200)
ATR_PERIODS: tuple[int, ...] = (14, 50)
ADX_PERIOD: int = 14
CCI_PERIOD: int = 20
ROC_PERIOD: int = 21

VOLUME_MA_PERIODS: tuple[int, ...] = (20, 60, 120)
VOLUME_RATIO_PERIODS: tuple[int, ...] = (20, 60)
VOLUME_ZSCORE_PERIOD: int = 60
VOLUME_TREND_PERIOD: int = 60

OBV_SLOPE_PERIOD: int = 20
AD_SLOPE_PERIOD: int = 20
CMF_PERIOD: int = 21
MFI_PERIOD: int = 14
VWAP_PERIOD: int = 252

MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL: int = 9
RSI_PERIODS: tuple[int, ...] = (14, 21)

HV_PERIODS: tuple[int, ...] = (20, 60)
BB_PERIOD: int = 20
BB_STD: float = 2.0
DONCHIAN_PERIOD: int = 20

REGRESSION_PERIOD: int = 60

# Volume Profile
VOLUME_PROFILE_BINS: int = 50
VOLUME_PROFILE_LOOKBACK: int = 252

# ======================================================================
# Filter Thresholds
# ======================================================================

# Long-term bear market
BEAR_DECLINE_PCT: float = -20.0  # Minimum decline over the lookback period for A-shares
BEAR_LOOKBACK_YEARS: int = 2  # Years for decline calculation
BEAR_MA200_DECLINING_DAYS: int = 40  # MA200 must be declining for at least N days

# Bottom consolidation
CONSOLIDATION_DAYS: int = 45  # Lookback for consolidation check
CONSOLIDATION_MAX_RANGE_PCT: float = 20.0  # Max % range during consolidation

# Volume accumulation
VOLUME_ACCUM_RATIO: float = 1.2  # Vol MA20 > Vol MA120 * ratio
VOLUME_ACCUM_MIN_DAYS: int = 20  # Must persist for this many consecutive days

# OBV Bullish Divergence
OBV_DIVERGENCE_LOOKBACK: int = 60  # Days to check for price low vs OBV low

# CMF
CMF_THRESHOLD: float = 0.0  # CMF must exceed this

# AD Line
AD_SLOPE_LOOKBACK: int = 30  # AD slope must be positive over N days

# Volatility Contraction
ATR_COMPRESSION_LOOKBACK: int = 60  # ATR must decline over this many days
BB_WIDTH_COMPRESSION_LOOKBACK: int = 60


# ======================================================================
# Scoring Weights (total = 100)
# ======================================================================
@dataclass(frozen=True)
class ScoringWeights:
    trend: float = 20.0
    volume: float = 25.0
    accumulation: float = 25.0
    volatility: float = 15.0
    structure: float = 15.0


SCORING_WEIGHTS: Final[ScoringWeights] = ScoringWeights()

# ======================================================================
# Output
# ======================================================================
TOP_N_REPORT: int = 50
TOP_N_PARQUET: int = 200

SCORING_VERSION: str = "2026-08-05-v6-evidence-aware-ranking"

# Per-ticker historical evidence is only allowed to influence the composite
# rank after more than a couple of independent observations.  This prevents a
# single recent signal from moving a stock ahead of a stronger technical setup.
BACKTEST_MIN_SAMPLES_FOR_RANKING: Final[int] = 3
BACKTEST_FULL_WEIGHT_SAMPLES: Final[int] = 10

FRESHNESS_MULTIPLIERS: Final[tuple[tuple[int, float], ...]] = (
    (0, 1.00),
    (1, 0.98),
    (2, 0.95),
    (5, 0.90),
    (999_999, 0.80),
)
INSTITUTIONAL_TIER_A_SCORE: Final[float] = 85.0
INSTITUTIONAL_TIER_B_SCORE: Final[float] = 75.0
INSTITUTIONAL_TIER_C_SCORE: Final[float] = 65.0
INSTITUTIONAL_TIER_WAIT_LABEL: Final[str] = "D级等待确认"
INSTITUTIONAL_TIER_TRAP_LABEL: Final[str] = "D级陷阱池"
VALUE_TRAP_RISK_THRESHOLD: Final[float] = 60.0
INSTITUTIONAL_SCORE_TIERS: Final[tuple[tuple[str, float], ...]] = (
    ("A级机构启动", INSTITUTIONAL_TIER_A_SCORE),
    ("B级观察", INSTITUTIONAL_TIER_B_SCORE),
    ("C级价值观察", INSTITUTIONAL_TIER_C_SCORE),
)

# ======================================================================
# Runtime
# ======================================================================
SCAN_THREADS: int = (
    24  # Threads for parallel indicator calculation (numpy releases GIL) — 3060 GPU 高配CPU建议值
)
DOWNLOAD_THREADS: int = 20  # 并行下载线程数（3060高配系统，网络IO密集可开更多）
CHECKPOINT_INTERVAL: int = 100  # Save checkpoint every N tickers
ENABLE_CHECKPOINT: bool = True

# ETF Fund Flows (optional, requires a free source)
ENABLE_FUND_FLOWS: bool = True

# Volume Profile in scoring
ENABLE_VOLUME_PROFILE: bool = True

# ======================================================================
# 集中日志配置
# ======================================================================
import logging
import sys
import time
from typing import Literal

_LOG_LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def setup_logging(
    name: str = "institution_scanner",
    level: str | int = logging.INFO,
    log_to_file: bool = True,
    log_dir: Path | None = None,
    console_level: str | int = logging.INFO,
    file_level: str | int = logging.DEBUG,
) -> logging.Logger:
    """
    集中式日志配置：统一管理所有模块的日志格式、级别和输出目标。

    使用方式（各模块只需调用此函数即可，无需手动创建 FileHandler）：
        logger = setup_logging("institution_scanner.scanner")

    Args:
        name: 日志器名称，建议按模块命名如 'institution_scanner.scanner'
        level: 日志器全局级别
        log_to_file: 是否输出到文件
        log_dir: 日志文件目录，默认使用 LOG_DIR
        console_level: 控制台输出级别
        file_level: 文件输出级别

    Returns:
        配置好的 logging.Logger 实例
    """
    level = _LOG_LEVEL_MAP.get(str(level).upper(), level) if isinstance(level, str) else level
    console_level = _LOG_LEVEL_MAP.get(str(console_level).upper(), console_level) if isinstance(console_level, str) else console_level
    file_level = _LOG_LEVEL_MAP.get(str(file_level).upper(), file_level) if isinstance(file_level, str) else file_level

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    formatter_console = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    formatter_file = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(formatter_console)
    logger.addHandler(console)

    if log_to_file:
        _log_dir = log_dir or LOG_DIR
        _log_dir.mkdir(parents=True, exist_ok=True)
        safe_name = name.replace("institution_scanner", "scanner").replace(".", "_")
        log_path = _log_dir / f"{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}.log"
        fh = logging.FileHandler(log_path, mode="a")
        fh.setLevel(file_level)
        fh.setFormatter(formatter_file)
        logger.addHandler(fh)

    return logger
