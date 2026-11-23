"""Free-tier AI text generation with automatic fallback between providers.

Provider order (all have genuinely free tiers, all optional):
  1. Google Gemini  - https://aistudio.google.com/apikey
  2. Groq           - https://console.groq.com/keys
  3. OpenRouter     - https://openrouter.ai/keys   (models ending in :free)
  4. Ollama         - local, http://localhost:11434  (unlimited, fully offline)

If no key is configured the system falls back to a deterministic local
template writer so the pipeline never breaks.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests

TIMEOUT = 120


class AIError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of an LLM response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AIError(f"Model did not return valid JSON: {text[:400]}") from exc
    raise AIError(f"Model did not return JSON: {text[:400]}")


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
GEMINI_PREFERRED = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
]

# models that failed in this process: name -> failure count
_gemini_failures: dict[str, int] = {}
_gemini_working: str | None = None
_gemini_cache: list[str] | None = None

GEMINI_CALL_TIMEOUT = 75
GEMINI_TOTAL_BUDGET = 210


def _gemini_model_list(api_key: str) -> list[str]:
    """Models this key can use, best flash model first. Cached for the process."""
    global _gemini_cache
    override = os.environ.get("GEMINI_MODEL", "").strip()
    models: list[str] = []
    if override:
        models.append(override)

    if _gemini_cache is not None:
        for name in _gemini_cache:
            if name not in models:
                models.append(name)
        return models

    discovered: list[str] = []
    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
            timeout=30,
        )
        if response.status_code == 200:
            for model in response.json().get("models", []):
                methods = model.get("supportedGenerationMethods") or []
                name = (model.get("name") or "").replace("models/", "")
                if "generateContent" in methods and name:
                    discovered.append(name)
    except Exception:  # noqa: BLE001
        pass

    def rank(name: str) -> int:
        score = 0
        if "flash" in name:
            score -= 2
        if "latest" in name:
            score -= 1
        if "preview" in name:
            score += 6
        if "pro" in name:
            score += 3
        if any(word in name for word in ("image", "tts", "transcribe", "research", "computer", "audio", "antigravity")):
            score += 30
        return score

    discovered.sort(key=lambda n: (rank(n), n))
    _gemini_cache = discovered

    for name in GEMINI_PREFERRED + discovered:
        if name not in models:
            models.append(name)
    # never burn more than a handful of models on one call
    return models[:14]


def _call_gemini_once(api_key: str, model: str, system: str, user: str, json_mode: bool) -> str:
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": f"{system}\n\n{user}"}]}],
        "generationConfig": {"temperature": 0.8, "maxOutputTokens": 3000},
    }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    response = requests.post(url, json=payload, timeout=GEMINI_CALL_TIMEOUT)
    if response.status_code == 503:
        raise AIError(f"Gemini {model} is busy (503 high demand)")
    if response.status_code == 404:
        raise AIError(f"Gemini {model} is not available to this key (404)")
    if response.status_code != 200:
        raise AIError(f"Gemini {model} returned {response.status_code}: {response.text[:200]}")
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise AIError(f"Gemini {model} returned no candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise AIError(f"Gemini {model} returned an empty response")
    return text


def _call_gemini(api_key: str, system: str, user: str, json_mode: bool) -> str:
    """Try models in order. Failures are remembered so one bad model is not retried."""
    global _gemini_working

    if _gemini_working:
        try:
            return _call_gemini_once(api_key, _gemini_working, system, user, json_mode)
        except Exception:  # noqa: BLE001
            _gemini_failures[_gemini_working] = _gemini_failures.get(_gemini_working, 0) + 1
            _gemini_working = None

    errors: list[str] = []
    deadline = time.time() + GEMINI_TOTAL_BUDGET
    for model in _gemini_model_list(api_key):
        if time.time() > deadline:
            errors.append("time budget reached")
            break
        if _gemini_failures.get(model, 0) >= 2:
            continue
        try:
            result = _call_gemini_once(api_key, model, system, user, json_mode)
            _gemini_working = model
            _gemini_failures[model] = 0
            return result
        except Exception as exc:  # noqa: BLE001
            _gemini_failures[model] = _gemini_failures.get(model, 0) + 1
            errors.append(f"{model}: {exc}")
    raise AIError("No Gemini model worked -> " + " | ".join(errors[:4]))


def _call_groq(api_key: str, system: str, user: str, json_mode: bool) -> str:
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    payload = {
        "model": model,
        "messages": _messages(system, user),
        "temperature": 0.8,
        "max_tokens": 3000,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise AIError(f"Groq {response.status_code}: {response.text[:300]}")
    return response.json()["choices"][0]["message"]["content"]


def _call_openrouter_once(api_key: str, model: str, system: str, user: str, json_mode: bool) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": _messages(system, user),
        "temperature": 0.8,
        "max_tokens": 2500,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://localhost",
            "X-Title": "Video Auto Publisher",
        },
        json=payload,
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise AIError(f"OpenRouter {model} returned {response.status_code}: {response.text[:250]}")
    message = response.json()["choices"][0]["message"]
    content = (message.get("content") or "").strip()
    if not content:
        content = (message.get("reasoning") or "").strip()
    if not content:
        raise AIError(f"OpenRouter {model} returned an empty response")
    return content


# free models that are known to work well for short social copy, newest first
OPENROUTER_PREFERRED = [
    "inclusionai/ling-3.2-flash:free",
    "baidu/ernie-5.5-flash-preview:free",
    "particle-ai/particle-1:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "dots-studio/dots-3-note-preview:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "liquid/lfm-3-1.7b:free",
]

_free_model_cache: list[str] | None = None


def discover_free_models() -> list[str]:
    """Ask OpenRouter which models are actually free right now and support JSON."""
    global _free_model_cache
    if _free_model_cache is not None:
        return _free_model_cache
    try:
        response = requests.get("https://openrouter.ai/api/v1/models", timeout=60)
        models = response.json().get("data", [])
        found = []
        for model in models:
            model_id = model.get("id", "")
            pricing = model.get("pricing", {})
            if not model_id.endswith(":free"):
                continue
            try:
                if float(pricing.get("prompt", 1)) != 0 or float(pricing.get("completion", 1)) != 0:
                    continue
            except (TypeError, ValueError):
                continue
            params = model.get("supported_parameters", []) or []
            if "response_format" in params or "structured_outputs" in params:
                found.append((model.get("created", 0), model_id))
        found.sort(reverse=True)
        _free_model_cache = [model_id for _, model_id in found]
    except Exception:  # noqa: BLE001
        _free_model_cache = []
    return _free_model_cache


def _openrouter_model_list() -> list[str]:
    override = os.environ.get("OPENROUTER_MODEL", "").strip()
    models: list[str] = []
    if override:
        models.append(override)
    discovered = discover_free_models()
    for model in discovered:
        if model not in models:
            models.append(model)
    for model in OPENROUTER_PREFERRED:
        if model not in models:
            models.append(model)
    return models


def _call_openrouter(api_key: str, system: str, user: str, json_mode: bool) -> str:
    """Try each free model until one works. OpenRouter free models rotate often."""
    errors: list[str] = []
    for model in _openrouter_model_list():
        try:
            return _call_openrouter_once(api_key, model, system, user, json_mode)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{model}: {exc}")
    raise AIError("No OpenRouter free model worked -> " + " | ".join(errors[:3]))


def _call_custom(api_key: str, system: str, user: str, json_mode: bool) -> str:
    """Any OpenAI-compatible gateway: NaraRouter, OpenAI, Together, Groq-compatible, self hosted.

    Config:
      CUSTOM_AI_BASE_URL   e.g. https://router.bynara.id/v1
      CUSTOM_AI_API_KEY
      CUSTOM_AI_MODEL      model id, optional when the gateway exposes one free model
      CUSTOM_AI_AUTH       header name, default "Authorization"
      CUSTOM_AI_AUTH_PREFIX default "Bearer " (use empty string for api-key style)
    """
    base_url = os.environ.get("CUSTOM_AI_BASE_URL", "").rstrip("/")
    if not base_url:
        raise AIError("CUSTOM_AI_BASE_URL is not set")
    auth_header = os.environ.get("CUSTOM_AI_AUTH", "Authorization").strip() or "Authorization"
    prefix = os.environ.get("CUSTOM_AI_AUTH_PREFIX", "Bearer ")
    if prefix == "__none__":
        prefix = ""

    model = os.environ.get("CUSTOM_AI_MODEL", "").strip()
    if not model:
        model = _first_custom_model(base_url, api_key, auth_header, prefix)

    payload: dict[str, Any] = {
        "model": model,
        "messages": _messages(system, user),
        "temperature": 0.8,
        "max_tokens": 2500,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    headers[auth_header] = f"{prefix}{api_key}"

    response = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise AIError(f"custom gateway {model} returned {response.status_code}: {response.text[:250]}")
    message = response.json()["choices"][0]["message"]
    content = (message.get("content") or "").strip()
    if not content:
        content = (message.get("reasoning") or "").strip()
    if not content:
        raise AIError(f"custom gateway {model} returned an empty response")
    return content


def _custom_headers(api_key: str) -> dict[str, str]:
    auth_header = os.environ.get("CUSTOM_AI_AUTH", "Authorization").strip() or "Authorization"
    prefix = os.environ.get("CUSTOM_AI_AUTH_PREFIX", "Bearer ")
    if prefix == "__none__":
        prefix = ""
    return {auth_header: f"{prefix}{api_key}"}


def _first_custom_model(base_url: str, api_key: str, auth_header: str, prefix: str) -> str:
    """Pick a model automatically when CUSTOM_AI_MODEL is empty."""
    try:
        response = requests.get(f"{base_url}/models", headers=_custom_headers(api_key), timeout=45)
        if response.status_code == 200:
            models = response.json().get("data", [])
            ids = [m.get("id") for m in models if m.get("id")]
            # prefer free looking models, otherwise the first one
            free = [i for i in ids if "free" in i.lower() or i.endswith(":free")]
            if free:
                return free[0]
            if ids:
                return ids[0]
            raise AIError("The gateway returned no models")
        if response.status_code in (401, 403):
            raise AIError(
                f"the key was rejected by {base_url} ({response.status_code}). "
                "Check CUSTOM_AI_API_KEY, CUSTOM_AI_AUTH and CUSTOM_AI_AUTH_PREFIX."
            )
        raise AIError(f"/models returned {response.status_code}")
    except AIError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIError(f"could not reach {base_url}: {exc}") from exc


def _call_ollama(system: str, user: str, json_mode: bool) -> str:
    model = os.environ.get("OLLAMA_MODEL", "llama3.1")
    response = requests.post(
        os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
        json={
            "model": model,
            "messages": _messages(system, user),
            "stream": False,
            "format": "json" if json_mode else "",
            "options": {"temperature": 0.8},
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise AIError(f"Ollama {response.status_code}: {response.text[:300]}")
    return response.json()["message"]["content"]


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def _friendly_error(name: str, message: str) -> str:
    """Turn provider errors into one short, actionable line."""
    low = message.lower()
    if "free-models-per-day" in low or "rate limit exceeded" in low:
        return (
            f"{name}: daily free request limit reached (resets at 00:00 UTC). "
            "Add a second provider key, or add 10 credits to OpenRouter to unlock 1000/day."
        )
    if "insufficient" in low and "credit" in low:
        return f"{name}: not enough credits on the account."
    if "401" in message[:60] or "unauthorized" in low:
        return f"{name}: the API key was rejected, check it in .env."
    if "429" in message[:80]:
        return f"{name}: rate limited, try again in a minute."
    return f"{name}: {message[:160]}"


def generate_json(system: str, user: str) -> dict[str, Any]:
    """Call the first available provider, return parsed JSON."""
    providers: list[tuple[str, Any]] = []

    if os.environ.get("GEMINI_API_KEY"):
        providers.append(("gemini", lambda: _call_gemini(os.environ["GEMINI_API_KEY"], system, user, True)))
    if os.environ.get("GROQ_API_KEY"):
        providers.append(("groq", lambda: _call_groq(os.environ["GROQ_API_KEY"], system, user, True)))
    if os.environ.get("CUSTOM_AI_API_KEY") and os.environ.get("CUSTOM_AI_BASE_URL"):
        providers.append(
            ("custom", lambda: _call_custom(os.environ["CUSTOM_AI_API_KEY"], system, user, True))
        )
    if os.environ.get("OPENROUTER_API_KEY"):
        providers.append(
            ("openrouter", lambda: _call_openrouter(os.environ["OPENROUTER_API_KEY"], system, user, True))
        )

    errors: list[str] = []
    for name, call in providers:
        try:
            raw = call()
            return _extract_json(raw)
        except Exception as exc:  # noqa: BLE001
            errors.append(_friendly_error(name, str(exc)))

    try:
        raw = _call_ollama(system, user, True)
        return _extract_json(raw)
    except Exception as exc:  # noqa: BLE001
        errors.append(_friendly_error("ollama", str(exc)))

    raise AIError(" | ".join(errors))


def provider_status() -> list[str]:
    active = []
    for name, key in (
        ("gemini", "GEMINI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
    ):
        if os.environ.get(key):
            active.append(name)
    if os.environ.get("CUSTOM_AI_API_KEY") and os.environ.get("CUSTOM_AI_BASE_URL"):
        active.append("custom")
    if os.environ.get("OPENROUTER_API_KEY"):
        active.append("openrouter")
    try:
        requests.get(os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/tags", timeout=3)
        active.append("ollama")
    except Exception:  # noqa: BLE001
        pass
    return active or ["local-template"]
