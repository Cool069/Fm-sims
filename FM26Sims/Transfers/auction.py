import asyncio
import re
import os
import random

# Hardcoded NVIDIA NIM endpoint and API key (intentionally hardcoded per user request)
NIM_API_URL = "https://integrate.api.nvidia.com/v1"
NIM_API_KEY = "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"
from typing import Optional
import aiohttp
import discord
from discord.ext import commands

from Squads.addplayer import get_player_owner, is_admin, rebuild_raw_squad
from Squads.db import get_db, error_embed, success_embed, warn_embed, get_setting, set_setting
from Economy.bal import get_balance
from Economy.balutils import parse_amount, fmt


async def fetch_fmscout(player_name: str) -> dict:
    """Attempt to fetch estimated market value & potential from FMScout.

    This is a best-effort scraper using the player name; if it fails,
    returns a conservative fallback.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", player_name.lower())
    url = f"https://www.fmscout.com/player/{slug}.html"
    result = {"market": None, "potential": None, "source": url}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return result
                text = await resp.text()
    except Exception:
        return result

    # Try to find values like "Market value: £5,000,000" or "Market value" nearby
    m_market = re.search(r"Market value[:\s<\/>]*\£?([\d,\.kmmbKMB]+)", text)
    if m_market:
        raw = m_market.group(1)
        try:
            # convert plain numeric with commas
            cleaned = raw.replace(",", "").lower()
            if cleaned.endswith("k"):
                val = int(float(cleaned[:-1]) * 1_000)
            elif cleaned.endswith("m"):
                val = int(float(cleaned[:-1]) * 1_000_000)
            elif cleaned.endswith("b"):
                val = int(float(cleaned[:-1]) * 1_000_000_000)
            else:
                val = int(float(cleaned))
            result["market"] = val
        except Exception:
            pass

    m_pot = re.search(r"Potential[:\s<\/>]*([0-9]{1,3})", text)
    if m_pot:
        try:
            result["potential"] = int(m_pot.group(1))
        except Exception:
            pass

    return result


def predict_price(market: int | None, potential: int | None) -> int:
    """Naive predictor that adjusts market value by current time of year and potential."""
    # base value
    base = market or 500_000

    # potential premium: each potential point above 50 adds 2% up to +100%
    premium = 0.0
    if potential:
        premium = max(0, min(50, potential - 50)) * 0.02

    # seasonal multiplier (July start of season): months Jul-Nov = market rising, Feb-May = stable/decline
    from datetime import datetime
    m = datetime.utcnow().month
    if 7 <= m <= 11:
        season_mult = 1.10  # early season hype
    elif 12 <= m <= 1 or m == 6:
        season_mult = 1.00
    else:
        season_mult = 0.95

    predicted = int(base * (1 + premium) * season_mult)
    # round to nearest 1000
    return max(1_000, int(round(predicted / 1000) * 1000))


async def nim_predict_price(market: int | None, potential: int | None) -> int | None:
    """Call an external NVIDIA NIM endpoint if configured.

    Expects two environment variables:
      - NIM_API_URL: full URL to the NIM inference endpoint
      - NIM_API_KEY: API key (Bearer)

    The function sends a JSON payload and expects a JSON response containing
    a numeric `predicted_price` field. Any failure returns None.
    """
    # Use hardcoded values rather than environment variables
    url = NIM_API_URL
    key = NIM_API_KEY
    if not url or not key:
        return None

    # Optional: allow comma-separated model names in NIM_MODELS; pick one at random
    models_env = os.getenv("NIM_MODELS")
    model = None
    if models_env:
        models = [m.strip() for m in models_env.split(",") if m.strip()]
        if models:
            model = random.choice(models)

    payload = {
        "features": {
            "market": market or 0,
            "potential": potential or 0,
            "utc_month": __import__("datetime").datetime.utcnow().month,
        }
    }

    if model:
        payload["model"] = model

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception:
        return None

    val = data.get("predicted_price") or data.get("predicted") or data.get("predictedPrice")
    try:
        if val is None:
            return None
        return int(val)
    except Exception:
        return None


async def nim_chat_analyze(player_name: str, season: str, model: str = "qwen/qwen3.5-397b-a17b") -> str | None:
    """Call NIM chat/completions to get a short analysis (market, potential, suggested sale price).

    Returns the assistant text on success, otherwise None.
    """
    url = NIM_API_URL.rstrip("/") + "/chat/completions"
    key = "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"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    prompt = (
        f"Provide a concise scouting-style analysis for the player '{player_name}' in season {season}. "
        "Include (1) estimated current market value in GBP, (2) potential (0-100), and (3) a suggested sale price for an in-game auction. "
        "Return results as short labeled lines, e.g. 'Market value: £1.2m', 'Potential: 78', 'Suggested sale price: £900k'."
    )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "temperature": 0.6,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception:
        return None

    # Extract assistant content
    try:
        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = message.get("content") or choices[0].get("text")
        if not content:
            return None
        return content.strip()
    except Exception:
        return None


def _extract_amount_from_line(line: str) -> int | None:
    # Find amount patterns like '£1.2m', '1.2m', '1200000'
    m = re.search(r"£?\s*([0-9,.]+)\s*([kKmMbB])?", line)
    if not m:
        return None
    num = m.group(1).replace(",", "")
    suffix = (m.group(2) or "").lower()
    try:
        val = float(num)
        if suffix == "k":
            val = int(val * 1_000)
        elif suffix == "m":
            val = int(val * 1_000_000)
        elif suffix == "b":
            val = int(val * 1_000_000_000)
        else:
            val = int(val)
        return int(val)
    except Exception:
        return None


def parse_nim_analysis(text: str) -> tuple[int | None, int | None, int | None]:
    """Try to extract (market, potential, suggested_price) from NIM assistant text."""
    market = None
    potential = None
    suggested = None
    for line in text.splitlines():
        l = line.lower()
        if "market" in l and market is None:
            market = _extract_amount_from_line(line)
            continue
        if "potential" in l and potential is None:
            m = re.search(r"(\d{1,2})", line)
            if m:
                try:
                    potential = int(m.group(1))
                except Exception:
                    potential = None
            continue
        if ("suggest" in l or "sale" in l or "suggested" in l or "price" in l) and suggested is None:
            suggested = _extract_amount_from_line(line)

    return market, potential, suggested


POSITION_GROUP_ALIASES = {
    "gk": "GK",
    "goalkeeper": "GK",
    "goalkeepers": "GK",
    "def": "DEF",
    "defender": "DEF",
    "defenders": "DEF",
    "cb": "DEF",
    "cb/lb": "DEF",
    "cb/rb": "DEF",
    "lb": "DEF",
    "rb": "DEF",
    "mid": "MID",
    "midfielder": "MID",
    "midfielders": "MID",
    "cm": "MID",
    "cdm": "MID",
    "cam": "MID",
    "att": "FWD",
    "attacker": "FWD",
    "attackers": "FWD",
    "fwd": "FWD",
    "forward": "FWD",
    "forwards": "FWD",
    "lw": "FWD",
    "rw": "FWD",
    "st": "FWD",
    "cf": "FWD",
}


POSITION_GROUP_LABELS = {
    "GK": "Goalkeeper",
    "DEF": "Defender",
    "MID": "Midfielder",
    "FWD": "Attacker",
}


def normalize_position_group(value: str | None) -> str | None:
    if not value:
        return None
    return POSITION_GROUP_ALIASES.get(value.strip().lower())


def format_position_group(value: str | None) -> str | None:
    if not value:
        return None
    return POSITION_GROUP_LABELS.get(value, value)


def parse_buyplayer_request(args: tuple[str, ...]) -> tuple[str, str | None, int | None]:
    """Parse `.buyplayer <name> <role> <amount>` if supplied, otherwise return the player name only."""
    if not args:
        return "", None, None

    tokens = [token.strip() for token in args if token.strip()]
    if len(tokens) >= 3:
        manual_amount: int | None = None
        try:
            manual_amount = parse_amount(tokens[-1])
        except Exception:
            manual_amount = None

        manual_position = normalize_position_group(tokens[-2])
        if manual_amount is not None and manual_position:
            player_name = " ".join(tokens[:-2]).strip()
            return player_name, manual_position, manual_amount

    return " ".join(tokens).strip(), None, None


async def predict_price(market: int | None, potential: int | None) -> int:
    """Wrap NIM predictor with fallback to local heuristic."""
    nim_val = await nim_predict_price(market, potential)
    if nim_val:
        # round to nearest 1k for presentation
        return max(1_000, int(round(nim_val / 1000) * 1000))
    # fallback to local heuristic
    return _predict_price_local(market, potential)


def _predict_price_local(market: int | None, potential: int | None) -> int:
    # original synchronous logic moved here
    base = market or 500_000
    premium = 0.0
    if potential:
        premium = max(0, min(50, potential - 50)) * 0.02
    from datetime import datetime
    m = datetime.utcnow().month
    if 7 <= m <= 11:
        season_mult = 1.10
    elif 12 <= m <= 1 or m == 6:
        season_mult = 1.00
    else:
        season_mult = 0.95
    predicted = int(base * (1 + premium) * season_mult)
    return max(1_000, int(round(predicted / 1000) * 1000))


class EditPriceModal(discord.ui.Modal):
    def __init__(self, start_price: int, callback):
        super().__init__(title="Edit Sale Price")
        self.start_price = start_price
        self.callback = callback
        self.price = discord.ui.TextInput(label="New sale price (e.g. 1.5m, 500k)", placeholder=str(start_price))
        self.add_item(self.price)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = parse_amount(self.price.value)
        except Exception:
            return await interaction.response.send_message(embed=error_embed("Invalid amount format."), ephemeral=True)
        await self.callback(interaction, amount)


class ApproveAuctionModal(discord.ui.Modal):
    def __init__(self, start_price: int, callback):
        super().__init__(title="Approve Auction")
        self.start_price = start_price
        self.callback = callback
        self.price = discord.ui.TextInput(
            label="Starting price",
            placeholder=str(start_price),
            required=True,
        )
        self.position = discord.ui.TextInput(
            label="Position",
            placeholder="Goalkeeper, Defender, Midfielder, Attacker",
            required=True,
            max_length=32,
        )
        self.add_item(self.price)
        self.add_item(self.position)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = parse_amount(self.price.value)
        except Exception:
            return await interaction.response.send_message(embed=error_embed("Invalid amount format."), ephemeral=True)

        position = self.position.value.strip()
        if not position:
            return await interaction.response.send_message(embed=error_embed("Please provide a position."), ephemeral=True)

        await self.callback(interaction, amount, position)


class AuctionView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        guild: discord.Guild,
        channel: discord.TextChannel,
        player_name: str,
        starting_price: int,
        position: str | None = None,
        timeout_seconds: int = 1800,
        sold_channel_id: Optional[int] = None,
        on_finish=None,
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.player_name = player_name
        self.starting_price = starting_price
        self.position = position
        self.timeout_seconds = timeout_seconds
        self.sold_channel_id = sold_channel_id
        self.on_finish = on_finish
        self.highest_bid = starting_price
        self.highest_bidder = None
        self.bids = {}  # user_id -> amount
        self.message: discord.Message | None = None
        self._auction_task = None

    async def start(self):
        embed = discord.Embed(title=f"Auction: {self.player_name}", color=0xF1C40F)
        embed.add_field(name="Starting Price", value=fmt(self.starting_price), inline=True)
        embed.add_field(name="Highest Bid", value=fmt(self.highest_bid), inline=True)
        if self.position:
            embed.add_field(name="Position", value=self.position, inline=True)
        embed.add_field(name="How to Bid", value="Use `.bid <amount>` in this channel.", inline=False)
        embed.set_footer(text=f"Auction duration: {self.timeout_seconds // 60} minutes")
        self.message = await self.channel.send(embed=embed)
        self._auction_task = asyncio.create_task(self._run_auction())

    async def _run_auction(self):
        await asyncio.sleep(self.timeout_seconds)
        await self.finish_auction()

    async def finish_auction(self):
        if self.on_finish:
            try:
                self.on_finish(self.channel.id)
            except Exception:
                pass

        if not self.highest_bidder:
            await self.channel.send(embed=warn_embed(f"No bids placed for **{self.player_name}**. Auction closed."))
            return

        winner = self.guild.get_member(self.highest_bidder)
        if not winner:
            await self.channel.send(embed=error_embed("Winner left the guild; refunding and cancelling."))
            return

        conn = get_db()
        try:
            row = conn.execute("SELECT amount FROM balances WHERE user_id = ?", (str(self.highest_bidder),)).fetchone()
            balance = row[0] if row else 0
            if balance < self.highest_bid:
                await self.channel.send(embed=error_embed(f"Winner {winner.mention} has insufficient funds. Auction cancelled."))
                return

            # Deduct money
            conn.execute("INSERT INTO balances (user_id, amount) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET amount = amount - excluded.amount", (str(self.highest_bidder), self.highest_bid))
            # Assign player
            conn.execute(
                "INSERT INTO squad (user_id, player_name, position) VALUES (?, ?, ?)",
                (str(self.highest_bidder), self.player_name, self.position),
            )

            # Refresh squad_meta.raw_squad so `.squad` immediately shows the new player
            raw_squad = rebuild_raw_squad(conn, str(self.highest_bidder))
            manager_row = conn.execute(
                "SELECT manager, logo_url FROM squad_meta WHERE user_id = ?",
                (str(self.highest_bidder),),
            ).fetchone()
            manager = manager_row[0] if manager_row else None
            logo_url = manager_row[1] if manager_row else None
            conn.execute(
                "INSERT INTO squad_meta (user_id, manager, raw_squad, logo_url) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET manager = excluded.manager, raw_squad = excluded.raw_squad, logo_url = excluded.logo_url",
                (str(self.highest_bidder), manager, raw_squad, logo_url),
            )
            conn.commit()
        finally:
            conn.close()

        sold_embed = success_embed(
            title="Player Sold",
            description=f"**{self.player_name}** sold to {winner.mention} for **{fmt(self.highest_bid)}**.",
            fields=[("Buyer", winner.display_name, True), ("Amount", fmt(self.highest_bid), True)],
            footer="FM26 Sims"
        )
        await self.channel.send(embed=sold_embed)

        if self.sold_channel_id:
            sold_channel = self.guild.get_channel(self.sold_channel_id)
            if isinstance(sold_channel, discord.TextChannel) and sold_channel.id != self.channel.id:
                log_embed = success_embed(
                    title="Sold Log",
                    description=(
                        f"**{self.player_name}** sold to {winner.mention} for **{fmt(self.highest_bid)}**."
                        f"\nAuction Channel: {self.channel.mention}"
                    ),
                    fields=[("Buyer", winner.display_name, True), ("Amount", fmt(self.highest_bid), True)],
                    footer="FM26 Sims",
                )
                await sold_channel.send(embed=log_embed)

    async def place_bid_from_command(self, bidder: discord.Member, amount: int) -> tuple[bool, str]:
        min_required = max(self.starting_price, self.highest_bid + 1_000)
        bal = get_balance(str(bidder.id))

        if amount < min_required:
            return False, f"Bid must be at least {fmt(min_required)}."
        if bal < amount:
            return False, "Insufficient funds for this bid."

        self.bids[bidder.id] = amount
        self.highest_bid = amount
        self.highest_bidder = bidder.id

        if self.message:
            embed = discord.Embed(title=f"Auction: {self.player_name}", color=0xF1C40F)
            embed.add_field(name="Starting Price", value=fmt(self.starting_price), inline=True)
            embed.add_field(name="Highest Bid", value=fmt(self.highest_bid), inline=True)
            embed.add_field(name="How to Bid", value="Use `.bid <amount>` in this channel.", inline=False)
            embed.set_footer(text=f"Auction duration: {self.timeout_seconds // 60} minutes")
            await self.message.edit(embed=embed)

            # Announce publicly who the current highest bidder is
            try:
                # Try to use the bidder's club logo if set, otherwise fall back to Discord avatar
                logo_url = None
                try:
                    conn = get_db()
                    row = conn.execute("SELECT logo_url FROM squad_meta WHERE user_id = ?", (str(bidder.id),)).fetchone()
                    logo_url = row[0] if row and row[0] else None
                except Exception:
                    logo_url = None
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

                embed = success_embed(
                    title="Highest Bidder",
                    description=f"Highest bidder is {bidder.mention} for **{fmt(amount)}**.",
                    fields=[("Current Highest", fmt(self.highest_bid), True)],
                    footer="FM26 Sims",
                )

                # Prefer club logo, fall back to Discord avatar
                thumbnail_url = None
                if logo_url:
                    thumbnail_url = logo_url
                else:
                    try:
                        thumbnail_url = bidder.display_avatar.url
                    except Exception:
                        thumbnail_url = None

                if thumbnail_url:
                    try:
                        embed.set_thumbnail(url=thumbnail_url)
                    except Exception:
                        pass

                await self.channel.send(embed=embed)
            except Exception:
                pass

        return True, f"Bid accepted: **{fmt(amount)}** for **{self.player_name}**."


class ApproveView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        player_name: str,
        predicted_price: int,
        ctx_channel: discord.TextChannel,
        transfers_cog: "Transfers",
        manual_position: str | None = None,
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.player_name = player_name
        self.price = predicted_price
        self.ctx_channel = ctx_channel
        self.transfers_cog = transfers_cog
        self.manual_position = manual_position

    async def _start_auction(self, interaction: discord.Interaction, amount: int, position: str):
        sold_channel_id = get_setting("sold_channel_id")
        parsed_sold_channel_id = int(sold_channel_id) if sold_channel_id and sold_channel_id.isdigit() else None

        def _on_finish(channel_id: int):
            self.transfers_cog.active_auctions.pop(channel_id, None)

        auction_view = AuctionView(
            self.bot,
            interaction.guild,
            self.ctx_channel,
            self.player_name,
            amount,
            position=position,
            sold_channel_id=parsed_sold_channel_id,
            on_finish=_on_finish,
        )
        self.transfers_cog.active_auctions[self.ctx_channel.id] = auction_view
        await auction_view.start()

        await interaction.response.send_message(
            embed=success_embed(
                title="Auction Approved",
                description=(
                    f"Auction for **{self.player_name}** approved at **{fmt(amount)}**.\n"
                    f"Position: **{format_position_group(position) or position}**\n"
                    "Starting now."
                ),
                fields=[("Starting Price", fmt(amount), True), ("Position", format_position_group(position) or position, True)],
                footer="FM26 Sims",
            ),
            ephemeral=False,
        )

    @discord.ui.button(label="Approve Auction", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("⛔ Only admins can approve."), ephemeral=True)
        if self.manual_position:
            return await self._start_auction(interaction, self.price, self.manual_position)

        async def on_submit(i: discord.Interaction, amount: int, position: str):
            await self._start_auction(i, amount, position)

        await interaction.response.send_modal(ApproveAuctionModal(self.price, on_submit))

    @discord.ui.button(label="Edit Price", style=discord.ButtonStyle.secondary)
    async def edit_price(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("⛔ Only admins can edit."), ephemeral=True)

        async def after_edit(i: discord.Interaction, amount: int):
            self.price = amount
            await i.response.send_message(embed=success_embed(
                title="Sale Price Updated",
                description=f"Sale price set to **{fmt(self.price)}**.",
                fields=[("New Price", fmt(self.price), True)],
                footer="FM26 Sims"
            ), ephemeral=True)
            # update original message if present
            try:
                await i.message.edit(embed=discord.Embed(title=f"Approve Auction: {self.player_name}", description=f"Proposed price: {fmt(self.price)}", color=0x95A5A6), view=self)
            except Exception:
                pass

        modal = EditPriceModal(self.price, after_edit)
        await interaction.response.send_modal(modal)


class Transfers(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_auctions: dict[int, AuctionView] = {}

    @commands.command(name="buyplayer")
    async def buyplayer(self, ctx: commands.Context, *args):
        """Start a purchase / auction flow for a free agent."""
        player_name, manual_position, manual_price = parse_buyplayer_request(args)
        if not player_name:
            return await ctx.reply(embed=error_embed("Provide the player's full name."), mention_author=False)

        if manual_position and not manual_price:
            return await ctx.reply(
                embed=error_embed(
                    "If you provide a position, also provide a starting price.\n"
                    "Examples: `.buyplayer BenNelson CB 32m` or `.buyplayer Ben Nelson Defender 32m`\n"
                    "CB/LB/RB will be stored as **Defender**."
                ),
                mention_author=False,
            )

        conn = get_db()
        try:
            owner = get_player_owner(conn, player_name)
        finally:
            conn.close()

        if owner:
            return await ctx.reply(embed=warn_embed(f"**{player_name}** is already owned."), mention_author=False)

        if ctx.channel.id in self.active_auctions:
            return await ctx.reply(
                embed=warn_embed("An auction is already active in this channel. Finish it before starting another."),
                mention_author=False,
            )

        fetch = await fetch_fmscout(player_name)
        market = fetch.get("market")
        pot = fetch.get("potential")

        season = get_setting("season_year", "Unknown")

        nim_text = None
        nim_market, nim_pot, nim_suggested = (None, None, None)

        if manual_price is None:
            # Call NIM chat to obtain a short analysis (market/potential/suggested price)
            nim_text = await nim_chat_analyze(player_name, season)
            if nim_text:
                nim_market, nim_pot, nim_suggested = parse_nim_analysis(nim_text)

            # Prefer numeric NIM suggestion if available, otherwise fallback to earlier predictors
            if nim_suggested:
                predicted = max(1_000, int(round(nim_suggested / 1000) * 1000))
                ai_source = "NIM(chat)"
            else:
                nim_val = await nim_predict_price(market, pot)
                if nim_val:
                    predicted = max(1_000, int(round(nim_val / 1000) * 1000))
                    ai_source = "NIM(model)"
                else:
                    predicted = _predict_price_local(market, pot)
                    ai_source = "heuristic"
        else:
            predicted = manual_price
            ai_source = "manual"

        desc_lines = []
        if market:
            desc_lines.append(f"Current Market Value: **{fmt(market)}**")
        if pot:
            desc_lines.append(f"Potential: **{pot}**")
        desc_lines.append(f"Starting Price: **{fmt(predicted)}** (source: {ai_source})")
        if nim_text:
            desc_lines.append("")
            desc_lines.append("NIM analysis:")
            for l in nim_text.splitlines():
                desc_lines.append(l)
        season = get_setting("season_year", "Unknown")
        desc_lines.append(f"Season: **{season}**")
        desc_lines.append("")
        if manual_position:
            desc_lines.append(f"Position: **{format_position_group(manual_position) or manual_position}**")

        desc_lines.append("Admins: approve the auction to begin.")

        embed = discord.Embed(title=f"Proposed Auction: {player_name}", description="\n".join(desc_lines), color=0x2ECC71)
        embed.set_footer(text="FM26 Sims")

        view = ApproveView(self.bot, player_name, predicted, ctx.channel, self, manual_position=manual_position)
        await ctx.reply(embed=embed, view=view, mention_author=False)

    @commands.command(name="bid")
    async def bid(self, ctx: commands.Context, amount: str):
        auction = self.active_auctions.get(ctx.channel.id)
        if not auction:
            return await ctx.reply(embed=warn_embed("No active auction in this channel."), mention_author=False)

        try:
            bid_amount = parse_amount(amount)
        except Exception:
            return await ctx.reply(embed=error_embed("Invalid amount format. Example: `.bid 1.5m`"), mention_author=False)

        ok, message = await auction.place_bid_from_command(ctx.author, bid_amount)
        if not ok:
            return await ctx.reply(embed=error_embed(message), mention_author=False)

        await ctx.reply(
            embed=success_embed(
                title="Bid Placed",
                description=message,
                fields=[("Current Highest", fmt(auction.highest_bid), True)],
                footer="FM26 Sims",
            ),
            mention_author=False,
        )

    @commands.command(name="setsoldchannel", aliases=["ssc", "scc"])
    @commands.has_permissions(administrator=True)
    async def setsoldchannel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        target = channel or ctx.channel
        set_setting("sold_channel_id", str(target.id))
        await ctx.reply(
            embed=success_embed(
                title="Sold Log Channel Updated",
                description=f"Sold logs will be posted in {target.mention}.",
                fields=[("Channel", target.mention, True)],
                footer="FM26 Sims",
            ),
            mention_author=False,
        )

    @commands.command(name="endsale")
    @commands.has_permissions(administrator=True)
    async def endsale(self, ctx: commands.Context):
        """Admin command: end the active auction in this channel immediately."""
        auction = self.active_auctions.pop(ctx.channel.id, None)
        if not auction:
            return await ctx.reply(embed=warn_embed("No active auction in this channel."), mention_author=False)

        # Cancel background task if running
        task = getattr(auction, "_auction_task", None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        try:
            await auction.finish_auction()
        except Exception as e:
            return await ctx.reply(embed=error_embed(f"Failed to end auction: {e}"), mention_author=False)

        await ctx.reply(
            embed=success_embed(
                title="Auction Ended",
                description=f"Auction for **{auction.player_name}** ended by moderator.",
                fields=[("Ended by", ctx.author.display_name, True)],
                footer="FM26 Sims",
            ),
            mention_author=False,
        )

    @commands.command(name="setseason")
    @commands.has_permissions(administrator=True)
    async def setseason(self, ctx: commands.Context, *, season: str):
        """Admin command: set current season year (e.g. 2026/27)."""
        set_setting("season_year", season)
        await ctx.reply(embed=success_embed(
            title="Season Updated",
            description=f"Season set to **{season}**.",
            fields=[("Season", season, True)],
            footer="FM26 Sims"
        ), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Transfers(bot))
