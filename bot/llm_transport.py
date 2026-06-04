import httpx
from config import LLM_API, OLLAMA_URL

_TIMEOUT = 60.0


def _openai_content(data: dict) -> str:
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise KeyError("choices[0].message.content is not a string")
    return content


async def complete_json(system: str, user: str, *, model: str) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if LLM_API == "openai":
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "response_format": {"type": "json_object"},
            }
            resp = await client.post(f"{OLLAMA_URL}/chat/completions", json=payload)
            resp.raise_for_status()
            return _openai_content(resp.json())

        payload = {
            "model": model,
            "system": system,
            "prompt": user,
            "stream": False,
            "format": "json",
        }
        resp = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
        resp.raise_for_status()
        raw = resp.json()["response"]
        if not isinstance(raw, str):
            raise KeyError("response is not a string")
        return raw


async def complete_chat(
    messages: list[dict],
    *,
    model: str,
    max_tokens: int,
    stop: list[str],
) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if LLM_API == "openai":
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "max_tokens": max_tokens,
                "stop": stop,
            }
            resp = await client.post(f"{OLLAMA_URL}/chat/completions", json=payload)
            resp.raise_for_status()
            return _openai_content(resp.json())

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "stop": stop,
            },
        }
        resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        if not isinstance(content, str):
            raise KeyError("message.content is not a string")
        return content
