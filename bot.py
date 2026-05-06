"""
Discord bot: when @mentioned, forwards the message text to Groq and replies
by pinging the user with the model response. Secrets come from environment only.
"""

from __future__ import annotations

import asyncio
import os
import sys

import discord
from dotenv import load_dotenv
from groq import Groq

# Discord message body limit; leave headroom for mention + ellipsis
MAX_REPLY_CHARS = 1900
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"


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


def _groq_complete(prompt: str, model: str, api_key: str) -> str:
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
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

    @client.event
    async def on_ready() -> None:
        assert client.user is not None
        print(f"Logged in as {client.user} (id={client.user.id})")

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        if client.user is None or client.user not in message.mentions:
            return

        prompt = _strip_mentions(message.content, message)
        if not prompt:
            await message.reply(
                f"{message.author.mention} Please include your question after mentioning me."
            )
            return

        try:
            answer = await asyncio.to_thread(
                _groq_complete,
                prompt,
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
        await message.reply(f"{message.author.mention} {answer}")

    await client.start(discord_token)


if __name__ == "__main__":
    asyncio.run(main())
