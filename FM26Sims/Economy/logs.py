import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional, List, Tuple

from Squads.db import get_db, error_embed, success_embed


# ─── Colour ───────────────────────────────────────────────────────────────────
PURPLE = 0x9B59B6


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _get_log_channel_id(guild_id: int) -> Optional[int]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT channel_id FROM money_log_channels WHERE guild_id = ?",
            (str(guild_id),)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


async def log_money(
    bot: commands.Bot,
    guild: discord.Guild,
    *,
    event_type: str,
    actor: discord.Member,
    target: discord.Member,
    amount: int,
    fmt_fn,
    extra_fields: Optional[List[Tuple[str, str, bool]]] = None,
):
    """Send a purple log embed to the configured money-log channel (if any)."""
    channel_id = _get_log_channel_id(guild.id)
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        return

    titles = {
        "transfer":  "💸 Money Transfer",
        "addbal":    "➕ Balance Added",
        "removebal": "➖ Balance Removed",
    }
    descriptions = {
        "transfer":  f"{actor.mention} sent **{fmt_fn(amount)}** to {target.mention}.",
        "addbal":    f"{actor.mention} added **{fmt_fn(amount)}** to {target.mention}'s balance.",
        "removebal": f"{actor.mention} removed **{fmt_fn(amount)}** from {target.mention}'s balance.",
    }

    embed = discord.Embed(
        title=titles.get(event_type, "💰 Money Event"),
        description=descriptions.get(event_type, ""),
        colour=PURPLE,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="FM26 Sims • Money Logs")

    # Only show extra fields (new balances etc.) — no redundant Actor/Target/Amount fields
    for name, value, inline in (extra_fields or []):
        embed.add_field(name=name, value=value, inline=inline)

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


# ─── Cog ──────────────────────────────────────────────────────────────────────

class MoneyLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ensure_table()

    @staticmethod
    def _ensure_table():
        conn = get_db()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS money_log_channels (
                    guild_id   TEXT PRIMARY KEY,
                    channel_id INTEGER NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    @commands.command(name="logs")
    @commands.has_permissions(administrator=True)
    async def set_logs(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        .logs #channel  — set the channel where money logs are sent.
        Admins only.
        """
        conn = get_db()
        try:
            conn.execute(
                """
                INSERT INTO money_log_channels (guild_id, channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id
                """,
                (str(ctx.guild.id), channel.id)
            )
            conn.commit()
        finally:
            conn.close()

        embed = discord.Embed(
            title="📋 Money Logs Configured",
            description=f"All money-related logs will now be sent to {channel.mention}.",
            colour=PURPLE,
        )
        embed.set_footer(text="FM26 Sims")
        await ctx.reply(embed=embed, mention_author=False)

        test = discord.Embed(
            title="✅ Money Log Channel Active",
            description=(
                "This channel has been set as the **money log channel**.\n"
                "Transfers, balance additions, and removals will appear here."
            ),
            colour=PURPLE,
            timestamp=datetime.now(timezone.utc),
        )
        test.set_footer(text=f"Configured by {ctx.author} • FM26 Sims")
        await channel.send(embed=test)

    @set_logs.error
    async def set_logs_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.reply(
                embed=error_embed("⛔ Only **Administrators** can set money logs."),
                mention_author=False,
            )
        if isinstance(error, commands.ChannelNotFound):
            return await ctx.reply(
                embed=error_embed("❌ Channel not found. Mention a valid text channel."),
                mention_author=False,
            )
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.reply(
                embed=error_embed("**Usage:** `.logs #channel`"),
                mention_author=False,
            )
        await ctx.reply(
            embed=error_embed(f"An unexpected error occurred: `{error}`"),
            mention_author=False,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MoneyLog(bot))