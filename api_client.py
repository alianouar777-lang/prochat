"""Minimal OpenAI Chat Completions client. Standard library only."""
import json
import urllib.error
import urllib.request

API_BASE = "https://api.openai.com/v1"
USER_AGENT = "ProChat/1.0"


class ApiError(Exception):
    """Error with a message that is safe and useful to show to the user."""


def _explain(status, code, message):
    if status == 401:
        return ("Invalid API key (401). Create a new key at platform.openai.com "
                "and paste it in Settings.")
    if status == 429 and code == "insufficient_quota":
        return ("No API credit left (insufficient_quota). Add credit under Billing "
                "on platform.openai.com. A ChatGPT Pro subscription does not include API credit.")
    if status == 429:
        return "Rate limit reached (429). Wait a moment and try again."
    if status == 404:
        return f"Model not available for this key (404): {message}"
    if status and status >= 500:
        return f"OpenAI server problem ({status}). Try again later."
    return f"API error ({status}): {message}"


def _http_error(exc):
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        err = payload.get("error") or {}
        return ApiError(_explain(exc.code, err.get("code"), err.get("message", "")))
    except Exception:
        return ApiError(_explain(exc.code, None, getattr(exc, "reason", "")))


def _open(path, key, body=None, timeout=120):
    if not key or not key.isascii():
        raise ApiError("The API key is empty or contains invalid characters.")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API_BASE + path,
        data=data,
        method="POST" if data is not None else "GET",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ApiError(f"Cannot reach OpenAI. Check your internet connection. ({reason})") from None


_EXCLUDE = ("instruct", "embedding", "audio", "realtime", "tts", "whisper",
            "transcribe", "image", "search", "moderation", "dall")


def list_models(key):
    """Return chat-capable model ids for this key. Also works as a key check."""
    with _open("/models", key, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ids = {m.get("id", "") for m in data.get("data", [])}
    keep = [i for i in ids
            if i.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4"))
            and not any(x in i for x in _EXCLUDE)]
    return sorted(keep)


def stream_chat(key, model, messages, stop=None, timeout=120):
    """Yield text chunks of the assistant reply. `stop` is a threading.Event."""
    body = {"model": model, "messages": messages, "stream": True}
    with _open("/chat/completions", key, body, timeout) as resp:
        while True:
            if stop is not None and stop.is_set():
                break
            try:
                raw = resp.readline()
            except (TimeoutError, OSError) as exc:
                raise ApiError(f"Connection lost: {exc}") from None
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("error"):
                err = obj["error"]
                raise ApiError(err.get("message", "Unknown streaming error"))
            choices = obj.get("choices") or []
            if choices:
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
