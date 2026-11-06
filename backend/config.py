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
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2", "")  # запасной ключ: используется при лимите или отказе первого
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

DEFAULT_MODELS = {
    "gemini": "gemini-flash-lite-latest",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
}
LLM_MODEL = os.getenv("LLM_MODEL", "") or DEFAULT_MODELS.get(LLM_PROVIDER, "")
# если модель недоступна (404), пробуем следующую
GEMINI_FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest"]  # 2.5-модели Google больше не выдаёт новым пользователям
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "12"))      # на одну попытку
LLM_TOTAL_BUDGET = float(os.getenv("LLM_TOTAL_BUDGET", "30"))  # на весь перебор моделей и ключей
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

ANALYSIS_WINDOW_DAYS = int(os.getenv("ANALYSIS_WINDOW_DAYS", "30"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.7"))


def api_keys() -> list[str]:
    raw = {"gemini": [GEMINI_API_KEY, GEMINI_API_KEY_2], "openai": [OPENAI_API_KEY], "anthropic": [ANTHROPIC_API_KEY]}.get(LLM_PROVIDER, [])
    keys = [k.strip() for item in raw for k in item.split(",") if k.strip()]
    return list(dict.fromkeys(keys))


def api_key() -> str:
    keys = api_keys()
    return keys[0] if keys else ""


def ai_enabled() -> bool:
    return LLM_PROVIDER in DEFAULT_MODELS and bool(api_key())


def mode_label() -> str:
    if ai_enabled():
        n = len(api_keys())
        return f"ИИ: {LLM_PROVIDER} · {LLM_MODEL}" + (f" · ключей: {n}" if n > 1 else "")
    return "Резервный режим: разбор по правилам, без ИИ (нет API-ключа)"
