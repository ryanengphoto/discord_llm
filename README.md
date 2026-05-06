# Discord mention → Groq bot

When someone **@mentions** the bot, the message (minus the mention) is sent to [Groq](https://console.groq.com/), and the bot replies in the channel **pinging the user** with the model output.

## Setup

### 1. Python environment

```bash
cd /path/to/discord_llm
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment variables

Copy or edit `.env` in this directory (never commit real tokens). Required:

| Variable         | Description                    |
|-----------------|--------------------------------|
| `DISCORD_TOKEN` | Bot token from Discord         |
| `GROQ_API_KEY`  | API key from Groq Console      |
| `GROQ_MODEL`    | Optional; default in code is `llama-3.1-8b-instant` |

You can omit `GROQ_MODEL` if you rely on the built-in default.

### 3. Discord Developer Portal (bot + intents)

1. [Discord Developer Portal](https://discord.com/developers/applications) → your application → **Bot**.
2. Under **Privileged Gateway Intents**, enable **Message Content Intent** (required so the bot can read message text when mentioned).
3. Reset or copy the bot token into `DISCORD_TOKEN` in `.env`.
4. **OAuth2 → URL Generator** (or use the invite URL below):
   - Scopes: `bot`
   - Bot permissions: at least **Read Messages/View Channels**, **Send Messages**, **Read Message History** (and **Mention Everyone** is *not* required for normal @ replies).

Example invite (replace `CLIENT_ID`):

`https://discord.com/api/oauth2/authorize?client_id=CLIENT_ID&permissions=277025508416&scope=bot`

(Permission integer `277025508416` = View Channel, Send Messages, Read Message History, Embed Links; adjust as needed.)

### 4. Groq

Create an API key in the Groq console and set `GROQ_API_KEY` in `.env`.

## Run

```bash
source .venv/bin/activate
python bot.py
```

You should see: `Logged in as YourBot#1234 (id=...)`.

## Verify

1. In a server channel where the bot can read/send, send: `@YourBot hello`.
2. Expect a reply that **mentions you** and includes the LLM answer.
3. Send `@YourBot` with no text; expect a short prompt to add a question.
4. (Optional) Temporarily set a wrong `GROQ_API_KEY` and confirm a polite error reply without crashing the process.

## Files

- `bot.py` — Discord + Groq logic
- `requirements.txt` — Python dependencies
- `.env` — local secrets (listed in `.gitignore`)
