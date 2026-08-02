from discord.ext import commands

class ErrorHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        print(f"[ERROR] Command `{ctx.command}` failed.")
        print(f"[ERROR MESSAGE] {type(error).__name__}: {error}")

async def setup(bot):
    await bot.add_cog(ErrorHandler(bot))
