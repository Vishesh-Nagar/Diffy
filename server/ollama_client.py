"""
Diffy — Ollama Client
Minimal HTTP client for Ollama's local API using only urllib.
Supports streaming generation, model listing, and health checks.
"""

import json
import urllib.request
import urllib.error

import config as cfg


class OllamaClient:
    """HTTP client for the local Ollama API."""

    def __init__(self, base_url=None):
        self.base_url = (base_url or cfg.get("ollama_url", "http://localhost:11434")).rstrip("/")

    # ----- Health -----

    def is_available(self):
        """Check if Ollama is running."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def list_models(self):
        """List available local models."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = []
                for m in data.get("models", []):
                    models.append({
                        "name": m.get("name", ""),
                        "size": m.get("size", 0),
                        "modified": m.get("modified_at", ""),
                    })
                return models
        except (urllib.error.URLError, OSError):
            return []

    # ----- Generation -----

    def generate(self, prompt, model=None, system=None, stream=False):
        """
        Generate text using Ollama.
        If stream=False, returns the complete response string.
        If stream=True, yields chunks as they arrive.
        """
        model = model or cfg.get("model", "codellama")

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
        }
        if system:
            payload["system"] = system

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        if stream:
            return self._stream_response(req)
        else:
            return self._blocking_response(req)

    def _blocking_response(self, req):
        """Get complete response (non-streaming)."""
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                full_text = ""
                for line in resp:
                    line = line.decode("utf-8").strip()
                    if line:
                        try:
                            chunk = json.loads(line)
                            full_text += chunk.get("response", "")
                            if chunk.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
                return full_text
        except (urllib.error.URLError, OSError) as e:
            return f"[Ollama error: {e}]"

    def _stream_response(self, req):
        """Yield response chunks as they stream in."""
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            for line in resp:
                line = line.decode("utf-8").strip()
                if line:
                    try:
                        chunk = json.loads(line)
                        text = chunk.get("response", "")
                        if text:
                            yield text
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
            resp.close()
        except (urllib.error.URLError, OSError) as e:
            yield f"[Ollama error: {e}]"

    # ----- Chat API -----

    def chat(self, messages, model=None, stream=False):
        """
        Chat completion format.
        messages: [{role: "user"|"assistant"|"system", content: "..."}]
        """
        model = model or cfg.get("model", "codellama")

        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        if stream:
            return self._stream_chat_response(req)
        else:
            return self._blocking_chat_response(req)

    def _blocking_chat_response(self, req):
        """Get complete chat response."""
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                full_text = ""
                for line in resp:
                    line = line.decode("utf-8").strip()
                    if line:
                        try:
                            chunk = json.loads(line)
                            msg = chunk.get("message", {})
                            full_text += msg.get("content", "")
                            if chunk.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
                return full_text
        except (urllib.error.URLError, OSError) as e:
            return f"[Ollama error: {e}]"

    def _stream_chat_response(self, req):
        """Yield chat response chunks."""
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            for line in resp:
                line = line.decode("utf-8").strip()
                if line:
                    try:
                        chunk = json.loads(line)
                        msg = chunk.get("message", {})
                        text = msg.get("content", "")
                        if text:
                            yield text
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
            resp.close()
        except (urllib.error.URLError, OSError) as e:
            yield f"[Ollama error: {e}]"

    # ----- Embeddings -----

    def embeddings(self, prompt, model=None):
        """Get embeddings for a given text."""
        model = model or cfg.get("embed_model", "nomic-embed-text")
        payload = {
            "model": model,
            "prompt": prompt,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/embeddings",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("embedding", [])
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    client = OllamaClient()
    print(f"Ollama available: {client.is_available()}")

    models = client.list_models()
    if models:
        print("Models:")
        for m in models:
            print(f"  {m['name']}")
    else:
        print("No models found (is Ollama running?)")

    if client.is_available() and models:
        print("\nTest generation:")
        result = client.generate("Say hello in Python code", stream=False)
        print(f"  {result[:200]}")
