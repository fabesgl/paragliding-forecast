# Chat-log importer

Turns WhatsApp and Telegram chat exports into weak "pilot report" labels. Needs a local model (nothing is sent over the internet).

1. One-time: install Ollama (https://ollama.com), then `ollama pull qwen2.5:7b-instruct` (about 4.7 GB; fits an 8 GB GPU).
2. Export the chat: WhatsApp > Export chat > Without media (.txt). Telegram Desktop > Export chat history > Format: JSON.
3. Put exports in `chat_exports/` (git-ignored), then:
   - Trial: `.venv/bin/python scripts/import_chats.py chat_exports/bright.txt --default-site bright --limit 30`
   - Full:  `.venv/bin/python scripts/import_chats.py chat_exports/*.txt chat_exports/*.json --default-site bright`
   - Count only (no model): add `--dry-run`. Re-running skips messages already done.

What is stored (`data/chat_labels.sqlite`, never pushed): site, date, flown yes/no, wind speed and direction, turbulence, thermals, restitution, rating 1-5, the model's own confidence, and the marks `evidence=chat_llm`, `confidence_tier=low`, `weight=0.3` (flight tracks get 1.0).
Never stored: message text, names, phone numbers. The only per-message fingerprint is a salted hash (`data/chat_salt`, local) used to skip messages already imported.
Tests (synthetic chats, fake model): `.venv/bin/python -m unittest tests.test_chatlog`.
