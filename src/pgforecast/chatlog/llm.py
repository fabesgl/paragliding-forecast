"""Talk to a LOCAL language model (Ollama, or any OpenAI-compatible local server such as llama.cpp / LM Studio).
Refuses any non-local address so chat text can never be sent over the internet."""
import json, requests
from urllib.parse import urlparse

LOCAL = {"localhost", "127.0.0.1", "::1", "[::1]"}

class LocalLLM:
    def __init__(self, model, backend="ollama", base_url=None, timeout=180):
        self.model, self.backend, self.timeout = model, backend, timeout
        self.base_url = (base_url or ("http://localhost:11434" if backend == "ollama" else "http://localhost:8080/v1")).rstrip("/")
        host = urlparse(self.base_url).hostname
        if host not in LOCAL:
            raise SystemExit(f"Refusing {self.base_url}: the chat importer only talks to a model on this machine.")

    def complete(self, system, user, schema):
        """Return the model's JSON reply as text."""
        if self.backend == "ollama":
            r = requests.post(f"{self.base_url}/api/chat", timeout=self.timeout, json=dict(
                model=self.model, stream=False, format=schema, options=dict(temperature=0, num_ctx=4096),
                messages=[dict(role="system", content=system), dict(role="user", content=user)]))
            r.raise_for_status(); return r.json()["message"]["content"]
        r = requests.post(f"{self.base_url}/chat/completions", timeout=self.timeout, json=dict(
            model=self.model, temperature=0, response_format=dict(type="json_object"),
            messages=[dict(role="system", content=system), dict(role="user", content=user)]))
        r.raise_for_status(); return r.json()["choices"][0]["message"]["content"]
