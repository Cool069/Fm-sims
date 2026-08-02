import discord
from discord.ext import commands

from Squads.db import get_db, error_embed, success_embed, warn_embed
from Economy.balutils import parse_amount, fmt
from Economy.logs import log_money


class RemoveBal(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="removebal", aliases=["remove", "rm", "deduct", "sub", "removemoney"])
    @commands.has_permissions(administrator=True)
    async def removebal(self, ctx: commands.Context, member: discord.Member, *, raw_amount: str):
        """
        .removebal @user <amount>
        .removemoney @user <amount>
        Amount supports: 100k  1.5m  2b  1e6  10e6  10e5  2.5e3  500
        """
        try:
            amount = parse_amount(raw_amount)
        except ValueError:
            return await ctx.reply(
                embed=error_embed(
                    "❌ Invalid amount.\n"
                    "Examples: `500` · `100k` · `1.5m` · `2b` · `1e6` · `10e6` · `10e5` · `2.5e3`"
                ),
                mention_author=False
            )

        if amount <= 0:
            return await ctx.reply(
                embed=warn_embed("⚠️ Amount must be greater than zero."),
                mention_author=False
            )

        conn = get_db()
        try:
            row         = conn.execute(
                "SELECT amount FROM balances WHERE user_id = ?", (str(member.id),)
            ).fetchone()
            current_bal = row[0] if row else 0

            new_bal = current_bal - amount
            conn.execute(
                "INSERT INTO balances (user_id, amount) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET amount = excluded.amount",
                (str(member.id), new_bal)
            )
            conn.commit()
        finally:
            conn.close()

        await ctx.reply(
            embed=success_embed(
                title="💸 Balance Deducted",
                description=f"Removed **{fmt(amount)}** from {member.mention}'s balance.",
                fields=[
                    ("New Balance", fmt(new_bal),             True),
                    ("Updated by",  ctx.author.display_name, True),
                ],
                footer="FM26 Sims"
            ),
            mention_author=False
        )

        # ── Money log ──────────────────────────────────────────────────────────
        await log_money(
            self.bot,
            ctx.guild,
            event_type="removebal",
            actor=ctx.author,
            target=member,
            amount=amount,
            fmt_fn=fmt,
            extra_fields=[
                (f"{member.display_name}'s New Balance", fmt(new_bal), True),
            ],
        )

    @removebal.error
    async def removebal_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.reply(
                embed=error_embed("⛔ Only **Adminis** can use this command."),
                mention_author=False
            )
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.reply(
                embed=error_embed(
                    "**Usage:** `.removebal @user <amount>` or `.removemoney @user <amount>`\n"
                    "Example: `.rm @user 10e5`"
                ),
                mention_author=False
            )
        await ctx.reply(embed=error_embed(f"An unexpected error occurred: `{error}`"), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(RemoveBal(bot))