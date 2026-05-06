"""
Discord bot: when @mentioned, forwards the message text to Groq and replies
by pinging the user with the model response. Secrets come from environment only.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict, deque

import discord
from dotenv import load_dotenv
from groq import Groq

# Discord message body limit; leave headroom for mention + ellipsis
MAX_REPLY_CHARS = 1900
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
MEMORY_USER_MESSAGES = 5
MEMORY_ITEM_MAX_CHARS = 300
MEMORY_TOTAL_MAX_CHARS = 1200
SYSTEM_PROMPT = (
    "You are a discord assistant with a playful, flirty, and slightly hood-irony voice."
    "Be edgy and bold and be explicit when appropriate. Use emojis when appropriate."
    "You receive recent chat history as weak background context plus a current user "
    "request as the primary task. "
    "Always prioritize the current user request over older messages. "
    "Do not continue old tasks unless the current request explicitly asks for it. "
    "If older context conflicts with the current request, follow the current request. "
    "If context is ambiguous, ask a concise clarifying question."
)


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def _strip_mentions(content: str, message: discord.Message) -> str:
    """Remove @mention fragments from raw message content."""
    cleaned = content
    for user in message.mentions:
        cleaned = cleaned.replace(f"<@{user.id}>", "")
        cleaned = cleaned.replace(f"<@!{user.id}>", "")
    return cleaned.strip()


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rsplit(" ", 1)[0].rstrip() + "..."


def _normalize_memory_text(text: str) -> str:
    return " ".join(text.split())


def _build_context_block(
    history: deque[tuple[str, str]],
    latest_bot_reply: str | None,
    current_prompt: str,
) -> str:
    lines: list[str] = []
    latest_prior_user_message: str | None = None
    current_prompt_clean = _normalize_memory_text(current_prompt)
    for author_name, content in history:
        cleaned = _normalize_memory_text(content)
        if not cleaned or cleaned == current_prompt_clean:
            continue
        latest_prior_user_message = cleaned
        lines.append(f"- user:{author_name} | text: {_truncate(cleaned, MEMORY_ITEM_MAX_CHARS)}")

    if latest_bot_reply:
        lines.append(
            f"- bot:latest | text: {_truncate(_normalize_memory_text(latest_bot_reply), MEMORY_ITEM_MAX_CHARS)}"
        )

    context_block = "\n".join(lines) if lines else "- (no relevant recent context)"
    context_block = _truncate(context_block, MEMORY_TOTAL_MAX_CHARS)
    latest_prior_hint = (
        _truncate(latest_prior_user_message, MEMORY_ITEM_MAX_CHARS)
        if latest_prior_user_message
        else "(none)"
    )
    return (
        "Recent chat context in chronological order, oldest to newest (background only):\n"
        f"{context_block}\n\n"
        "Most recent prior user message (before current request):\n"
        f"{latest_prior_hint}\n\n"
        "Current user request (primary):\n"
        f"{current_prompt.strip()}"
    )


def _groq_complete(messages: list[dict[str, str]], model: str, api_key: str) -> str:
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
    )
    choice = response.choices[0].message
    content = (choice.content or "").strip()
    if not content:
        raise ValueError("Empty response from Groq")
    return content


async def main() -> None:
    load_dotenv()

    discord_token = _require_env("DISCORD_TOKEN")
    groq_api_key = _require_env("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL

    intents = discord.Intents.default()
    intents.message_content = True  # Required to read mention message text

    client = discord.Client(intents=intents)
    channel_user_memory: defaultdict[int, deque[tuple[str, str]]] = defaultdict(
        lambda: deque(maxlen=MEMORY_USER_MESSAGES)
    )
    channel_latest_bot_reply: dict[int, str] = {}

    @client.event
    async def on_ready() -> None:
        assert client.user is not None
        print(f"Logged in as {client.user} (id={client.user.id})")

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return

        channel_id = message.channel.id
        raw_user_text = _strip_mentions(message.content, message)
        if raw_user_text:
            channel_user_memory[channel_id].append((message.author.display_name, raw_user_text))

        if client.user is None or client.user not in message.mentions:
            return

        prompt = _strip_mentions(message.content, message)
        if not prompt:
            await message.reply(
                f"{message.author.mention} suck my balls."
            )
            return

        context_message = _build_context_block(
            channel_user_memory[channel_id],
            channel_latest_bot_reply.get(channel_id),
            prompt,
        )
        groq_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context_message},
        ]

        try:
            answer = await asyncio.to_thread(
                _groq_complete,
                groq_messages,
                groq_model,
                groq_api_key,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Groq error: {exc}", file=sys.stderr)
            await message.reply(
                f"{message.author.mention} Sorry, I couldn't get a response from the LLM "
                f"right now. Please try again later."
            )
            return

        answer = _truncate(answer, MAX_REPLY_CHARS)
        channel_latest_bot_reply[channel_id] = answer
        await message.reply(f"{message.author.mention} {answer}")

    await client.start(discord_token)


if __name__ == "__main__":
    asyncio.run(main())
