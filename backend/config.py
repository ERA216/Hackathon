"""Настройки из переменных окружения (.env). Ключи в коде не хранятся."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "smena.db")))

# gemini | openai | anthropic | mock. Без ключа автоматически включается mock (резервный режим).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
}
LLM_MODEL = os.getenv("LLM_MODEL", "") or DEFAULT_MODELS.get(LLM_PROVIDER, "")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "25"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

ANALYSIS_WINDOW_DAYS = int(os.getenv("ANALYSIS_WINDOW_DAYS", "30"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.7"))


def api_key() -> str:
    return {"gemini": GEMINI_API_KEY, "openai": OPENAI_API_KEY, "anthropic": ANTHROPIC_API_KEY}.get(LLM_PROVIDER, "")


def ai_enabled() -> bool:
    return LLM_PROVIDER in DEFAULT_MODELS and bool(api_key())


def mode_label() -> str:
    if ai_enabled():
        return f"ИИ: {LLM_PROVIDER} · {LLM_MODEL}"
    return "Резервный режим: разбор по правилам, без ИИ (нет API-ключа)"
