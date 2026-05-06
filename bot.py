"""
Discord bot: when @mentioned, forwards the message text to Groq and replies
by pinging the user with the model response. Secrets come from environment only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv
from groq import Groq

# Discord message body limit; leave headroom for mention + ellipsis
MAX_REPLY_CHARS = 1900
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
DEFAULT_COMMAND_SYNC_WAIT_SECONDS = 3600
MEMORY_USER_MESSAGES = 5
MEMORY_ITEM_MAX_CHARS = 300
MEMORY_TOTAL_MAX_CHARS = 1200
PROMPT_CONFIG_PATH = Path(__file__).with_name("prompt_config.json")
PROMPT_COMMAND_CHANNEL = "bot-commands"

DEFAULT_PERSONALITY_PROMPT = (
    "You are a discord assistant with a playful, flirty, and slightly hood-irony voice."
    "Be edgy and bold and be explicit when appropriate. Use emojis when appropriate."
)
BASELINE_SYSTEM_PROMPT = (
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


def _compose_system_prompt(personality_prompt: str) -> str:
    personality = personality_prompt.strip()
    if not personality:
        personality = DEFAULT_PERSONALITY_PROMPT
    return f"{personality}\n\n{BASELINE_SYSTEM_PROMPT}"


def _load_personality_prompt() -> str:
    if not PROMPT_CONFIG_PATH.exists():
        return DEFAULT_PERSONALITY_PROMPT
    try:
        data = json.loads(PROMPT_CONFIG_PATH.read_text(encoding="utf-8"))
        loaded = str(data.get("personality_prompt", "")).strip()
        return loaded or DEFAULT_PERSONALITY_PROMPT
    except Exception as exc:  # noqa: BLE001
        print(f"Prompt config read error: {exc}", file=sys.stderr)
        return DEFAULT_PERSONALITY_PROMPT


def _save_personality_prompt(personality_prompt: str) -> None:
    payload = {"personality_prompt": personality_prompt.strip()}
    PROMPT_CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


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
    discord_guild_id_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    discord_guild_id = int(discord_guild_id_raw) if discord_guild_id_raw.isdigit() else None

    intents = discord.Intents.default()
    intents.message_content = True  # Required to read mention message text

    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    channel_user_memory: defaultdict[int, deque[tuple[str, str]]] = defaultdict(
        lambda: deque(maxlen=MEMORY_USER_MESSAGES)
    )
    channel_latest_bot_reply: dict[int, str] = {}
    personality_prompt = _load_personality_prompt()
    has_synced_commands = False

    def _is_prompt_command_channel(channel: discord.abc.GuildChannel | None) -> bool:
        return channel is not None and getattr(channel, "name", "") == PROMPT_COMMAND_CHANNEL

    @client.event
    async def on_ready() -> None:
        nonlocal has_synced_commands
        assert client.user is not None
        if not has_synced_commands:
            if discord_guild_id is not None:
                guild = discord.Object(id=discord_guild_id)
                # Clear any stale global commands so Discord doesn't show duplicates
                # when we intentionally run prompt commands as guild-scoped.
                tree.clear_commands(guild=None)
                await tree.sync()
                await tree.sync(guild=guild)
                print(
                    "Slash commands synced to guild "
                    f"{discord_guild_id} (instant command visibility)."
                )
            else:
                await tree.sync()
                print(
                    "Slash commands synced globally. They may take up to "
                    f"{DEFAULT_COMMAND_SYNC_WAIT_SECONDS // 60} minutes to appear."
                )
            has_synced_commands = True
        print(f"Logged in as {client.user} (id={client.user.id})")

    @app_commands.guild_only()
    class PromptCommandGroup(app_commands.Group):
        def __init__(self) -> None:
            super().__init__(name="prompt", description="Manage bot personality prompt")

        @app_commands.command(name="show", description="Show current personality and active system prompt")
        async def show(self, interaction: discord.Interaction) -> None:
            if not _is_prompt_command_channel(interaction.channel):
                await interaction.response.send_message(
                    f"This command can only be used in `#{PROMPT_COMMAND_CHANNEL}`.",
                    ephemeral=True,
                )
                return
            system_prompt = _compose_system_prompt(personality_prompt)
            await interaction.response.send_message(
                "Current tunable personality prompt:\n"
                f"```{personality_prompt}```\n"
                "Fixed baseline (always appended):\n"
                f"```{BASELINE_SYSTEM_PROMPT}```\n"
                "Active system prompt sent to the model:\n"
                f"```{system_prompt}```",
                ephemeral=True,
            )

        @app_commands.command(name="set", description="Set tunable personality prompt")
        async def set(self, interaction: discord.Interaction, text: str) -> None:
            nonlocal personality_prompt
            if not _is_prompt_command_channel(interaction.channel):
                await interaction.response.send_message(
                    f"This command can only be used in `#{PROMPT_COMMAND_CHANNEL}`.",
                    ephemeral=True,
                )
                return
            new_personality = text.strip()
            if not new_personality:
                await interaction.response.send_message(
                    "Please provide a non-empty personality prompt.",
                    ephemeral=True,
                )
                return
            personality_prompt = new_personality
            try:
                _save_personality_prompt(personality_prompt)
            except Exception as exc:  # noqa: BLE001
                print(f"Prompt config write error: {exc}", file=sys.stderr)
                await interaction.response.send_message(
                    "Failed to save prompt config.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                "Updated personality prompt. Baseline remains enforced.",
                ephemeral=True,
            )

        @app_commands.command(name="reset", description="Reset personality prompt to default")
        async def reset(self, interaction: discord.Interaction) -> None:
            nonlocal personality_prompt
            if not _is_prompt_command_channel(interaction.channel):
                await interaction.response.send_message(
                    f"This command can only be used in `#{PROMPT_COMMAND_CHANNEL}`.",
                    ephemeral=True,
                )
                return
            personality_prompt = DEFAULT_PERSONALITY_PROMPT
            try:
                _save_personality_prompt(personality_prompt)
            except Exception as exc:  # noqa: BLE001
                print(f"Prompt config write error: {exc}", file=sys.stderr)
                await interaction.response.send_message(
                    "Failed to save prompt config.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                "Reset personality prompt to default.",
                ephemeral=True,
            )

    prompt_group = PromptCommandGroup()
    if discord_guild_id is not None:
        tree.add_command(prompt_group, guild=discord.Object(id=discord_guild_id))
    else:
        tree.add_command(prompt_group)

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
            {"role": "system", "content": _compose_system_prompt(personality_prompt)},
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
