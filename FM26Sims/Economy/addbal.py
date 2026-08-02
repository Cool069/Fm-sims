import discord
from discord.ext import commands

from Squads.db import get_db, error_embed, success_embed, warn_embed
from Economy.balutils import parse_amount, fmt
from Economy.logs import log_money


class AddBal(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="addbal", aliases=["add", "ab", "addmoney", "credit"])
    @commands.has_permissions(administrator=True)
    async def addbal(self, ctx: commands.Context, member: discord.Member, *, raw_amount: str):
        """
        .addbal @user <amount>
        .add @user <amount>
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
            conn.execute(
                "INSERT INTO balances (user_id, amount) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET amount = amount + excluded.amount",
                (str(member.id), amount)
            )
            conn.commit()
            new_bal = conn.execute(
                "SELECT amount FROM balances WHERE user_id = ?", (str(member.id),)
            ).fetchone()[0]
        finally:
            conn.close()

        await ctx.reply(
            embed=success_embed(
                title="💰 Balance Added",
                description=f"Added **{fmt(amount)}** to {member.mention}'s balance.",
                fields=[
                    ("New Balance", fmt(new_bal),              True),
                    ("Updated by",  ctx.author.display_name,  True),
                ],
                footer="FM26 Sims"
            ),
            mention_author=False
        )

        # ── Money log ──────────────────────────────────────────────────────────
        await log_money(
            self.bot,
            ctx.guild,
            event_type="addbal",
            actor=ctx.author,
            target=member,
            amount=amount,
            fmt_fn=fmt,
            extra_fields=[
                (f"{member.display_name}'s New Balance", fmt(new_bal), True),
            ],
        )

    @addbal.error
    async def addbal_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.reply(
                embed=error_embed("⛔ Only **Adminis** can use this command."),
                mention_author=False
            )
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.reply(
                embed=error_embed(
                    "**Usage:** `.addbal @user <amount>` or `.add @user <amount>`\n"
                    "Example: `.add @user 10e6`"
                ),
                mention_author=False
            )
        await ctx.reply(embed=error_embed(f"An unexpected error occurred: `{error}`"), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(AddBal(bot))