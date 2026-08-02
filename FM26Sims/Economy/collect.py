import discord
from discord.ext import commands
from datetime import datetime, timezone

from Squads.db import get_db, error_embed, warn_embed
from Economy.balutils import fmt
from Economy.logs import log_money

LEAGUE_ROLES = {
    1479796732320813138: ("Bundesliga",     1_500_000),
    1479796807243665511: ("Ligue 1",          800_000),
    1479796510374760520: ("LaLiga",         2_000_000),
    1479796605887451331: ("Serie A",        1_000_000),
    1479796383518294198: ("Premier League", 3_000_000),
}

COOLDOWN_SECONDS = 5 * 3600


def _ensure_table():
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS collect_cooldowns (
                user_id      TEXT PRIMARY KEY,
                last_collect INTEGER NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _get_last_collect(user_id: str):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT last_collect FROM collect_cooldowns WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_last_collect(user_id: str, ts: int):
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO collect_cooldowns (user_id, last_collect) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_collect = excluded.last_collect
            """,
            (user_id, ts)
        )
        conn.commit()
    finally:
        conn.close()


def _add_balance(user_id: str, amount: int) -> int:
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO balances (user_id, amount) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET amount = amount + excluded.amount
            """,
            (user_id, amount)
        )
        conn.commit()
        return conn.execute(
            "SELECT amount FROM balances WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    finally:
        conn.close()


class Collect(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        _ensure_table()

    @commands.command(name="collect", aliases=["claim", "salary", "collectmoney"])
    async def collect(self, ctx: commands.Context):
        member_role_ids = {r.id for r in ctx.author.roles}
        matched = [
            (role_id, name, payout)
            for role_id, (name, payout) in LEAGUE_ROLES.items()
            if role_id in member_role_ids
        ]

        if not matched:
            return await ctx.reply(
                embed=warn_embed("⚠️ You don't have any income role to collect."),
                mention_author=False
            )

        user_id = str(ctx.author.id)
        now_ts  = int(datetime.now(timezone.utc).timestamp())
        last    = _get_last_collect(user_id)

        if last is not None:
            remaining = COOLDOWN_SECONDS - (now_ts - last)
            if remaining > 0:
                next_ts = last + COOLDOWN_SECONDS
                return await ctx.reply(
                    embed=warn_embed(f"⏳ Next collect available <t:{next_ts}:R>"),
                    mention_author=False
                )

        total_payout = sum(p for _, _, p in matched)
        new_bal      = _add_balance(user_id, total_payout)
        _set_last_collect(user_id, now_ts)
        next_ts = now_ts + COOLDOWN_SECONDS

        # Simple breakdown: <@&role_id> - amount
        breakdown = "\n".join(
            f"<@&{role_id}> - {fmt(payout)}"
            for role_id, _, payout in sorted(matched, key=lambda x: -x[2])
        )

        # Fetch club logo
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT logo_url FROM squad_meta WHERE user_id = ?", (user_id,)
            ).fetchone()
            logo_url = row[0] if row else None
        finally:
            conn.close()

        embed = discord.Embed(
            title="💰 Salary Collected!",
            description=breakdown,
            colour=0x2ECC71,
        )
        embed.set_footer(text="FM26 Sims")
        if logo_url:
            embed.set_thumbnail(url=logo_url)

        await ctx.reply(embed=embed, mention_author=False)

        await log_money(
            self.bot, ctx.guild,
            event_type="addbal",
            actor=ctx.author, target=ctx.author,
            amount=total_payout, fmt_fn=fmt,
            extra_fields=[
                ("Source",      "`.collect`",   True),
                ("New Balance", fmt(new_bal),   True),
            ],
        )

    @collect.error
    async def collect_error(self, ctx: commands.Context, error):
        await ctx.reply(
            embed=error_embed(f"An unexpected error occurred: `{error}`"),
            mention_author=False
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Collect(bot))