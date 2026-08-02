import discord
from discord.ext import commands
import sqlite3
from typing import Optional

from Squads.db import get_db, error_embed, success_embed
from Economy.balutils import fmt


def wipe_all_data() -> dict:
    """Wipe all known bot data tables and return affected row counts."""
    tables = [
        "balances",
        "collect_cooldowns",
        "squad",
        "squad_meta",
        "money_log_channels",
    ]

    counts = {}
    conn = get_db()
    try:
        for table in tables:
            try:
                count_row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = count_row[0] if count_row else 0
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                # Table might not exist yet in older DB state.
                counts[table] = 0
        conn.commit()
    finally:
        conn.close()

    return counts


def reset_all_balances() -> int:
    """Set every stored balance to zero and return the number of rows updated."""
    conn = get_db()
    try:
        count_row = conn.execute("SELECT COUNT(*) FROM balances").fetchone()
        count = count_row[0] if count_row else 0
        conn.execute("UPDATE balances SET amount = 0")
        conn.commit()
        return count
    finally:
        conn.close()


class ResetAllConfirmView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the command user can respond to this reset confirmation.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Yes, Wipe All", style=discord.ButtonStyle.danger)
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        counts = wipe_all_data()
        total_deleted = sum(counts.values())

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embed=success_embed(
                title="🚨 Full Data Reset Complete",
                description="All bot data has been wiped.",
                fields=[
                    ("Rows Deleted", str(total_deleted), True),
                    ("Reset by", interaction.user.display_name, True),
                    (
                        "Tables",
                        (
                            f"balances: {counts.get('balances', 0)}\n"
                            f"collect_cooldowns: {counts.get('collect_cooldowns', 0)}\n"
                            f"squad: {counts.get('squad', 0)}\n"
                            f"squad_meta: {counts.get('squad_meta', 0)}\n"
                            f"money_log_channels: {counts.get('money_log_channels', 0)}"
                        ),
                        False,
                    ),
                ],
                footer="FM26 Sims",
            ),
            view=self,
        )

    @discord.ui.button(label="No, Cancel", style=discord.ButtonStyle.secondary)
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embed=error_embed("❎ Reset cancelled. No data was changed."),
            view=self,
        )


class ResetMoneyConfirmView(discord.ui.View):
    def __init__(self, author_id: int, user_id: str, display_name: str, old_amount: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.user_id = user_id
        self.display_name = display_name
        self.old_amount = old_amount
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the command user can respond to this reset confirmation.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO balances (user_id, amount) VALUES (?, 0) "
                "ON CONFLICT(user_id) DO UPDATE SET amount = 0",
                (self.user_id,),
            )
            conn.commit()
        finally:
            conn.close()

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embed=success_embed(
                title="♻️ Balance Reset",
                description=f"**{self.display_name}'s** balance has been reset to **£0**.",
                fields=[
                    ("Previous Balance", fmt(self.old_amount), True),
                    ("Reset by", interaction.user.display_name, True),
                ],
                footer="FM26 Sims",
            ),
            view=self,
        )

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embed=error_embed("❎ Reset cancelled. No data was changed."),
            view=self,
        )


class ResetAllBalancesConfirmView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the command user can respond to this reset confirmation.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        updated_count = reset_all_balances()

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embed=success_embed(
                title="♻️ All Balances Reset",
                description="Every stored balance has been reset to **£0**.",
                fields=[
                    ("Accounts Updated", str(updated_count), True),
                    ("Reset by", interaction.user.display_name, True),
                ],
                footer="FM26 Sims",
            ),
            view=self,
        )

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embed=error_embed("❎ Reset cancelled. No data was changed."),
            view=self,
        )


class ResetMoney(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def resolve_user(self, ctx: commands.Context, user_input: str) -> tuple[Optional[str], Optional[str]]:
        """
        Resolve a user from various input formats.
        Returns: (user_id, display_name) or (None, None) if not found
        
        Accepts:
        - Discord ID (numeric string)
        - User mention
        - Username from leaderboard
        """
        user_input = user_input.strip()
        
        # Try to parse as Discord ID (numeric)
        if user_input.isdigit():
            try:
                user = await self.bot.fetch_user(int(user_input))
                return (str(user.id), user.name)
            except (discord.NotFound, discord.HTTPException):
                pass
        
        # Try to find as member mention first
        try:
            member = await commands.MemberConverter().convert(ctx, user_input)
            return (str(member.id), member.display_name)
        except commands.MemberNotFound:
            pass
        
        # Try to find by username in leaderboard (case-insensitive partial match)
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT user_id FROM balances ORDER BY amount DESC"
            ).fetchall()
            
            for (user_id,) in rows:
                member = ctx.guild.get_member(int(user_id))
                if member:
                    if member.display_name.lower() == user_input.lower():
                        return (user_id, member.display_name)
                    # Also check for partial matches
                    if user_input.lower() in member.display_name.lower():
                        return (user_id, member.display_name)
        finally:
            conn.close()
        
        return (None, None)

    @commands.command(name="resetmoney", aliases=["rmoney", "resetbal", "clearbal"])
    @commands.has_permissions(administrator=True)
    async def resetmoney(self, ctx: commands.Context, *, user_input: str):
        """Reset a user's balance. Accepts: mention, Discord ID, or username from leaderboard."""
        user_id, display_name = await self.resolve_user(ctx, user_input)
        
        if not user_id:
            return await ctx.reply(
                embed=error_embed(
                    f"❌ Could not find user: `{user_input}`\n\n"
                    "Please provide:\n"
                    "• A user mention: `@username`\n"
                    "• A Discord ID: `123456789`\n"
                    "• A username from leaderboard"
                ),
                mention_author=False,
            )
        
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT amount FROM balances WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            old_amount = row[0] if row else 0
        finally:
            conn.close()

        view = ResetMoneyConfirmView(author_id=ctx.author.id, user_id=user_id, display_name=display_name, old_amount=old_amount)
        embed = discord.Embed(
            title="⚠️ Confirm Balance Reset",
            description=(
                f"This will reset **{display_name}'s** balance to **£0**.\n\n"
                "Are you sure?"
            ),
            color=0xED4245,
        )
        embed.set_footer(text="This action cannot be undone")

        msg = await ctx.reply(embed=embed, view=view, mention_author=False)
        view.message = msg

    @resetmoney.error
    async def resetmoney_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.reply(
                embed=error_embed("⛔ Only **Admins** can use this command."),
                mention_author=False,
            )
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.reply(
                embed=error_embed("**Usage:** `.resetmoney <@user | Discord ID | username>`"),
                mention_author=False,
            )
        await ctx.reply(
            embed=error_embed(f"An unexpected error occurred: `{error}`"),
            mention_author=False,
        )

    @commands.command(name="resetall", aliases=["resetallmoney", "clearallmoney"])
    @commands.has_permissions(administrator=True)
    async def resetall(self, ctx: commands.Context):
        view = ResetAllConfirmView(author_id=ctx.author.id)
        embed = discord.Embed(
            title="⚠️ Confirm Full Reset",
            description=(
                "This will wipe **all bot data** including balances, squads, "
                "squad meta, collect cooldowns, and money log channel settings.\n\n"
                "Are you sure?"
            ),
            color=0xED4245,
        )
        embed.set_footer(text="This action cannot be undone")

        msg = await ctx.reply(embed=embed, view=view, mention_author=False)
        view.message = msg

    @commands.command(name="resetallbal", aliases=["resetallbalance", "clearallbal"])
    @commands.has_permissions(administrator=True)
    async def resetallbal(self, ctx: commands.Context):
        view = ResetAllBalancesConfirmView(author_id=ctx.author.id)
        embed = discord.Embed(
            title="⚠️ Confirm Balance Reset",
            description=(
                "This will reset **every user's balance** to **£0**.\n\n"
                "Are you sure?"
            ),
            color=0xED4245,
        )
        embed.set_footer(text="This action cannot be undone")

        msg = await ctx.reply(embed=embed, view=view, mention_author=False)
        view.message = msg

    @resetallbal.error
    async def resetallbal_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            return await ctx.reply(
                embed=error_embed("⛔ Only **Adminis** can use this command."),
                mention_author=False,
            )
        await ctx.reply(
            embed=error_embed(f"An unexpected error occurred: `{error}`"),
            mention_author=False,
        )

    @resetall.error
    async def resetall_error(self, ctx: commands.Context, error):
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
    await bot.add_cog(ResetMoney(bot))
