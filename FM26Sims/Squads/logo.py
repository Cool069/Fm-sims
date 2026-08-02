import discord
from discord.ext import commands

from Squads.db import get_db, error_embed, success_embed

ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png")


class Logo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="logo")
    async def logo(self, ctx: commands.Context):
        # Step 1 — prompt the user
        prompt = discord.Embed(
            description="Please upload your Club Logo (JPG and PNG files are accepted)",
            color=0x3498db
        )
        await ctx.reply(embed=prompt, mention_author=False)

        # Step 2 — wait for their next message with an attachment
        def check(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except TimeoutError:
            return await ctx.send(
                embed=error_embed("⏰ Timed out. Run `.logo` again when you're ready."),
            )

        if not msg.attachments:
            return await ctx.send(embed=error_embed("❌ No file was attached. Run `.logo` and attach an image again."))

        attachment = msg.attachments[0]
        filename   = attachment.filename.lower()

        if not any(filename.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            return await ctx.send(
                embed=error_embed(
                    f"❌ Invalid file type. Only **JPG** and **PNG** are accepted.\n"
                    f"File received: `{attachment.filename}`"
                )
            )

        if attachment.size > 8 * 1024 * 1024:
            return await ctx.send(embed=error_embed("❌ File too large. Maximum size is **8MB**."))

        # Step 3 — save to DB
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO squad_meta (user_id, logo_url) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET logo_url = excluded.logo_url",
                (str(ctx.author.id), attachment.url)
            )
            conn.commit()
        finally:
            conn.close()

        # Step 4 — confirm
        embed = discord.Embed(
            title="✅ Club Logo Set",
            description="Your logo will now appear on your squad.",
            color=0x57F287
        )
        embed.set_image(url=attachment.url)
        await ctx.send(embed=embed)

    @logo.error
    async def logo_error(self, ctx: commands.Context, error):
        await ctx.reply(embed=error_embed(f"An unexpected error occurred: `{error}`"), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Logo(bot))