"""
Diffy — Unified LLM Client
Checks for cloud LLM API keys (OpenAI, Anthropic, Google Gemini).
If a key is found, routes requests to that provider's API.
Otherwise, falls back to the local Ollama instance.

Provider priority (first available key wins):
  1. OpenAI      (DIFFY_OPENAI_API_KEY)
  2. Anthropic   (DIFFY_ANTHROPIC_API_KEY)
  3. Gemini      (DIFFY_GEMINI_API_KEY)
  4. Ollama      (local, no key required)
"""

import json
import urllib.request
import urllib.error

import config as cfg
import ollama_client as ollama


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_json(url, payload, headers, timeout=120):
    """POST JSON and return the parsed response dict."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Cloud provider implementations
# ---------------------------------------------------------------------------

class _OpenAIClient:
    """Minimal OpenAI-compatible chat completions client."""

    def __init__(self, api_key, model=None):
        self.api_key = api_key
        self.model = model or cfg.get("openai_model", "gpt-4o-mini")
        self.base_url = cfg.get("openai_base_url", "https://api.openai.com/v1")
        self.provider_name = "OpenAI"

    def is_available(self):
        return bool(self.api_key)

    def list_models(self):
        try:
            req = urllib.request.Request(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [
                    {"name": m["id"], "size": 0, "modified": ""}
                    for m in data.get("data", [])
                ]
        except (urllib.error.URLError, OSError):
            return []

    def generate(self, prompt, model=None, system=None, stream=False):
        """Generate using the OpenAI chat completions API (non-streaming only for simplicity)."""
        model = model or self.model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": model, "messages": messages, "stream": False}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            result = _post_json(f"{self.base_url}/chat/completions", payload, headers)
            text = result["choices"][0]["message"]["content"]
            if stream:
                # Emulate streaming by yielding the whole thing at once
                def _gen():
                    yield text
                return _gen()
            return text
        except (urllib.error.URLError, OSError, KeyError) as e:
            err = f"[OpenAI error: {e}]"
            if stream:
                def _gen():
                    yield err
                return _gen()
            return err


class _AnthropicClient:
    """Minimal Anthropic messages API client."""

    def __init__(self, api_key, model=None):
        self.api_key = api_key
        self.model = model or cfg.get("anthropic_model", "claude-sonnet-4-20250514")
        self.provider_name = "Anthropic"

    def is_available(self):
        return bool(self.api_key)

    def list_models(self):
        # Anthropic doesn't expose a public model-list endpoint
        return [{"name": self.model, "size": 0, "modified": ""}]

    def generate(self, prompt, model=None, system=None, stream=False):
        model = model or self.model
        messages = [{"role": "user", "content": prompt}]

        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            payload["system"] = system

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        try:
            result = _post_json("https://api.anthropic.com/v1/messages", payload, headers)
            # Response: { "content": [{"type":"text","text":"..."}] }
            text = result["content"][0]["text"]
            if stream:
                def _gen():
                    yield text
                return _gen()
            return text
        except (urllib.error.URLError, OSError, KeyError, IndexError) as e:
            err = f"[Anthropic error: {e}]"
            if stream:
                def _gen():
                    yield err
                return _gen()
            return err


class _GeminiClient:
    """Minimal Google Gemini (generativelanguage) client."""

    def __init__(self, api_key, model=None):
        self.api_key = api_key
        self.model = model or cfg.get("gemini_model", "gemini-2.0-flash")
        self.provider_name = "Gemini"

    def is_available(self):
        return bool(self.api_key)

    def list_models(self):
        return [{"name": self.model, "size": 0, "modified": ""}]

    def generate(self, prompt, model=None, system=None, stream=False):
        model = model or self.model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent?key={self.api_key}"
        )

        contents = [{"parts": [{"text": prompt}]}]
        payload = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        headers = {"Content-Type": "application/json"}

        try:
            result = _post_json(url, payload, headers)
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            if stream:
                def _gen():
                    yield text
                return _gen()
            return text
        except (urllib.error.URLError, OSError, KeyError, IndexError) as e:
            err = f"[Gemini error: {e}]"
            if stream:
                def _gen():
                    yield err
                return _gen()
            return err


# ---------------------------------------------------------------------------
# Unified LLM Client — the public API
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Unified LLM client that auto-selects the provider based on available API keys.

    Attributes:
        provider_name (str): Name of the active provider ('OpenAI', 'Anthropic',
                             'Gemini', or 'Ollama').
    """

    def __init__(self):
        self._client = None
        self.provider_name = "Ollama"
        self._resolve_provider()

    def _resolve_provider(self):
        """Pick the first provider whose API key is set."""
        openai_key = cfg.get("openai_api_key", "")
        anthropic_key = cfg.get("anthropic_api_key", "")
        gemini_key = cfg.get("gemini_api_key", "")

        if openai_key:
            self._client = _OpenAIClient(openai_key)
            self.provider_name = self._client.provider_name
        elif anthropic_key:
            self._client = _AnthropicClient(anthropic_key)
            self.provider_name = self._client.provider_name
        elif gemini_key:
            self._client = _GeminiClient(gemini_key)
            self.provider_name = self._client.provider_name
        else:
            self._client = ollama.OllamaClient()
            self.provider_name = "Ollama"

    # ---- Public interface (matches OllamaClient) ----

    def is_available(self):
        """Check if the active LLM provider is reachable / configured."""
        return self._client.is_available()

    def list_models(self):
        """List models available from the active provider."""
        return self._client.list_models()

    def generate(self, prompt, model=None, system=None, stream=False):
        """Generate text using the active LLM provider."""
        return self._client.generate(prompt, model=model, system=system, stream=stream)

    def info(self):
        """Return a dict describing the active provider."""
        return {
            "provider": self.provider_name,
            "model": getattr(self._client, "model", cfg.get("model", "codellama")),
        }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    client = LLMClient()
    info = client.info()
    print(f"Active provider : {info['provider']}")
    print(f"Model           : {info['model']}")
    print(f"Available       : {client.is_available()}")

    models = client.list_models()
    if models:
        print("Models:")
        for m in models:
            print(f"  {m['name']}")

    if client.is_available():
        print("\nTest generation:")
        result = client.generate("Say hello in Python code", stream=False)
        print(f"  {result[:200]}")
