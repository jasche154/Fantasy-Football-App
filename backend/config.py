import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BACKEND_DIR / "database" / "fantasy.db"
# Tests set FANTASY_ENV_FILE="" so real keys in backend/.env never leak into them.
ENV_FILE = os.environ.get("FANTASY_ENV_FILE", str(BACKEND_DIR / ".env")) or None


class Settings(BaseSettings):
    """App configuration, read from environment variables and backend/.env."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    database_url: str = f"sqlite:///{DEFAULT_DB_PATH}"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]

    groq_api_key: str | None = None
    # The 120b model hallucinates far fewer players than 20b, on the same free tier.
    groq_model: str = "openai/gpt-oss-120b"
    # AI providers to try in order when one is rate-limited or down (services/llm_client.py),
    # e.g. "groq,gemini,openrouter". Providers without a key are skipped. Gemini's and
    # OpenRouter's free tiers may use prompts (your league's rosters) for training.
    llm_chain: str = "groq"
    gemini_api_key: str | None = None
    gemini_model: str | None = None        # default: services.llm_client.PROVIDERS["gemini"].model
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    ollama_model: str | None = None
    ollama_base_url: str | None = None     # default http://localhost:11434/v1
    # Snap counts reach nflverse from Pro Football Reference, whose terms restrict using
    # their content to prompt AI models. Keep them out of tool results unless opted in.
    agent_share_pfr_data: bool = False

    fantasypros_api_key: str | None = None
    # The Odds API (free plan: 500 credits/month) for Vegas player props in Week X rankings.
    odds_api_key: str | None = None
    # Let the Week X rankings endpoint refresh stale Yahoo/ESPN/props snapshots itself.
    weekly_auto_refresh: bool = True
    # While the API runs, load each finished week's stats (which updates the learned source
    # weights) and save the next week's pre-game projections. See services/auto_update.py.
    auto_update: bool = True
    # Outside sources to fetch. Yahoo's and ESPN's endpoints are unofficial (see
    # docs/DATA_SOURCES.md); drop either here to stop contacting it.
    weekly_sources: list[str] = ["sleeper_status", "yahoo_experts", "espn", "vegas_props"]

    yahoo_client_id: str | None = None
    yahoo_client_secret: str | None = None
    yahoo_redirect_uri: str = "oob"
    token_encryption_key: str | None = None

    # Optional single-user token. When set, every /api request except /api/meta must send
    # "Authorization: Bearer <token>" (enter it once in the web app's Settings). Leave unset
    # for localhost-only use; host and origin checks still apply either way.
    api_token: str | None = None

    # Daily copies of the SQLite database while the API runs (services/backup.py).
    backups: bool = True
    backup_dir: str = str(BACKEND_DIR / "database" / "backups")
    backup_keep: int = 7

    # Last week of the fantasy season (playoffs included); drives "weeks remaining".
    fantasy_end_week: int = 18          # the fantasy final; playoffs default to weeks 16-18


@lru_cache
def get_settings() -> Settings:
    return Settings()
