"""DeepSeek chat and Kokoro speech using the same local OpenRouter key as Jev."""

import json
import urllib.error
import urllib.request

from local_secrets import get_secret

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_MODEL = get_secret("OPENROUTER_CHAT_MODEL", "deepseek/deepseek-chat")
TTS_MODEL = get_secret("OPENROUTER_TTS_MODEL", "hexgrad/kokoro-82m")
TTS_VOICE = get_secret("OPENROUTER_TTS_VOICE", "am_liam")
CHAT_TIMEOUT_SECONDS = 30
TTS_TIMEOUT_SECONDS = 30


def has_api_key():
    return bool(get_secret("OPENROUTER_API_KEY"))


def _post(endpoint, payload, timeout):
    key = get_secret("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Set OPENROUTER_API_KEY to enable Clippy responses and speech.")
    request = urllib.request.Request(
        BASE_URL + endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        # Do not log provider bodies, which may echo prompts or credentials.
        exc.close()
        raise RuntimeError("OpenRouter request failed (HTTP %d)." % exc.code) from None
    except urllib.error.URLError:
        raise RuntimeError("Could not reach OpenRouter. Check your network connection.") from None


def json_completion(prompt, schema, *, name, temperature, max_tokens):
    """Request schema-conforming JSON and reject empty/truncated/error responses."""
    raw, _ = _post("/chat/completions", {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": name, "strict": True, "schema": schema,
        }},
        "provider": {"require_parameters": True},
        "temperature": temperature,
        "max_tokens": max_tokens,
    }, CHAT_TIMEOUT_SECONDS)
    try:
        data = json.loads(raw)
        choice = data["choices"][0]
        if choice.get("finish_reason") not in (None, "stop"):
            raise ValueError("Incomplete response")
        result = json.loads(choice["message"]["content"])
        if not isinstance(result, dict):
            raise ValueError("Expected an object")
        return result
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ValueError("OpenRouter returned an invalid structured response.") from None


def spoken_audio(text):
    """Return MP3 bytes for Qt playback; no key or empty text disables speech."""
    if not has_api_key() or not text.strip():
        return b""
    audio, content_type = _post("/audio/speech", {
        "model": TTS_MODEL,
        "input": text,
        "voice": TTS_VOICE,
        "response_format": "mp3",
    }, TTS_TIMEOUT_SECONDS)
    if not audio or content_type.split(";", 1)[0].strip().lower() != "audio/mpeg":
        raise RuntimeError("OpenRouter returned no MP3 audio.")
    return audio
