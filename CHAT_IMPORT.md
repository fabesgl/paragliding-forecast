# Chat-log importer

Turns WhatsApp and Telegram chat exports into weak "pilot report" labels. Runs on a local model only (it refuses any non-local address).
Meant to be run on the home desktop: nothing is installed on the laptop.

## Setup on the desktop
1. `git clone` this repo, `python3 -m venv .venv`, `.venv/bin/pip install -r requirements.txt` (only `requests` and `pyyaml` are needed for the importer).
2. Install Ollama (https://ollama.com) and pull a model. `qwen2.5:7b-instruct` (~4.7 GB) is a sensible start. For Spanish and especially Catalan, try a trial with it first and compare against a larger multilingual model if the labels look wrong.
3. Export chats: WhatsApp > Export chat > Without media (.txt). Telegram Desktop > Export chat history > Format: JSON. Put them in `chat_exports/` (git-ignored).

## Optional: use the Claude API instead of a local model
Install `pip install -r requirements-chat-api.txt` and set `ANTHROPIC_API_KEY` in your shell profile (never in a file in this repo). Then add `--backend anthropic` (model `claude-haiku-5-5`, $0.10 per million input tokens, $0.50 per million output tokens).
The same scrubbing of links, @mentions, phone numbers and emails is applied first. The scrubbed message text and up to 2 earlier messages ARE sent to Anthropic; a name written inside a message is not removed.
- Messages go in batches of 20 per call (`--batch-size`), 4 calls at once (`--parallel`). Only messages that mention flying or the site are sent unless you add `--no-prefilter`.
- Count and estimate first, sending nothing: add `--dry-run`. Representative trial: `--sample 30` (30 messages spread evenly across the whole file).
- Stop any time with Ctrl-C (running batches finish and are saved). Run the SAME command again to continue: finished messages are skipped.
- It prints tokens and estimated cost as it goes, and stops at `--max-usd` (default 2.00).

## Run
- Trial (30 messages): `.venv/bin/python scripts/import_chats.py chat_exports/bright.txt --default-site bright --limit 30`
- Full: `.venv/bin/python scripts/import_chats.py chat_exports/*.txt chat_exports/*.json --default-site bright`
- Count only (no model, nothing stored): add `--dry-run`. Re-running skips messages already done.
- `--chat NAME` (one file): a name for the chat, so the same people keep the same anonymous ids when you re-export it under a different file name. Without it the file name is used.

## Languages
English, Spanish and Catalan chats work, including Spanish/Catalan WhatsApp layouts ("2:03 p. m.", two-digit years, "<Multimedia omitido>", "<Multimèdia omès>"). Accents are ignored when matching place names, so "Organyà", "organya" and "ORGANYÀ" are the same. For Organyà later: add `sites/organya.yaml` with `id`, `name`, `timezone` and `chat_aliases: [organyà, organya]`, then run with `--default-site organya`.

## What is stored (`data/chat_labels.sqlite`, never pushed)
Site, date, flown yes/no, wind speed and direction, turbulence, thermals, restitution, rating 1-5, the model's own confidence, and the marks `evidence=chat_llm`, `confidence_tier=low`, `weight=0.3` (flight tracks get 1.0).
Senders are stored only as a salted hash per chat (`sender_hash`, with `chat_hash`), so repeat reports from one person can be grouped. The view `chat_label_weighted` splits one person's weight across their reports for the same site and day (`effective_weight`).
**Never stored:** message text, names, phone numbers. The salt lives in `data/chat_salt`. Keep it: without it ids change on the next import, and anyone holding it could test a guessed name against the ids. These hashes are still pseudonymous personal data (relevant under EU/Spanish law), so keep the database private.

Tests (synthetic chats, fake model): `.venv/bin/python -m unittest tests.test_chatlog`.
