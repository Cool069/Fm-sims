import discord
from discord.ext import commands
import random

from Squads.db import get_db, error_embed, success_embed, warn_embed
from Economy.balutils import parse_amount, fmt
from Economy.logs import log_money


TRANSACTION_GIFS = [
    "https://tenor.com/view/walter-white-and-jesse-pinkman-handshake-walter-and-jesse-handshake-gif-12675755010462311397",
    "https://tenor.com/view/gifgod-remasteredgifs-handshake-shake-captain-america-gif-15154140431823499405",
    "https://tenor.com/view/trump-putin-red-square-handshake-gif-17748020122143559585",
    "https://tenor.com/view/anime-yes-gif-14856640862890942143",
]


class Give(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="give", aliases=["send", "pay", "givemoney", "sendmoney"])
    async def give(self, ctx: commands.Context, member: discord.Member, *, raw_amount: str):
        """
        .give @user <amount>  — transfer money from your balance to another user
        .send @user <amount> / .pay @user <amount>
        Amount supports: 100k  1.5m  2b  1e6  10e6  10e5  2.5e3  500
        """
        # Can't give to yourself
        if member.id == ctx.author.id:
            return await ctx.reply(
                embed=warn_embed("⚠️ You can't give money to yourself."),
                mention_author=False
            )

        # Can't give to bots
        if member.bot:
            return await ctx.reply(
                embed=warn_embed("⚠️ You can't give money to a bot."),
                mention_author=False
            )

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

        sender_id    = str(ctx.author.id)
        recipient_id = str(member.id)

        conn = get_db()
        try:
            # Fetch current balances for rollback safety
            row = conn.execute(
                "SELECT amount FROM balances WHERE user_id = ?", (sender_id,)
            ).fetchone()
            sender_bal = row[0] if row else 0

            row = conn.execute(
                "SELECT amount FROM balances WHERE user_id = ?", (recipient_id,)
            ).fetchone()
            recipient_bal = row[0] if row else 0

            if amount > sender_bal:
                return await ctx.reply(
                    embed=warn_embed(
                        f"⚠️ You only have **{fmt(sender_bal)}**.\n"
                        f"Cannot transfer **{fmt(amount)}**."
                    ),
                    mention_author=False
                )

            new_sender_bal = sender_bal - amount
            new_recipient_bal = recipient_bal + amount

            # Apply DB updates
            conn.execute(
                "INSERT INTO balances (user_id, amount) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET amount = excluded.amount",
                (sender_id, new_sender_bal)
            )
            conn.execute(
                "INSERT INTO balances (user_id, amount) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET amount = amount + excluded.amount",
                (recipient_id, amount)
            )
            conn.commit()
        finally:
            conn.close()

        chosen_gif = random.choice(TRANSACTION_GIFS)

        # Build per-user embeds (only show each user's own balance)
        sender_embed = success_embed(
            title="🤝 Transfer Sent",
            description=f"You sent **{fmt(amount)}** to {member.display_name}.",
            fields=[
                ("Your New Balance", fmt(new_sender_bal), True),
            ],
            footer="FM26 Sims"
        )
        sender_embed.set_image(url=chosen_gif)

        recipient_embed = success_embed(
            title="🤝 Transfer Received",
            description=f"You received **{fmt(amount)}** from {ctx.author.display_name}.",
            fields=[
                ("Your New Balance", fmt(new_recipient_bal), True),
            ],
            footer="FM26 Sims"
        )
        recipient_embed.set_image(url=chosen_gif)

        # Attempt to DM both parties; if either DM fails, roll back the transaction.
        dm_failed = False
        dm_failed_reason = None
        try:
            await ctx.author.send(embed=sender_embed, content=chosen_gif)
        except Exception as e:
            dm_failed = True
            dm_failed_reason = f"Could not DM sender: {e}"

        try:
            await member.send(embed=recipient_embed, content=chosen_gif)
        except Exception as e:
            dm_failed = True
            dm_failed_reason = f"Could not DM recipient: {e}"

        if dm_failed:
            # Roll back DB
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO balances (user_id, amount) VALUES (?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET amount = excluded.amount",
                    (sender_id, sender_bal),
                )
                conn.execute(
                    "INSERT INTO balances (user_id, amount) VALUES (?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET amount = excluded.amount",
                    (recipient_id, recipient_bal),
                )
                conn.commit()
            finally:
                conn.close()

            # Inform the command user in-channel about the failure
            await ctx.reply(
                embed=error_embed("❌ Transfer cancelled — could not deliver DMs to one or both users. Transaction has been rolled back."),
                mention_author=False,
            )
            # Optionally log the DM failure to console for debugging
            try:
                self.bot.logger and self.bot.logger.warning(dm_failed_reason)
            except Exception:
                pass
            return

        # ── Money log ──────────────────────────────────────────────────────────
        await log_money(
            self.bot,
            ctx.guild,
            event_type="transfer",
            actor=ctx.author,
            target=member,
            amount=amount,
            fmt_fn=fmt,
            extra_fields=[
                (f"{ctx.author.display_name}'s New Balance", fmt(new_sender_bal),    True),
                (f"{member.display_name}'s New Balance",     fmt(new_recipient_bal), True),
            ],
        )

    @give.error
    async def give_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MemberNotFound):
            return await ctx.reply(embed=error_embed("❌ User not found."), mention_author=False)
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.reply(
                embed=error_embed(
                    "**Usage:** `.give @user <amount>` or `.send @user <amount>`\n"
                    "Example: `.give @user 10e5`"
                ),
                mention_author=False
            )
        await ctx.reply(embed=error_embed(f"An unexpected error occurred: `{error}`"), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Give(bot))