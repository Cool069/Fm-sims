import asyncio
import random
from typing import Optional

import discord
from discord.ext import commands

from Economy.bal import get_balance
from Economy.balutils import fmt, parse_amount
from Squads.addplayer import get_squad_players, rebuild_raw_squad
from Squads.db import get_db, error_embed, success_embed, warn_embed, get_setting


TRANSFER_GIFS = [
    "https://tenor.com/view/walter-white-and-jesse-pinkman-handshake-walter-and-jesse-handshake-gif-12675755010462311397",
    "https://tenor.com/view/gifgod-remasteredgifs-handshake-shake-captain-america-gif-15154140431823499405",
    "https://tenor.com/view/trump-putin-red-square-handshake-gif-17748020122143559585",
    "https://tenor.com/view/anime-yes-gif-14856640862890942143",
]


def _sold_channel_id() -> Optional[int]:
    value = get_setting("sold_channel_id")
    if not value or not str(value).isdigit():
        return None
    return int(value)


def _trade_label(trade_type: str) -> str:
    return {
        "player": "Player Only",
        "player_cash": "Player + Cash",
        "cash": "Cash Only",
    }.get(trade_type, "Transfer")


def _trade_flow(trade_type: str) -> str:
    if trade_type == "player":
        return "One player moves with no cash attached from your side."
    if trade_type == "player_cash":
        return "One player plus cash from your side."
    if trade_type == "cash":
        return "Cash-only deal from your side."
    return "Choose a deal type to continue."

class TransferProposal:
    def __init__(
        self,
        bot: commands.Bot,
        guild: discord.Guild,
        sender: discord.Member,
        recipient: discord.Member,
        sender_trade_type: str,
        sender_player_id: Optional[int] = None,
        sender_player_name: Optional[str] = None,
        sender_player_position: Optional[str] = None,
        sender_cash_amount: Optional[int] = None,
        recipient_trade_type: Optional[str] = None,
        recipient_player_id: Optional[int] = None,
        recipient_player_name: Optional[str] = None,
        recipient_player_position: Optional[str] = None,
        recipient_cash_amount: Optional[int] = None,
    ):
        self.bot = bot
        self.guild = guild
        self.sender = sender
        self.recipient = recipient
        self.sender_trade_type = sender_trade_type
        self.sender_player_id = sender_player_id
        self.sender_player_name = sender_player_name
        self.sender_player_position = sender_player_position
        self.sender_cash_amount = sender_cash_amount
        self.recipient_trade_type = recipient_trade_type
        self.recipient_player_id = recipient_player_id
        self.recipient_player_name = recipient_player_name
        self.recipient_player_position = recipient_player_position
        self.recipient_cash_amount = recipient_cash_amount
        self.lock = asyncio.Lock()
        self.approved = set()
        self.rejected = False
        self.completed = False
        self.sender_message: Optional[discord.Message] = None
        self.recipient_message: Optional[discord.Message] = None
        self.sender_view: Optional[TransferApprovalView] = None
        self.recipient_view: Optional[TransferApprovalView] = None

    def _side_user(self, side: str) -> discord.Member:
        return self.sender if side == "sender" else self.recipient

    def _side_fields(self, side: str):
        if side == "sender":
            return (
                self.sender_trade_type,
                self.sender_player_id,
                self.sender_player_name,
                self.sender_player_position,
                self.sender_cash_amount,
            )
        return (
            self.recipient_trade_type,
            self.recipient_player_id,
            self.recipient_player_name,
            self.recipient_player_position,
            self.recipient_cash_amount,
        )

    def side_ready(self, side: str) -> bool:
        trade_type, player_id, _, _, cash_amount = self._side_fields(side)
        if not trade_type:
            return False
        if trade_type in {"player", "player_cash"} and not player_id:
            return False
        if trade_type in {"cash", "player_cash"} and not cash_amount:
            return False
        return True

    def side_summary(self, side: str) -> str:
        trade_type, _, player_name, player_position, cash_amount = self._side_fields(side)
        user = self._side_user(side)
        other = self.recipient if side == "sender" else self.sender
        pieces = [f"**{side.title()} Offer:** {_trade_label(trade_type or '')}"]
        if player_name:
            suffix = f" ({player_position})" if player_position else ""
            pieces.append(f"**Player:** {player_name}{suffix}")
        if cash_amount is not None:
            pieces.append(f"**Cash:** {fmt(cash_amount)} from {user.display_name} to {other.display_name}")
        if not trade_type:
            pieces.append("**Status:** Pending configuration")
        return "\n".join(pieces)

    def deal_summary(self) -> str:
        return "\n\n".join((self.side_summary("sender"), self.side_summary("recipient")))

    def proposal_embed(self, side: str) -> discord.Embed:
        target = self.sender if side == "sender" else self.recipient
        other = self.recipient if side == "sender" else self.sender
        embed = discord.Embed(
            title="🤝 Transfer Proposal",
            description=(
                f"You are {'the sender' if side == 'sender' else 'the recipient'} in this deal.\n"
                "Both sides must approve before anything is processed."
            ),
            color=0xF1C40F,
        )
        if side == "recipient" and not self.side_ready("recipient"):
            embed.add_field(name="Deal", value=self.side_summary("sender"), inline=False)
            embed.add_field(name="Your Offer", value="Configure your side below before approving.", inline=False)
        else:
            embed.add_field(name="Deal", value=self.deal_summary(), inline=False)
        embed.add_field(name="Your Side", value=target.display_name, inline=True)
        embed.add_field(name="Other Side", value=other.display_name, inline=True)
        embed.set_footer(text="Approve or reject from this DM")
        return embed

    def approved_embed(self, side: str) -> discord.Embed:
        embed = self.proposal_embed(side)
        embed.color = 0x57F287
        embed.title = "✅ Transfer Approved"
        embed.description = "Your approval has been recorded. Waiting for the other side."
        return embed

    def cancelled_embed(self, reason: str) -> discord.Embed:
        embed = discord.Embed(
            title="❌ Transfer Cancelled",
            description=reason,
            color=0xED4245,
        )
        embed.add_field(name="Deal", value=self.deal_summary(), inline=False)
        embed.set_footer(text="FM26 Sims")
        return embed

    def completed_embed(self, user: discord.Member, balance: int, player_line: Optional[str], cash_line: Optional[str]) -> discord.Embed:
        embed = discord.Embed(
            title="✅ Transfer Complete",
            description="The deal was approved by both sides and has been processed.",
            color=0x57F287,
        )
        if player_line:
            embed.add_field(name="Player Update", value=player_line, inline=False)
        if cash_line:
            embed.add_field(name="Cash Update", value=cash_line, inline=False)
        embed.add_field(name="Your New Balance", value=fmt(balance), inline=True)
        embed.add_field(name="User", value=user.display_name, inline=True)
        embed.set_footer(text="FM26 Sims")
        return embed

    async def send_proposal(self):
        sender_view = TransferApprovalView(self, "sender")
        recipient_view = TransferApprovalView(self, "recipient")
        self.sender_view = sender_view
        self.recipient_view = recipient_view

        gif_url = random.choice(TRANSFER_GIFS)
        try:
            self.sender_message = await self.sender.send(content=gif_url, embed=self.proposal_embed("sender"), view=sender_view)
            sender_view.message = self.sender_message
        except discord.Forbidden:
            raise RuntimeError("Unable to DM the sender.")

        try:
            self.recipient_message = await self.recipient.send(content=gif_url, embed=self.proposal_embed("recipient"), view=recipient_view)
            recipient_view.message = self.recipient_message
        except discord.Forbidden:
            raise RuntimeError(f"Unable to DM {self.recipient.display_name}.")

    async def mark_approval(self, side: str) -> bool:
        async with self.lock:
            if self.rejected or self.completed:
                return False
            self.approved.add(0 if side == "sender" else 1)
            return len(self.approved) == 2

    async def mark_rejected(self, reason: str):
        async with self.lock:
            if self.rejected or self.completed:
                return
            self.rejected = True

        embed = self.cancelled_embed(reason)
        for view in (self.sender_view, self.recipient_view):
            if not view:
                continue
            for item in view.children:
                item.disabled = True

        for message, view in ((self.sender_message, self.sender_view), (self.recipient_message, self.recipient_view)):
            if message and view:
                try:
                    await message.edit(embed=embed, view=view)
                except discord.HTTPException:
                    pass

    async def finalize(self):
        async with self.lock:
            if self.rejected or self.completed or len(self.approved) < 2:
                return
            self.completed = True

        try:
            result = await self._apply_trade()
        except Exception as exc:
            await self.mark_rejected(f"The trade could not be processed: {exc}")

            sender_player_lines = []
            recipient_player_lines = []
            sender_cash_lines = []
            recipient_cash_lines = []
            touched_users = set()

            for side, user, other, trade_type, player_id, player_name, player_position, cash_amount in (
                (
                    "sender",
                    self.sender,
                    self.recipient,
                    self.sender_trade_type,
                    self.sender_player_id,
                    self.sender_player_name,
                    self.sender_player_position,
                    self.sender_cash_amount,
                ),
                (
                    "recipient",
                    self.recipient,
                    self.sender,
                    self.recipient_trade_type,
                    self.recipient_player_id,
                    self.recipient_player_name,
                    self.recipient_player_position,
                    self.recipient_cash_amount,
                ),
            ):
                if not trade_type:
                    continue

                if trade_type in {"cash", "player_cash"}:
                    amount = cash_amount or 0
                    if amount <= 0:
                        raise ValueError("Cash amount must be greater than zero.")
                    if user.id == self.sender.id:
                        if sender_balance < amount:
                            raise ValueError(f"{user.display_name} does not have enough balance.")
                        sender_balance -= amount
                        recipient_balance += amount
                    else:
                        if recipient_balance < amount:
                            raise ValueError(f"{user.display_name} does not have enough balance.")
                        recipient_balance -= amount
                        sender_balance += amount

                    cash_line = f"{fmt(amount)} from {user.display_name} to {other.display_name}."
                    if user.id == self.sender.id:
                        sender_cash_lines.append(f"Sent {cash_line}")
                        recipient_cash_lines.append(f"Received {cash_line}")
                    else:
                        sender_cash_lines.append(f"Received {cash_line}")
                        recipient_cash_lines.append(f"Sent {cash_line}")

                if trade_type in {"player", "player_cash"}:
                    row = conn.execute(
                        "SELECT user_id, player_name, position FROM squad WHERE id = ?",
                        (player_id,),
                    ).fetchone()
                    if not row:
                        raise ValueError("The selected player is no longer available.")
                    if str(row[0]) != str(user.id):
                        raise ValueError(f"That player is no longer owned by {user.display_name}.")

                    conn.execute(
                        "UPDATE squad SET user_id = ? WHERE id = ?",
                        (str(other.id), player_id),
                    )
                    touched_users.update({str(self.sender.id), str(self.recipient.id)})

                    player_line = f"{player_name} ({player_position or 'no position'}) moved from {user.display_name} to {other.display_name}."
                    if user.id == self.sender.id:
                        sender_player_lines.append(f"Sent {player_line}")
                        recipient_player_lines.append(f"Received {player_line}")
                    else:
                        sender_player_lines.append(f"Received {player_line}")
                        recipient_player_lines.append(f"Sent {player_line}")

            if touched_users:
                for user_id in touched_users:
                    raw_squad = rebuild_raw_squad(conn, user_id)
                    conn.execute(
                        "INSERT INTO squad_meta (user_id, raw_squad) VALUES (?, ?) "
                        "ON CONFLICT(user_id) DO UPDATE SET raw_squad = excluded.raw_squad",
                        (user_id, raw_squad),
                    )
                summary.add_field(name="Approved By", value=f"{self.sender.display_name} • {self.recipient.display_name}", inline=False)
                summary.set_footer(text="FM26 Sims")
                try:
                    await channel.send(embed=summary)
                except discord.HTTPException:
                    pass

    async def _apply_trade(self) -> dict:
        sender_balance_before = get_balance(str(self.sender.id))
        recipient_balance_before = get_balance(str(self.recipient.id))

        conn = get_db()
        try:
            conn.execute("BEGIN")

            sender_balance = sender_balance_before
            recipient_balance = recipient_balance_before

            if self.trade_type == "cash":
                if sender_balance < (self.cash_amount or 0):
                    raise ValueError(f"{self.sender.display_name} does not have enough balance.")
                sender_balance -= self.cash_amount or 0
                recipient_balance += self.cash_amount or 0
            elif self.trade_type == "player_cash":
                if recipient_balance < (self.cash_amount or 0):
                    raise ValueError(f"{self.recipient.display_name} does not have enough balance.")
                recipient_balance -= self.cash_amount or 0
                sender_balance += self.cash_amount or 0

            if self.trade_type in {"player", "player_cash"}:
                row = conn.execute(
                    "SELECT user_id, player_name, position FROM squad WHERE id = ?",
                    (self.player_id,),
                ).fetchone()
                if not row:
                    raise ValueError("The selected player is no longer available.")
                if str(row[0]) != str(self.sender.id):
                    raise ValueError("That player is no longer owned by the sender.")

                conn.execute(
                    "UPDATE squad SET user_id = ? WHERE id = ?",
                    (str(self.recipient.id), self.player_id),
                )

                sender_raw = rebuild_raw_squad(conn, str(self.sender.id))
                recipient_raw = rebuild_raw_squad(conn, str(self.recipient.id))
                conn.execute(
                    "INSERT INTO squad_meta (user_id, raw_squad) VALUES (?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET raw_squad = excluded.raw_squad",
                    (str(self.sender.id), sender_raw),
                )
                conn.execute(
                    "INSERT INTO squad_meta (user_id, raw_squad) VALUES (?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET raw_squad = excluded.raw_squad",
                    (str(self.recipient.id), recipient_raw),
                )

            if sender_balance != sender_balance_before:
                conn.execute(
                    "INSERT INTO balances (user_id, amount) VALUES (?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET amount = excluded.amount",
                    (str(self.sender.id), sender_balance),
                )
            if recipient_balance != recipient_balance_before:
                conn.execute(
                    "INSERT INTO balances (user_id, amount) VALUES (?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET amount = excluded.amount",
                    (str(self.recipient.id), recipient_balance),
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return {
            "sender_balance": sender_balance,
            "recipient_balance": recipient_balance,
            "sender_player_line": "\n".join(sender_player_lines) or None,
            "recipient_player_line": "\n".join(recipient_player_lines) or None,
            "sender_cash_line": "\n".join(sender_cash_lines) or None,
            "recipient_cash_line": "\n".join(recipient_cash_lines) or None,
        }


class TransferApprovalView(discord.ui.View):
    def __init__(self, proposal: TransferProposal, side: str):
        super().__init__(timeout=900)
        self.proposal = proposal
        self.side = side
        self.message: Optional[discord.Message] = None
        self.trade_type: Optional[str] = proposal.recipient_trade_type if side == "recipient" else None
        self.selected_player_id: Optional[int] = proposal.recipient_player_id if side == "recipient" else None
        self.selected_player_name: Optional[str] = proposal.recipient_player_name if side == "recipient" else None
        self.selected_player_position: Optional[str] = proposal.recipient_player_position if side == "recipient" else None
        self.cash_amount: Optional[int] = proposal.recipient_cash_amount if side == "recipient" else None
        self.current_page = 0
        self.players = []
        self.player_select: Optional[TransferPlayerSelect] = None
        if self.side == "recipient":
            conn = get_db()
            try:
                self.players = get_squad_players(conn, str(self.target_user.id))
            finally:
                conn.close()
            self.refresh_player_select()
        self._sync_buttons()

    @property
    def target_user(self) -> discord.Member:
        return self.proposal.sender if self.side == "sender" else self.proposal.recipient

    def _ready(self) -> bool:
        if self.side == "sender":
            return self.proposal.side_ready("sender")
        if not self.trade_type:
            return False
        if self.trade_type in {"player", "player_cash"} and not self.selected_player_id:
            return False
        if self.trade_type in {"cash", "player_cash"} and not self.cash_amount:
            return False
        return True

    def _pages(self) -> int:
        return max(1, (len(self.players) + 24) // 25)

    def refresh_player_select(self):
        if self.player_select:
            try:
                self.remove_item(self.player_select)
            except ValueError:
                pass
            self.player_select = None

        if self.side == "recipient" and self.trade_type in {"player", "player_cash"} and self.players:
            self.player_select = TransferPlayerSelect(self, self.players, self.current_page)
            self.add_item(self.player_select)

    def _sync_buttons(self):
        sender_locked = self.side == "sender"
        pages = self._pages()
        player_mode = self.trade_type in {"player", "player_cash"}
        self.prev_page.disabled = sender_locked or not (player_mode and pages > 1 and self.current_page > 0)
        self.next_page.disabled = sender_locked or not (player_mode and pages > 1 and self.current_page < pages - 1)
        self.enter_cash.disabled = sender_locked or self.trade_type not in {"cash", "player_cash"}
        self.deal_type.disabled = sender_locked
        self.approve.disabled = not self._ready()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target_user.id:
            await interaction.response.send_message(
                "Only the intended user can approve this DM.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        await self.proposal.mark_rejected("The transfer proposal timed out before both sides approved.")

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.side == "recipient":
            self.proposal.recipient_trade_type = self.trade_type
            self.proposal.recipient_player_id = self.selected_player_id
            self.proposal.recipient_player_name = self.selected_player_name
            self.proposal.recipient_player_position = self.selected_player_position
            self.proposal.recipient_cash_amount = self.cash_amount
            if not self.proposal.side_ready("recipient"):
                return await interaction.response.send_message(
                    "Fill in your side of the deal before approving.",
                    ephemeral=True,
                )
        await interaction.response.defer()
        finished = await self.proposal.mark_approval(self.side)
        for item in self.children:
            item.disabled = True
        if interaction.message:
            try:
                await interaction.message.edit(embed=self.proposal.approved_embed(self.side), view=self)
            except discord.HTTPException:
                pass
        if finished:
            await self.proposal.finalize()

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.proposal.mark_rejected(f"{interaction.user.display_name} rejected the deal.")

    def preview_embed(self) -> discord.Embed:
        if self.side == "sender":
            return self.proposal.proposal_embed("sender")

        embed = discord.Embed(
            title="🤝 Transfer Proposal",
            description=(
                "Set your side of the deal, then approve if everything looks right.\n"
                "Your offer is mandatory before the deal can continue."
            ),
            color=0x3498DB,
        )
        embed.add_field(name="Sender Offer", value=self.proposal.side_summary("sender"), inline=False)
        embed.add_field(name="Your Offer", value=self._preview_offer(), inline=False)
        embed.add_field(name="Your Side", value=self.target_user.display_name, inline=True)
        embed.add_field(name="Other Side", value=self.proposal.sender.display_name, inline=True)
        if self.trade_type in {"player", "player_cash"} and self.players:
            total_pages = self._pages()
            if total_pages > 1:
                embed.set_footer(text=f"Player list page {self.current_page + 1}/{total_pages}")
            else:
                embed.set_footer(text="Player list loaded from your squad")
        else:
            embed.set_footer(text="Choose your offer type to continue")
        return embed

    def _preview_offer(self) -> str:
        if not self.trade_type:
            return "Pending configuration"
        pieces = [f"**Type:** {_trade_label(self.trade_type)}"]
        if self.selected_player_name:
            suffix = f" ({self.selected_player_position})" if self.selected_player_position else ""
            pieces.append(f"**Player:** {self.selected_player_name}{suffix}")
        if self.cash_amount is not None:
            pieces.append(f"**Cash:** {fmt(self.cash_amount)} from {self.target_user.display_name} to {self.proposal.sender.display_name}")
        return "\n".join(pieces)

    @discord.ui.select(
        placeholder="Choose your deal type",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Player Only", value="player", description="Send one player with no cash attached"),
            discord.SelectOption(label="Player + Cash", value="player_cash", description="Send a player plus cash"),
            discord.SelectOption(label="Cash Only", value="cash", description="Send cash only"),
        ],
    )
    async def deal_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        if self.side != "recipient":
            return await interaction.response.defer()
        self.trade_type = select.values[0]
        if self.trade_type == "cash":
            self.selected_player_id = None
            self.selected_player_name = None
            self.selected_player_position = None
        if self.trade_type == "player":
            self.cash_amount = None
        self.current_page = 0
        await interaction.response.defer()
        await self.refresh_message()

    @discord.ui.button(label="Enter Cash", style=discord.ButtonStyle.secondary)
    async def enter_cash(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.side != "recipient":
            return await interaction.response.defer()
        if self.trade_type not in {"cash", "player_cash"}:
            return await interaction.response.send_message(
                "Choose a deal type that uses cash first.",
                ephemeral=True,
            )
        await interaction.response.send_modal(TransferAmountModal(self, "cash_amount"))

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.side != "recipient":
            return await interaction.response.defer()
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            self.selected_player_id = None
            self.selected_player_name = None
            self.selected_player_position = None
            await self.refresh_message()

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.side != "recipient":
            return await interaction.response.defer()
        await interaction.response.defer()
        if self.current_page < self._pages() - 1:
            self.current_page += 1
            self.selected_player_id = None
            self.selected_player_name = None
            self.selected_player_position = None
            await self.refresh_message()

    async def refresh_message(self):
        self.refresh_player_select()
        self._sync_buttons()
        if self.message:
            try:
                await self.message.edit(embed=self.preview_embed(), view=self)
            except discord.HTTPException:
                pass


class TransferSetupView(discord.ui.View):
    def __init__(self, bot: commands.Bot, sender: discord.Member, recipient: discord.Member):
        super().__init__(timeout=900)
        self.bot = bot
        self.sender = sender
        self.recipient = recipient
        self.trade_type: Optional[str] = None
        self.selected_player_id: Optional[int] = None
        self.selected_player_name: Optional[str] = None
        self.selected_player_position: Optional[str] = None
        self.cash_amount: Optional[int] = None
        self.current_page = 0
        self.message: Optional[discord.Message] = None
        self.players = []
        self.player_select: Optional[TransferPlayerSelect] = None
        self._load_players()
        self.refresh_player_select()
        self._sync_buttons()

    def _load_players(self):
        conn = get_db()
        try:
            self.players = get_squad_players(conn, str(self.sender.id))
        finally:
            conn.close()

    def _pages(self) -> int:
        return max(1, (len(self.players) + 24) // 25)

    def _ready(self) -> bool:
        if not self.trade_type:
            return False
        if self.trade_type in {"player", "player_cash"} and not self.selected_player_id:
            return False
        if self.trade_type in {"cash", "player_cash"} and not self.cash_amount:
            return False
        return True

    def refresh_player_select(self):
        if self.player_select:
            try:
                self.remove_item(self.player_select)
            except ValueError:
                pass
            self.player_select = None

        if self.trade_type in {"player", "player_cash"} and self.players:
            self.player_select = TransferPlayerSelect(self, self.players, self.current_page)
            self.add_item(self.player_select)

    def _sync_buttons(self):
        pages = self._pages()
        player_mode = self.trade_type in {"player", "player_cash"}
        self.prev_page.disabled = not (player_mode and pages > 1 and self.current_page > 0)
        self.next_page.disabled = not (player_mode and pages > 1 and self.current_page < pages - 1)
        self.enter_cash.disabled = self.trade_type not in {"cash", "player_cash"}
        self.send_proposal.disabled = not self._ready()

    async def refresh_message(self):
        self.refresh_player_select()
        self._sync_buttons()
        if self.message:
            try:
                await self.message.edit(embed=self.preview_embed(), view=self)
            except discord.HTTPException:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.sender.id:
            await interaction.response.send_message(
                "Only the command user can configure this transfer.",
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

    def preview_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🔁 Build Transfer Proposal",
            description=(
                f"Choose the trade type, then fill in the required details.\n"
                f"The proposal will be sent to {self.recipient.mention} and both sides must approve in DMs."
            ),
            color=0x3498DB,
        )
        embed.add_field(name="Recipient", value=self.recipient.display_name, inline=True)
        embed.add_field(name="Deal Type", value=_trade_label(self.trade_type or ""), inline=True)
        embed.add_field(name="Flow", value=_trade_flow(self.trade_type or ""), inline=False)

        if self.selected_player_name:
            player_line = self.selected_player_name
            if self.selected_player_position:
                player_line += f" ({self.selected_player_position})"
            embed.add_field(name="Selected Player", value=player_line, inline=False)

        if self.cash_amount is not None:
            cash_text = f"{fmt(self.cash_amount)} from {self.sender.display_name} to {self.recipient.display_name}"
            embed.add_field(name="Cash", value=cash_text, inline=False)

        if self.trade_type in {"player", "player_cash"}:
            total_pages = self._pages()
            if total_pages > 1:
                embed.set_footer(text=f"Player list page {self.current_page + 1}/{total_pages}")
            else:
                embed.set_footer(text="Player list loaded from your squad")
        else:
            embed.set_footer(text="Use the buttons below to finish the proposal")

        return embed

    @discord.ui.select(
        placeholder="Choose deal type",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Player Only", value="player", description="Move one player with no cash attached"),
            discord.SelectOption(label="Player + Cash", value="player_cash", description="One player plus cash from the other side"),
            discord.SelectOption(label="Cash Only", value="cash", description="Cash-only deal with no player"),
        ],
    )
    async def deal_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.trade_type = select.values[0]
        if self.trade_type == "cash":
            self.selected_player_id = None
            self.selected_player_name = None
            self.selected_player_position = None
        if self.trade_type == "player":
            self.cash_amount = None
        self.current_page = 0
        self.refresh_player_select()
        self._sync_buttons()
        await interaction.response.defer()
        await self.refresh_message()

    @discord.ui.button(label="Enter Cash", style=discord.ButtonStyle.secondary)
    async def enter_cash(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.trade_type not in {"cash", "player_cash"}:
            return await interaction.response.send_message(
                "Choose a deal type that uses cash first.",
                ephemeral=True,
            )
        await interaction.response.send_modal(TransferAmountModal(self, "cash_amount"))

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            self.selected_player_id = None
            self.selected_player_name = None
            self.selected_player_position = None
            await self.refresh_message()

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.current_page < self._pages() - 1:
            self.current_page += 1
            self.selected_player_id = None
            self.selected_player_name = None
            self.selected_player_position = None
            await self.refresh_message()

    @discord.ui.button(label="Send Proposal", style=discord.ButtonStyle.success)
    async def send_proposal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not self._ready():
            return await interaction.followup.send(
                embed=error_embed("Fill in every required field before sending the proposal."),
            )

        # Use the sender's guild (command originated in server) so sold-channel posts work
        proposal = TransferProposal(
            bot=self.bot,
            guild=self.sender.guild,
            sender=self.sender,
            recipient=self.recipient,
            sender_trade_type=self.trade_type or "",
            sender_player_id=self.selected_player_id,
            sender_player_name=self.selected_player_name,
            sender_player_position=self.selected_player_position,
            sender_cash_amount=self.cash_amount,
        )

        try:
            await proposal.send_proposal()
        except RuntimeError as exc:
            return await interaction.followup.send(embed=error_embed(str(exc)))

        for item in self.children:
            item.disabled = True

        if self.message:
            try:
                await self.message.edit(
                    embed=success_embed(
                        title="Proposal Sent",
                        description=f"Transfer proposal sent to {self.recipient.mention} in DMs.",
                        fields=[("Deal Type", _trade_label(self.trade_type or ""), True)],
                        footer="FM26 Sims",
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=warn_embed("Transfer setup cancelled."), view=self)


class TransferDeals(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="transfer")
    async def transfer(self, ctx: commands.Context, member: discord.Member):
        if ctx.guild is None:
            return await ctx.reply(embed=error_embed("This command only works in a server."), mention_author=False)
        if member.id == ctx.author.id:
            return await ctx.reply(embed=warn_embed("⚠️ You cannot transfer with yourself."), mention_author=False)
        if member.bot:
            return await ctx.reply(embed=warn_embed("⚠️ You cannot transfer with a bot."), mention_author=False)

        view = TransferSetupView(self.bot, ctx.author, member)
        try:
            msg = await ctx.author.send(embed=view.preview_embed(), view=view)
            view.message = msg
        except discord.Forbidden:
            return await ctx.reply(
                embed=error_embed("I could not DM you. Please enable DMs to set up a transfer."),
                mention_author=False,
            )

        await ctx.reply(
            embed=success_embed(
                title="Transfer Builder Sent",
                description="Check your DMs to configure the deal.",
                fields=[("Recipient", member.display_name, True)],
                footer="FM26 Sims",
            ),
            mention_author=False,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TransferDeals(bot))