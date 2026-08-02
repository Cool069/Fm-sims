import time
import discord
from discord.ext import commands


class Ping(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        start = time.perf_counter()

        # Send a lightweight placeholder first so we can measure message roundtrip.
        msg = await ctx.reply("Pinging...", mention_author=False)

        roundtrip_ms = (time.perf_counter() - start) * 1000
        ws_latency_ms = self.bot.latency * 1000

        if self.bot.is_closed():
            status_text = "Offline"
            status_emoji = "🔴"
        elif self.bot.is_ready():
            status_text = "Online"
            status_emoji = "🟢"
        else:
            status_text = "Starting"
            status_emoji = "🟡"

        embed = discord.Embed(
            title="🏓 Pong!",
            color=0x57F287,
        )
        embed.add_field(name="WebSocket Latency", value=f"`{ws_latency_ms:.2f} ms`", inline=True)
        embed.add_field(name="Roundtrip", value=f"`{roundtrip_ms:.2f} ms`", inline=True)
        embed.add_field(name="Bot Status", value=f"{status_emoji} **{status_text}**", inline=False)
        embed.set_footer(text="FM26 Sims")

        await msg.edit(content=None, embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Ping(bot))
