import discord
from discord.ext import commands

from Squads.db import get_db, error_embed
from Economy.balutils import fmt


def get_balance(user_id: str) -> int:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT amount FROM balances WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def get_logo(user_id: str):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT logo_url FROM squad_meta WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


class BalanceButton(discord.ui.View):
    def __init__(self, target: discord.Member, requester: discord.Member):
        super().__init__(timeout=60)
        self.target    = target
        self.requester = requester

    @discord.ui.button(label="View Balance", style=discord.ButtonStyle.success)
    async def view_balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_admin     = interaction.user.guild_permissions.administrator
        is_target    = interaction.user.id == self.target.id
        is_requester = interaction.user.id == self.requester.id

        if not (is_admin or is_target or is_requester):
            return await interaction.response.send_message(
                embed=error_embed("⛔ You don't have permission to view this balance."),
                ephemeral=True
            )

        balance  = get_balance(str(self.target.id))
        logo_url = get_logo(str(self.target.id))

        embed = discord.Embed(
            title="💰 Club Balance",
            color=0x57F287
        )
        embed.add_field(name="Club",    value=self.target.display_name, inline=True)
        embed.add_field(name="Balance", value=f"**{fmt(balance)}**",    inline=True)
        embed.set_thumbnail(url=logo_url or self.target.display_avatar.url)
        

        await interaction.response.send_message(embed=embed, ephemeral=True)


class Balance(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="balance", aliases=["bal", "money", "wallet"])
    async def balance(self, ctx: commands.Context, member: discord.Member = None):
        if member:
            if not ctx.author.guild_permissions.administrator:
                return await ctx.reply(
                    embed=error_embed("⛔ Only **Adminis** can view another user's balance."),
                    mention_author=False
                )
            target = member
        else:
            target = ctx.author

        is_self = target.id == ctx.author.id
        desc    = "Click the button below to see your balance." if is_self \
                  else f"Click the button below to see **{target.display_name}**'s balance."

        logo_url = get_logo(str(target.id))

        embed = discord.Embed(
            title="🏦 Club Balance",
            description=desc,
            color=0x9B59B6
        )
        embed.set_thumbnail(url=logo_url or target.display_avatar.url)

        view = BalanceButton(target=target, requester=ctx.author)
        await ctx.reply(embed=embed, view=view, mention_author=False)

    @balance.error
    async def balance_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MemberNotFound):
            return await ctx.reply(embed=error_embed("❌ User not found."), mention_author=False)
        await ctx.reply(embed=error_embed(f"An unexpected error occurred: `{error}`"), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Balance(bot))