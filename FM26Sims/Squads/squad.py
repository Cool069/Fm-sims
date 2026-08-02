import re
import discord
from discord.ext import commands

from Squads.db import get_db, error_embed


class Squad(commands.Cog):
    """Command to view a user's squad."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="squad")
    async def view_squad(self, ctx: commands.Context, member: discord.Member = None):
        """
        .squad          -> your own squad
        .squad @user    -> that user's squad
        """
        target    = member or ctx.author
        target_id = str(target.id)

        conn = get_db()
        try:
            meta = conn.execute(
                "SELECT manager, raw_squad, logo_url FROM squad_meta WHERE user_id = ?",
                (target_id,)
            ).fetchone()

            total = conn.execute(
                "SELECT COUNT(*) FROM squad WHERE user_id = ?",
                (target_id,)
            ).fetchone()[0]

            total_loan = conn.execute(
                "SELECT COUNT(*) FROM squad WHERE user_id = ? AND on_loan = 1",
                (target_id,)
            ).fetchone()[0]
        finally:
            conn.close()

        club_name = target.display_name

        # ── Empty squad ───────────────────────────────────────────────────────
        if not meta or not meta[1]:
            is_self  = target.id == ctx.author.id
            desc     = "No squad registered yet! Use `.addplayer` to add players." if is_self \
                       else f"{club_name} hasn't registered a squad yet."
            logo_url = meta[2] if meta else None
            embed    = discord.Embed(title=f"🏟️ {club_name}", description=desc, color=0x95a5a6)
            embed.set_thumbnail(url=logo_url or target.display_avatar.url)
            return await ctx.reply(embed=embed)

        manager, raw_squad, logo_url = meta

        # ── Clean raw squad text ──────────────────────────────────────────────
        display_lines = []
        for line in raw_squad.splitlines():
            stripped = line.strip()
            if stripped.startswith("<@"):
                continue
            if re.match(r"^[_*\s]*[A-Z\s]+ SQUAD[_*\s]*$", stripped, re.IGNORECASE):
                continue
            if re.match(r"^[*_\s]*manager\s*[-:].+", stripped, re.IGNORECASE):
                continue
            display_lines.append(stripped)

        while display_lines and display_lines[0] == "":
            display_lines.pop(0)
        while display_lines and display_lines[-1] == "":
            display_lines.pop()

        body = "\n".join(display_lines)

        # ── Build description ─────────────────────────────────────────────────
        description_parts = []
        if manager:
            description_parts.append(f"**MANAGER - {manager}**")
            description_parts.append("")
        description_parts.append(body)
        description = "\n".join(description_parts)

        if len(description) > 4096:
            description = description[:4090] + "\n..."

        # ── Embed ─────────────────────────────────────────────────────────────
        embed = discord.Embed(
            title=f"🎭 {club_name}'s Squad",
            description=description,
            color=0x3498db
        )

        # Use club logo if set, otherwise fall back to Discord avatar
        embed.set_thumbnail(url=logo_url or target.display_avatar.url)

        footer_parts = [f"Total: {total} player(s)"]
        if total_loan:
            footer_parts.append(f"{total_loan} out on loan")
        embed.set_footer(text=" · ".join(footer_parts))

        await ctx.reply(embed=embed)

    @view_squad.error
    async def view_squad_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MemberNotFound):
            return await ctx.reply(embed=error_embed("❌ User not found."))
        await ctx.reply(embed=error_embed(f"❌ Unexpected error: `{error}`"))


async def setup(bot: commands.Bot):
    await bot.add_cog(Squad(bot))