import discord
from discord.ext import commands
import os
import asyncio
import json
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=["."], intents=discord.Intents.all())
bot.remove_command('help')

PREFIX_FILE = "prefixes.json"





@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

# Load extensions
async def load_extensions():
    await bot.load_extension("error")
    await bot.load_extension("help_command")
    await bot.load_extension("Squads.addplayer")
    await bot.load_extension("Squads.squad")
    await bot.load_extension("Squads.managers")
    await bot.load_extension("Squads.logo")
    await bot.load_extension("Economy.bal")
    await bot.load_extension("Economy.addbal")
    await bot.load_extension("Economy.removebal")
    await bot.load_extension("Economy.give")
    await bot.load_extension("Economy.logs")
    await bot.load_extension("Economy.collect")
    await bot.load_extension("Economy.lb")
    await bot.load_extension("Economy.reset")
    await bot.load_extension("Transfers.transfer")
    await bot.load_extension("Transfers.auction")
    await bot.load_extension("ping")
    await bot.load_extension("daily")
    
    


# Add cogs manually where needed



# Main function
async def main():
    async with bot:
        await load_extensions()        # 👈 Load dynamic extensions
        await bot.start('MTQ4MDEyMTE2NDc0MjUyNDk3OQ.GAG3ag.uCinUQfmbJStZT4TKl337uk9KYKtgsztZpms2s') # Runs the bot ✅


asyncio.run(main())  # ✅ Ensures all commands are loaded
