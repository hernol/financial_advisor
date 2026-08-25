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

# The application values everything in one currency. Mixing them without a
# conversion table produces a total that is quietly wrong, which is worse than
# refusing the position, so anything else is excluded and reported rather than
# summed. Changing this is not a matter of editing the constant: it needs an FX
# table and a decision about which rate applies to a historical trade.
BASE_CURRENCY = "USD"


def _text(name: str, default: str = "") -> str:
    """An environment variable, treating empty as unset.

    ``os.environ.get(name, default)`` only falls back when the variable is
    absent, and a container passes ``VAR=`` for anything the operator left
    blank — so an unset value arrived as "" and silently beat the default. That
    is how the dashboard ended up asking Gemini for an empty model name.
    """
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _optional(name: str) -> str | None:
    """Same, for settings whose absence is meaningful."""
    return _text(name) or None


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
    database_url: str = ""
    api_token: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""

    @property
    def database_target(self) -> "Path | str":
        """Where the data lives: a Postgres URL when set, the SQLite file if not.

        This single choice is what separates a laptop install from a hosted one.
        Nothing else in the application asks which engine it is talking to.
        """
        return self.database_url or self.db_path

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
    db_path = Path(_text("FA_DB_PATH", str(DB_PATH)))
    log_path = Path(_text("FA_LOG_PATH", str(LOG_PATH)))
    database_url = _text("DATABASE_URL")
    api_token = _text("FA_API_TOKEN")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return Settings(
        gemini_api_key=_optional("GEMINI_API_KEY"),
        gemini_model=_text("FA_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        alpha_vantage_key=_optional("ALPHA_VANTAGE_API_KEY"),
        finnhub_key=_optional("FINNHUB_API_KEY"),
        telegram_bot_token=_optional("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_optional("TELEGRAM_CHAT_ID"),
        desktop_notifications=_flag("FA_DESKTOP_NOTIFICATIONS", default=True),
        default_cooldown_hours=_int("FA_COOLDOWN_HOURS", DEFAULT_COOLDOWN_HOURS),
        earnings_warning_days=_int("FA_EARNINGS_WARNING_DAYS", DEFAULT_EARNINGS_WARNING_DAYS),
        local_ai_url=_text("FA_LOCAL_AI_URL", DEFAULT_LOCAL_AI_URL).rstrip("/"),
        local_ai_model=_optional("FA_LOCAL_AI_MODEL"),
        local_ai_api_key=_text("FA_LOCAL_AI_API_KEY", "not-needed"),
        local_ai_timeout=_int("FA_LOCAL_AI_TIMEOUT", DEFAULT_LOCAL_AI_TIMEOUT),
        local_ai_max_tokens=_int("FA_LOCAL_AI_MAX_TOKENS", DEFAULT_LOCAL_AI_MAX_TOKENS),
        benchmark=_text("FA_BENCHMARK", DEFAULT_BENCHMARK).upper(),
        db_path=db_path,
        log_path=log_path,
        database_url=database_url,
        api_token=api_token,
        supabase_url=_text("SUPABASE_URL").rstrip("/"),
        supabase_anon_key=_text("SUPABASE_ANON_KEY"),
        supabase_jwt_secret=_text("SUPABASE_JWT_SECRET"),
    )
