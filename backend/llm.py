"""Единая обёртка над LLM: JSON-вывод, проверка схемой, повторы с текстом ошибки, понятные исключения.

Провайдеры вызываются через REST (requests), без SDK: меньше зависимостей, легче запуск.
"""
import json
import re
import time
from typing import Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from . import config, db

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Базовая ошибка ИИ. message — текст для пользователя."""
    user_message = "ИИ-сервис временно недоступен. Запись сохранена, разбор выполнен по правилам."


class LLMUnavailable(LLMError):
    user_message = "Нет связи с ИИ-сервисом (сеть или ключ). Включён резервный режим."


class LLMRateLimit(LLMError):
    user_message = "Превышен лимит запросов к ИИ. Подождите минуту; пока работает резервный режим."


class LLMAuthError(LLMUnavailable):
    user_message = "ИИ-сервис отклонил API-ключ. Проверьте ключ в .env; пока работает резервный режим."


class LLMModelNotFound(LLMUnavailable):
    pass


class LLMOverloaded(LLMUnavailable):
    user_message = "Модели Gemini сейчас перегружены (ответ 503). Попробуйте через минуту; пока работает резервный режим."


class LLMBadOutput(LLMError):
    user_message = "ИИ вернул ответ в неверном формате даже после повторов. Использован резервный разбор."


def _post(url: str, headers: dict, body: dict) -> dict:
    try:
        r = requests.post(url, headers=headers, json=body, timeout=config.LLM_TIMEOUT)
    except requests.Timeout as e:
        raise LLMOverloaded(f"timeout: {e}") from e  # модель не успела ответить — пробуем другую
    except requests.ConnectionError as e:
        raise LLMUnavailable(str(e)) from e
    if r.status_code == 429:
        raise LLMRateLimit(r.text[:300])
    if r.status_code in (401, 403):
        raise LLMAuthError(f"auth {r.status_code}: {r.text[:200]}")
    if r.status_code == 404:
        raise LLMModelNotFound(r.text[:200])
    if r.status_code >= 500:
        raise LLMOverloaded(f"HTTP {r.status_code}: {r.text[:200]}")
    if r.status_code >= 400:
        raise LLMUnavailable(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _gemini(system: str, user: str, key: str, model: str) -> str:
    data = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": key, "Content-Type": "application/json"},
        {"systemInstruction": {"parts": [{"text": system}]},
         "contents": [{"role": "user", "parts": [{"text": user}]}],
         "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}},
    )
    try:
        return "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as e:
        raise LLMBadOutput(f"empty gemini response: {str(data)[:200]}") from e


_working = {}  # запоминаем рабочую пару ключ/модель, чтобы не перебирать каждый раз


def _call_gemini(system: str, user: str) -> str:
    """Перебор: рабочая пара → другие модели (при 404/503) → другой ключ (при 429/401). Два прохода с паузой."""
    keys = config.api_keys()
    models = list(dict.fromkeys([_working.get("model") or config.LLM_MODEL] + config.GEMINI_FALLBACK_MODELS))
    if _working.get("key") in keys:
        keys = [_working["key"]] + [k for k in keys if k != _working["key"]]
    last: LLMError = LLMUnavailable("no keys")
    started = time.monotonic()
    for round_ in range(2):
        if round_:
            if not isinstance(last, LLMOverloaded):
                break
            time.sleep(2)  # перегрузка обычно кратковременная
        for key in keys:
            for model in models:
                if time.monotonic() - started > config.LLM_TOTAL_BUDGET:
                    raise last  # не держим пользователя дольше бюджета — дальше резервный режим
                try:
                    text = _gemini(system, user, key, model)
                    _working.update(key=key, model=model)
                    return text
                except (LLMModelNotFound, LLMOverloaded) as e:
                    last = e
                    continue  # следующая модель с тем же ключом
                except (LLMRateLimit, LLMAuthError) as e:
                    last = e
                    break  # следующий ключ
    raise last


def _call_provider(system: str, user: str) -> str:
    p, key, model = config.LLM_PROVIDER, config.api_key(), config.LLM_MODEL
    if not config.ai_enabled():
        raise LLMUnavailable("no api key")
    if p == "gemini":
        return _call_gemini(system, user)
    if p == "openai":
        data = _post(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {"model": model, "temperature": 0.1, "response_format": {"type": "json_object"},
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        return data["choices"][0]["message"]["content"]
    if p == "anthropic":
        data = _post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            {"model": model, "max_tokens": 1500, "temperature": 0.1, "system": system + "\nОтвечай только JSON-объектом.",
             "messages": [{"role": "user", "content": user}]},
        )
        return "".join(b.get("text", "") for b in data.get("content", []))
    raise LLMUnavailable(f"unknown provider {p}")


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def call_json(system: str, user: str, schema: Type[T], purpose: str) -> T:
    """Вызывает модель и возвращает объект схемы. При невалидном ответе повторяет запрос с текстом ошибки."""
    if not config.ai_enabled():
        raise LLMUnavailable("no api key")  # резервный режим — не считаем это неудачным вызовом
    started, attempts, last_err = time.monotonic(), 0, None
    prompt = user
    try:
        for attempts in range(1, config.LLM_MAX_RETRIES + 2):
            try:
                raw = _call_provider(system, prompt)
            except LLMError:
                raise
            except Exception as e:  # любая неожиданная ошибка клиента → резервный режим, а не падение
                raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
            try:
                result = schema.model_validate(_parse_json(raw))
                db.log_llm_call(purpose, config.LLM_PROVIDER, _working.get("model", config.LLM_MODEL), int((time.monotonic() - started) * 1000), True, attempts, None)
                return result
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = str(e)[:800]
                prompt = (f"{user}\n\nТвой предыдущий ответ не прошёл проверку схемы:\n{last_err}\n"
                          f"Верни исправленный JSON строго по схеме, без пояснений.")
        raise LLMBadOutput(last_err or "invalid output")
    except LLMError as e:
        db.log_llm_call(purpose, config.LLM_PROVIDER, config.LLM_MODEL, int((time.monotonic() - started) * 1000), False, attempts, f"{type(e).__name__}: {e}")
        raise
