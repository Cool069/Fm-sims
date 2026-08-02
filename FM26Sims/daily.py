from pathlib import Path
import random
import time

import discord
from discord.ext import commands


class Daily(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.base_dir = Path(__file__).resolve().parent
        self.upload_dir = self.base_dir / "photo"
        self.upload_dir.mkdir(exist_ok=True)

    def _allowed_image_suffix(self, filename: str) -> bool:
        return Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}

    def _get_uploaded_images(self) -> list[Path]:
        return sorted(
            [
                p
                for p in self.upload_dir.iterdir()
                if p.is_file() and self._allowed_image_suffix(p.name)
            ]
        )

    async def _save_uploaded_images(self, attachments: list[discord.Attachment]) -> tuple[int, list[str]]:
        saved_names: list[str] = []
        for attachment in attachments:
            if not self._allowed_image_suffix(attachment.filename):
                continue
            safe_name = f"{int(time.time() * 1000)}_{attachment.filename.replace(' ', '_')}"
            save_path = self.upload_dir / safe_name
            await attachment.save(save_path)
            saved_names.append(safe_name)
        return len(saved_names), saved_names

    @commands.command(name="daily")
    @commands.cooldown(1, 86400, commands.BucketType.user)
    async def daily(self, ctx: commands.Context):
        uploaded = self._get_uploaded_images()
        if not uploaded:
            await ctx.reply(
                "No images found in `photo`. Use `.dailyupload` with attachments first.",
                mention_author=False,
            )
            return

        chosen = random.choice(uploaded)
        file = discord.File(str(chosen), filename=chosen.name)
        await ctx.reply(content="FM26 Daily Card", file=file, mention_author=False)

    @daily.error
    async def daily_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandOnCooldown):
            retry_seconds = int(error.retry_after)
            hours, remainder = divmod(retry_seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            await ctx.reply(
                f"You can use `.daily` once every 24 hours. Try again in {hours}h {minutes}m.",
                mention_author=False,
            )
            return
        raise error

    @commands.command(name="dailyupload", aliases=["dupload", "dailyaddimage"])
    async def dailyupload(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.reply("`.dailyupload` can only be used in a server.", mention_author=False)
            return

        perms = ctx.author.guild_permissions
        if not (perms.manage_messages or perms.administrator):
            await ctx.reply("Only moderators can use `.dailyupload`.", mention_author=False)
            return

        if not ctx.message.attachments:
            await ctx.reply(
                "Attach one or more images when using `.dailyupload` (png/jpg/jpeg/webp).",
                mention_author=False,
            )
            return

        saved_count, saved_names = await self._save_uploaded_images(ctx.message.attachments)
        if saved_count == 0:
            await ctx.reply(
                "No supported image files found. Use png/jpg/jpeg/webp attachments.",
                mention_author=False,
            )
            return

        await ctx.reply(
            f"Saved {saved_count} image(s) in `photo`. `.daily` will pick one randomly.",
            mention_author=False,
        )

    @commands.command(name="dailyimages")
    async def dailyimages(self, ctx: commands.Context):
        uploaded = self._get_uploaded_images()
        if not uploaded:
            await ctx.reply("No uploaded daily images yet.", mention_author=False)
            return

        preview_names = "\n".join(f"- {p.name}" for p in uploaded[:15])
        more = "" if len(uploaded) <= 15 else f"\n... and {len(uploaded) - 15} more"
        await ctx.reply(
            f"Uploaded daily images: {len(uploaded)}\n{preview_names}{more}",
            mention_author=False,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Daily(bot))