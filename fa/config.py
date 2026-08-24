"""Environment driven configuration. No secret ever lives in source."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(os.environ.get("FA_HOME", Path(__file__).resolve().parent.parent))
DATA_DIR = Path(os.environ.get("FA_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("FA_DB_PATH", DATA_DIR / "financial_analyzer.db"))
LOG_PATH = Path(os.environ.get("FA_LOG_PATH", DATA_DIR / "alerts.log"))

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_COOLDOWN_HOURS = 24
DEFAULT_EARNINGS_WARNING_DAYS = 7
DEFAULT_HISTORY_PERIOD = "2y"
ANALYSIS_HISTORY_PERIOD = "5y"
DEFAULT_LOCAL_AI_URL = "http://localhost:1234/v1"
DEFAULT_LOCAL_AI_TIMEOUT = 240  # the first call also loads the weights
DEFAULT_LOCAL_AI_MAX_TOKENS = 2000
# Index the positions are measured against for relative strength.
DEFAULT_BENCHMARK = "SPY"


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the runtime configuration."""

    gemini_api_key: str | None
    gemini_model: str
    alpha_vantage_key: str | None
    finnhub_key: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    desktop_notifications: bool
    default_cooldown_hours: int
    earnings_warning_days: int
    local_ai_url: str
    local_ai_model: str | None
    local_ai_api_key: str
    local_ai_timeout: int
    local_ai_max_tokens: int
    benchmark: str
    db_path: Path
    log_path: Path

    @property
    def local_ai_enabled(self) -> bool:
        """Local models are opt-in: a model name must be configured."""
        return bool(self.local_ai_url and self.local_ai_model)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def fallback_providers_configured(self) -> bool:
        return bool(self.alpha_vantage_key or self.finnhub_key)


def load_settings() -> Settings:
    """Build settings from the environment."""
    db_path = Path(os.environ.get("FA_DB_PATH", DB_PATH))
    log_path = Path(os.environ.get("FA_LOG_PATH", LOG_PATH))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return Settings(
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
        gemini_model=os.environ.get("FA_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        alpha_vantage_key=os.environ.get("ALPHA_VANTAGE_API_KEY"),
        finnhub_key=os.environ.get("FINNHUB_API_KEY"),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
        desktop_notifications=_flag("FA_DESKTOP_NOTIFICATIONS", default=True),
        default_cooldown_hours=_int("FA_COOLDOWN_HOURS", DEFAULT_COOLDOWN_HOURS),
        earnings_warning_days=_int("FA_EARNINGS_WARNING_DAYS", DEFAULT_EARNINGS_WARNING_DAYS),
        local_ai_url=os.environ.get("FA_LOCAL_AI_URL", DEFAULT_LOCAL_AI_URL).rstrip("/"),
        local_ai_model=os.environ.get("FA_LOCAL_AI_MODEL"),
        local_ai_api_key=os.environ.get("FA_LOCAL_AI_API_KEY", "not-needed"),
        local_ai_timeout=_int("FA_LOCAL_AI_TIMEOUT", DEFAULT_LOCAL_AI_TIMEOUT),
        local_ai_max_tokens=_int("FA_LOCAL_AI_MAX_TOKENS", DEFAULT_LOCAL_AI_MAX_TOKENS),
        benchmark=os.environ.get("FA_BENCHMARK", DEFAULT_BENCHMARK).upper(),
        db_path=db_path,
        log_path=log_path,
    )
