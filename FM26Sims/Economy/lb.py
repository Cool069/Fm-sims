import discord
from discord.ext import commands

from Squads.db import get_db, error_embed
from Economy.balutils import fmt


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="lb", aliases=["leaderboard"])
    @commands.has_permissions(administrator=True)
    async def lb(self, ctx: commands.Context):
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT user_id, amount FROM balances ORDER BY amount DESC LIMIT 10"
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return await ctx.reply(
                embed=error_embed("No balance data found yet."),
                mention_author=False,
            )

        lines = []
        for i, (user_id, amount) in enumerate(rows, start=1):
            member = None
            try:
                member = ctx.guild.get_member(int(user_id))
            except (ValueError, TypeError):
                member = None

            display_name = member.display_name if member else f"User {user_id}"
            lines.append(f"**{i}.** {display_name} - **{fmt(amount)}**")

        embed = discord.Embed(
            title="🏆 Money Leaderboard",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        embed.set_footer(text="Top 10 clubs by balance")
        await ctx.reply(embed=embed, mention_author=False)

    @lb.error
    async def lb_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.reply(
                embed=error_embed("⛔ Only **Adminis** can use this command."),
                mention_author=False,
            )
        await ctx.reply(
            embed=error_embed(f"An unexpected error occurred: `{error}`"),
            mention_author=False,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
