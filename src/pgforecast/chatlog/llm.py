"""Talk to a LOCAL language model (Ollama, or any OpenAI-compatible local server such as llama.cpp / LM Studio).
Refuses any non-local address so chat text can never be sent over the internet."""
import json, os, requests
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


# ---- Claude API (optional, sends the SCRUBBED message text to Anthropic) --------------------------------------------------------------------
PRICES = {"claude-haiku-5-5": (0.10, 0.50)}      # USD per 1M input / output tokens, for prompts up to 100K tokens (Anthropic price list, Nov 2026)

class AnthropicLLM:
    """Same interface as LocalLLM, but calls the Claude API. The key comes ONLY from the ANTHROPIC_API_KEY environment variable
    (never a file in the repo, never a command-line argument). Keeps a running token count and cost, and refuses to go past max_usd."""
    def __init__(self, model="claude-haiku-5-5", max_usd=2.0, client=None, backend="anthropic"):
        self.model, self.max_usd, self.backend = model, max_usd, backend
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        if model not in PRICES: raise SystemExit(f"No price known for {model}; add it to PRICES in llm.py so the cost can be tracked.")
        if client is None:
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key: raise SystemExit("ANTHROPIC_API_KEY is not set. Set it in your shell or profile (not in a file inside this repo), then run again.")
            import anthropic                                   # imported here so local-model users never need the package
            client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=60.0)
        self.client = client

    def cost(self):
        pin, pout = PRICES[self.model]
        return (self.usage["input_tokens"] * pin + self.usage["output_tokens"] * pout) / 1e6

    def over_budget(self): return self.cost() >= self.max_usd

    def report(self):
        u = self.usage
        return f"Claude API ({self.model}): {u['calls']} calls, {u['input_tokens']:,} input + {u['output_tokens']:,} output tokens, estimated cost ${self.cost():.4f} (cap ${self.max_usd:.2f})"

    def complete(self, system, user, schema):
        """Return the model's JSON reply as text. Uses a forced tool call so the reply always matches the label schema."""
        tool = {"name": "label_message", "description": "Record the labels for the last chat message.", "input_schema": schema}
        try:
            r = self.client.messages.create(model=self.model, max_tokens=600, system=system, tools=[tool], tool_choice={"type": "tool", "name": "label_message"},
                                            output_config={"effort": "low"}, messages=[{"role": "user", "content": user}])
        except Exception as e:
            if type(e).__name__ == "AuthenticationError": raise SystemExit("The Claude API rejected ANTHROPIC_API_KEY (check the key).")
            raise
        self.usage["calls"] += 1; self.usage["input_tokens"] += r.usage.input_tokens; self.usage["output_tokens"] += r.usage.output_tokens
        for b in r.content:
            if b.type == "tool_use": return json.dumps(b.input)
        return None
