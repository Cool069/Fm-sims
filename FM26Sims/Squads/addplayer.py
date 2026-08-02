import re
import sqlite3
import discord
from discord.ext import commands
from typing import List, Tuple, Optional

from Squads.db import get_db, error_embed, warn_embed, success_embed


# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


def squad_size(conn: sqlite3.Connection, user_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM squad WHERE user_id = ?", (user_id,)
    ).fetchone()[0]


def get_player_owner(conn: sqlite3.Connection, player_name: str):
    row = conn.execute(
        "SELECT user_id FROM squad WHERE player_name = ? COLLATE NOCASE",
        (player_name,)
    ).fetchone()
    return row[0] if row else None


def _section_for_position(position: Optional[str]) -> str:
    if not position:
        return "Players"

    normalized = position.upper().strip()
    if normalized == "GK":
        return "Goalkeepers"
    if normalized in {"DEF", "CB", "LB", "RB", "LWB", "RWB"} or "/" in normalized and any(
        part in {"CB", "LB", "RB", "LWB", "RWB"} for part in normalized.split("/")
    ):
        return "Defenders"
    if normalized in {"MID", "CM", "CDM", "CAM", "LM", "RM", "AM"} or "/" in normalized and any(
        part in {"CM", "CDM", "CAM", "LM", "RM", "AM"} for part in normalized.split("/")
    ):
        return "Midfielders"
    if normalized in {"FWD", "ST", "CF", "LW", "RW"} or "/" in normalized and any(
        part in {"ST", "CF", "LW", "RW"} for part in normalized.split("/")
    ):
        return "Attackers"
    return "Players"


def rebuild_raw_squad(conn: sqlite3.Connection, user_id: str) -> str:
    manager_row = conn.execute(
        "SELECT manager FROM squad_meta WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    manager = manager_row[0] if manager_row else None

    rows = conn.execute(
        "SELECT player_name, position, on_loan FROM squad WHERE user_id = ? ORDER BY added_at ASC, player_name COLLATE NOCASE",
        (user_id,)
    ).fetchall()

    if not rows:
        return ""

    grouped = {
        "Goalkeepers": [],
        "Defenders": [],
        "Midfielders": [],
        "Attackers": [],
        "Players": [],
    }

    for player_name, position, on_loan in rows:
        line = player_name
        if position and position.upper() not in {"GK", "DEF", "MID", "FWD"}:
            line = f"{line} ({position})"
        if on_loan:
            line = f"{line} (out on L)"
        grouped[_section_for_position(position)].append(line)

    lines = []
    if manager:
        lines.append(f"** MANAGER - {manager} **")
        lines.append("")

    for section_name in ("Goalkeepers", "Defenders", "Midfielders", "Attackers", "Players"):
        entries = grouped[section_name]
        if not entries:
            continue
        lines.append(f"** __ {section_name.upper()} __ **")
        lines.extend(entries)
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


def parse_args(ctx, args: tuple):
    if not args:
        raise ValueError("No arguments provided.")
    if ctx.message.mentions:
        if not is_admin(ctx.author):
            raise PermissionError("Only **Adminis** can manage another user's squad.")
        target      = ctx.message.mentions[0]
        player_name = " ".join(a for a in args if not a.startswith("<@")).strip()
        if not player_name:
            raise ValueError("Player name missing.")
        return str(target.id), player_name
    return str(ctx.author.id), " ".join(args).strip()


def parse_release_args(ctx, args: tuple) -> Tuple[str, str]:
    target_id = str(ctx.author.id)
    filtered_args = list(args)

    if ctx.message.mentions:
        if not is_admin(ctx.author):
            raise PermissionError("Only **Adminis** can manage another user's squad.")
        target = ctx.message.mentions[0]
        target_id = str(target.id)
        filtered_args = [a for a in args if not re.fullmatch(r"<@!?\d+>", a)]

    return target_id, " ".join(filtered_args).strip()


SINGLE_POSITION_MAP = {
    "gk": "GK",
    "goalkeeper": "GK",
    "goalkeepers": "GK",
    "def": "DEF",
    "defender": "DEF",
    "defenders": "DEF",
    "mid": "MID",
    "midfielder": "MID",
    "midfielders": "MID",
    "fwd": "FWD",
    "forward": "FWD",
    "forwards": "FWD",
    "att": "FWD",
    "attacker": "FWD",
    "attackers": "FWD",
}


def split_position_and_name(player_input: str) -> Tuple[Optional[str], str]:
    parts = player_input.strip().split()
    if not parts:
        return None, ""

    token = parts[0].lower().rstrip(":")
    position = SINGLE_POSITION_MAP.get(token)
    if position:
        return position, " ".join(parts[1:]).strip()
    return None, player_input.strip()


def get_squad_players(conn: sqlite3.Connection, user_id: str) -> List[Tuple[int, str, Optional[str], int]]:
    return conn.execute(
        "SELECT id, player_name, position, on_loan FROM squad WHERE user_id = ? ORDER BY player_name COLLATE NOCASE",
        (user_id,)
    ).fetchall()


# ─── Bulk Parser ──────────────────────────────────────────────────────────────

# Section headers to detect (with or without Discord bold/underline markdown)
POSITION_HEADERS = {
    "goalkeepers", "defenders", "midfielders", "forwards",
    "attackers", "wingers", "goalkeeper", "defender",
    "midfielder", "forward", "attacker"
}

# Strips Discord markdown: **, __, *, _
MARKDOWN_RE = re.compile(r"[*_]+")

# Strips trailing emojis and whitespace
EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FFFF"   # misc symbols & pictographs
    r"\U00002700-\U000027BF"    # dingbats
    r"\U0001F900-\U0001F9FF"    # supplemental symbols
    r"\U00002600-\U000026FF"    # misc symbols
    r"\u2640-\u2642"
    r"\uFE0F\u200D"
    r"]+",
    flags=re.UNICODE
)

# Loan patterns — covers:
#   (out on L)  (L to westham)  (on loan)  (loaned out)  (loan)
LOAN_RE = re.compile(
    r"\(\s*(?:out\s+on\s+L|L\s+to\s+\w[\w\s]*|on\s+loan|loan(?:ed\s+out)?)\s*\)",
    re.IGNORECASE
)

# Position tag pattern: (CB/LB), (RB), (ST/CAM), etc.
POSITION_TAG_RE = re.compile(r"\(([A-Z]{1,4}(?:/[A-Z]{1,4})*)\)")

# Manager line: "MANAGER - Name" or "MANAGER: Name"
MANAGER_RE = re.compile(r"^manager\s*[-:]\s*(.+)$", re.IGNORECASE)


def clean_line(raw: str) -> str:
    """Strip Discord markdown and trailing emojis from a line."""
    text = MARKDOWN_RE.sub("", raw).strip()
    text = EMOJI_RE.sub("", text).strip()
    return text


def parse_bulk_squad(text: str) -> dict:
    """
    Parse the full squad format:

        ** __ CHELSEA SQUAD __ **
        ** MANAGER - Liam Rosenior **
        ** __ GOALKEEPERS __ **
        Diogo Costa 🧤
        Filip Jorgensen 🧤
        Mike Penders 🧤 (out on L)
        ** __ DEFENDERS __ **
        Jorrel Hato (CB/LB)
        ...

    Returns:
        {
            "manager": "Liam Rosenior" | None,
            "players": [
                {
                    "name":     "Diogo Costa",
                    "position": "GK" | "CB/LB" | None,
                    "on_loan":  False | True,
                },
                ...
            ]
        }
    """
    result   = {"manager": None, "players": []}
    current_position_group = None  # "GK", "DEF", "MID", "FWD"

    POSITION_GROUP_MAP = {
        "goalkeepers": "GK",  "goalkeeper": "GK",
        "defenders":   "DEF", "defender":   "DEF",
        "midfielders": "MID", "midfielder": "MID",
        "forwards":    "FWD", "forward":    "FWD",
        "attackers":   "FWD", "attacker":   "FWD",
        "wingers":     "MID",
    }

    NON_PLAYER_LINES = {"image"}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Skip Discord role/user mentions
        if line.startswith("<@"):
            continue

        # Clean markdown + emojis for parsing
        cleaned = clean_line(line)
        if not cleaned:
            continue

        # ── Manager line ──
        m = MANAGER_RE.match(cleaned)
        if m:
            result["manager"] = m.group(1).strip()
            continue

        # ── Position header ──
        header_check = cleaned.rstrip(":").lower()
        if header_check in POSITION_HEADERS:
            current_position_group = POSITION_GROUP_MAP.get(header_check)
            continue

        # Skip generic title lines (e.g. "CHELSEA SQUAD")
        if re.match(r"^[A-Z\s]+SQUAD\s*$", cleaned, re.IGNORECASE):
            continue

        # ── Player line ──
        # Check for loan
        on_loan = bool(LOAN_RE.search(cleaned))

        # Remove loan tag
        name_part = LOAN_RE.sub("", cleaned).strip()

        # Extract position tag like (CB/LB)
        pos_match = POSITION_TAG_RE.search(name_part)
        position  = pos_match.group(1) if pos_match else current_position_group

        # Remove position tag from name
        name_part = POSITION_TAG_RE.sub("", name_part).strip()

        # Final cleanup — remove leftover brackets/punctuation
        name_part = re.sub(r"[\(\)]", "", name_part).strip()

        if (
            name_part
            and re.search(r"[A-Za-z]", name_part)
            and name_part.lower() not in NON_PLAYER_LINES
        ):
            result["players"].append({
                "name":     name_part,
                "position": position,
                "on_loan":  on_loan,
            })

    return result


# ─── Cog ──────────────────────────────────────────────────────────────────────

class AddPlayer(commands.Cog):
    """Handles adding and removing players from squads."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def resolve_reset_target(self, ctx: commands.Context, target_input: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if not target_input:
            return str(ctx.author.id), ctx.author.display_name

        user_input = target_input.strip()

        if user_input.isdigit():
            target_id = user_input
            member = ctx.guild.get_member(int(target_id)) if ctx.guild else None
            if member:
                return target_id, member.display_name
            if target_id == str(ctx.author.id):
                return target_id, ctx.author.display_name
            return target_id, f"<@{target_id}>"

        try:
            member = await commands.MemberConverter().convert(ctx, user_input)
            return str(member.id), member.display_name
        except commands.MemberNotFound:
            return None, None

    # ── .addplayer ────────────────────────────────────────────────────────────

    @commands.command(name="addplayer")
    async def add_player(self, ctx: commands.Context, *args):
        command_prefix = ctx.prefix + ctx.invoked_with
        body = ctx.message.content[len(command_prefix):].strip()

        if "\n" in body:
            await self._handle_bulk(ctx, body)
        else:
            await self._handle_single(ctx, args)

    # ── Single-player ─────────────────────────────────────────────────────────

    async def _handle_single(self, ctx: commands.Context, args: tuple):
        try:
            target_id, player_input = parse_args(ctx, args)
        except PermissionError as e:
            return await ctx.reply(embed=error_embed(f"⛔ {e}"), mention_author=False)
        except ValueError:
            embed = error_embed(
                "**Invalid usage.**\n\n"
                "`.addplayer <player name>` — add to your squad\n"
                "`.addplayer <goalkeeper|defender|midfielder|attacker> <player name>` — add with position\n"
                "`.addplayer @user <player name>` — admin: add to another user's squad\n"
                "`.append <goalkeeper|defender|midfielder|attacker> <player name>` — append with position\n\n"
                "**Bulk format** — paste a full squad list after the command."
            )
            embed.title = "Incorrect Usage"
            return await ctx.reply(embed=embed, mention_author=False)

        position, player_name = split_position_and_name(player_input)

        if len(player_name.split()) < 2:
            return await ctx.reply(
                embed=error_embed(
                    "Provide the player's **full name**.\n"
                    "Examples: `.addplayer Lionel Messi`, `.addplayer attacker Erling Haaland`"
                ),
                mention_author=False
            )

        await self._sign_player(ctx, target_id, player_name, position)

    @commands.command(name="append")
    async def append_player(self, ctx: commands.Context, *args):
        try:
            target_id, player_input = parse_args(ctx, args)
        except PermissionError as e:
            return await ctx.reply(embed=error_embed(f"⛔ {e}"), mention_author=False)
        except ValueError:
            return await ctx.reply(
                embed=error_embed(
                    "**Invalid usage.**\n\n"
                    "`.append <goalkeeper|defender|midfielder|attacker> <player name>`\n"
                    "`.append @user <goalkeeper|defender|midfielder|attacker> <player name>`"
                ),
                mention_author=False,
            )

        position, player_name = split_position_and_name(player_input)
        if not position:
            return await ctx.reply(
                embed=error_embed(
                    "Use a position group first: `goalkeeper`, `defender`, `midfielder`, or `attacker`."
                ),
                mention_author=False,
            )

        if len(player_name.split()) < 2:
            return await ctx.reply(
                embed=error_embed("Provide the player's **full name** after the position group."),
                mention_author=False,
            )

        await self._sign_player(ctx, target_id, player_name, position)

    # ── Bulk squad ────────────────────────────────────────────────────────────

    async def _handle_bulk(self, ctx: commands.Context, body: str):
        target_id = str(ctx.author.id)

        if ctx.message.mentions:
            if not is_admin(ctx.author):
                return await ctx.reply(
                    embed=error_embed("⛔ Only **Adminis** can mass-add to another user's squad."),
                    mention_author=False
                )
            target_id = str(ctx.message.mentions[0].id)

        parsed  = parse_bulk_squad(body)
        manager = parsed["manager"]
        players = parsed["players"]

        if not players and not manager:
            return await ctx.reply(
                embed=error_embed("No valid player names found in the squad list."),
                mention_author=False
            )

        conn = get_db()
        try:
            # Save manager + raw squad text to squad_meta
            conn.execute(
                "INSERT INTO squad_meta (user_id, manager, raw_squad) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET manager = excluded.manager, raw_squad = excluded.raw_squad",
                (target_id, manager, body)
            )

            signed  = []
            skipped = []
            removed = []

            for p in players:
                player_name = p["name"]
                position    = p["position"]
                on_loan     = 1 if p["on_loan"] else 0

                owner_id = get_player_owner(conn, player_name)
                if owner_id:
                    if owner_id == target_id:
                        # Update position/loan status even if already in squad
                        conn.execute(
                            "UPDATE squad SET position = ?, on_loan = ? "
                            "WHERE user_id = ? AND player_name = ? COLLATE NOCASE",
                            (position, on_loan, target_id, player_name)
                        )
                        skipped.append((player_name, "already in squad (updated)"))
                    else:
                        owner         = ctx.guild.get_member(int(owner_id))
                        owner_display = owner.display_name if owner else f"<@{owner_id}>"
                        skipped.append((player_name, f"owned by {owner_display}"))
                    continue

                try:
                    conn.execute(
                        "INSERT INTO squad (user_id, player_name, position, on_loan) VALUES (?, ?, ?, ?)",
                        (target_id, player_name, position, on_loan)
                    )
                    signed.append(player_name)
                except sqlite3.IntegrityError:
                    skipped.append((player_name, "already in squad"))

            # Treat bulk submit as source of truth for this squad list:
            # remove players currently in this squad but not present in submitted list.
            if players:
                submitted_names = {p["name"].strip().lower() for p in players if p.get("name")}
                existing_rows = conn.execute(
                    "SELECT player_name FROM squad WHERE user_id = ?",
                    (target_id,),
                ).fetchall()
                to_remove = [name for (name,) in existing_rows if name.strip().lower() not in submitted_names]

                for player_name in to_remove:
                    conn.execute(
                        "DELETE FROM squad WHERE user_id = ? AND player_name = ? COLLATE NOCASE",
                        (target_id, player_name),
                    )
                    removed.append(player_name)

            conn.commit()

            size    = squad_size(conn, target_id)
            target  = ctx.guild.get_member(int(target_id))
            display = target.mention if target else f"<@{target_id}>"

            embed = discord.Embed(
                title="Squad Registered",
                description=f"Squad registration complete for {display}."
                            + (f"\n👔 **Manager:** {manager}" if manager else ""),
                color=0x57F287 if signed else 0xFEE75C
            )

            if signed:
                embed.add_field(
                    name=f"✅ Signed ({len(signed)})",
                    value="\n".join(signed) or "—",
                    inline=False
                )
            if skipped:
                skipped_lines = "\n".join(f"**{n}** — {r}" for n, r in skipped)
                embed.add_field(
                    name=f"⚠️ Skipped ({len(skipped)})",
                    value=skipped_lines,
                    inline=False
                )
            if removed:
                embed.add_field(
                    name=f"🗑️ Removed ({len(removed)})",
                    value="\n".join(removed),
                    inline=False,
                )

            embed.add_field(name="Squad Size", value=f"{size} player(s)", inline=True)
            embed.add_field(name="Signed by",  value=ctx.author.display_name, inline=True)
            embed.set_footer(text="FM26 Sims")

            await ctx.reply(embed=embed, mention_author=False)

        finally:
            conn.close()

    # ── Single sign helper ────────────────────────────────────────────────────

    async def _sign_player(
        self,
        ctx: commands.Context,
        target_id: str,
        player_name: str,
        position: Optional[str] = None,
    ):
        conn = get_db()
        try:
            owner_id = get_player_owner(conn, player_name)
            if owner_id:
                if owner_id == target_id:
                    return await ctx.reply(
                        embed=warn_embed(f"**{player_name}** is already in that squad."),
                        mention_author=False
                    )
                owner         = ctx.guild.get_member(int(owner_id))
                owner_display = owner.mention if owner else f"<@{owner_id}>"
                return await ctx.reply(
                    embed=warn_embed(
                        f"**{player_name}** is already signed to {owner_display}'s squad.\n\n"
                        f"A player can only belong to **one squad** at a time."
                    ),
                    mention_author=False
                )

            conn.execute(
                "INSERT INTO squad (user_id, player_name, position) VALUES (?, ?, ?)",
                (target_id, player_name, position)
            )
            conn.commit()

            raw_squad = rebuild_raw_squad(conn, target_id)
            conn.execute(
                "INSERT INTO squad_meta (user_id, raw_squad) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET raw_squad = excluded.raw_squad",
                (target_id, raw_squad)
            )
            conn.commit()

            size    = squad_size(conn, target_id)
            target  = ctx.guild.get_member(int(target_id))
            display = target.mention if target else f"<@{target_id}>"

            await ctx.reply(
                embed=success_embed(
                    title="Player Signed",
                    description=(
                        f"**{player_name}** has been added to {display}'s squad."
                        + (f"\nPosition Group: **{_section_for_position(position)}**" if position else "")
                    ),
                    fields=[
                        ("Squad Size", f"{size} player(s)", True),
                        ("Signed by",  ctx.author.display_name, True),
                    ],
                    footer="FM26 Sims"
                ),
                mention_author=False
            )

        except sqlite3.IntegrityError:
            await ctx.reply(
                embed=warn_embed(f"**{player_name}** is already in that squad."),
                mention_author=False
            )
        finally:
            conn.close()

    @add_player.error
    async def add_player_error(self, ctx: commands.Context, error):
        await ctx.reply(embed=error_embed(f"An unexpected error occurred: `{error}`"), mention_author=False)

    # ── .release ──────────────────────────────────────────────────────────────

    @commands.command(name="release")
    async def remove_player(self, ctx: commands.Context, *args):
        try:
            target_id, player_name = parse_release_args(ctx, args)
        except PermissionError as e:
            return await ctx.reply(embed=error_embed(f"⛔ {e}"), mention_author=False)

        if player_name:
            return await self._release_single(ctx, target_id, player_name)

        conn = get_db()
        try:
            players = get_squad_players(conn, target_id)
        finally:
            conn.close()

        if not players:
            target = ctx.guild.get_member(int(target_id))
            display = target.mention if target else f"<@{target_id}>"
            return await ctx.reply(
                embed=warn_embed(f"{display} has no players in that squad."),
                mention_author=False,
            )

        view = ReleasePlayersView(
            bot=self.bot,
            author_id=ctx.author.id,
            target_id=target_id,
            target_name=(ctx.guild.get_member(int(target_id)).display_name if ctx.guild.get_member(int(target_id)) else f"<@{target_id}>")
        )
        embed = discord.Embed(
            title="🗑️ Release Players",
            description=(
                "Select one or more players from the dropdown, then press **Release Selected**.\n"
                "Use **Home** if you want to cancel and go back later."
            ),
            color=0xFEE75C,
        )
        embed.add_field(
            name="How it works",
            value="You can select multiple names at once, then release them together.",
            inline=False,
        )
        embed.set_footer(text="FM26 Sims")
        msg = await ctx.reply(embed=embed, view=view, mention_author=False)
        view.message = msg

    async def _release_single(self, ctx: commands.Context, target_id: str, player_name: str):
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT player_name FROM squad WHERE user_id = ? AND player_name = ? COLLATE NOCASE",
                (target_id, player_name),
            ).fetchone()

            if not row:
                target = ctx.guild.get_member(int(target_id))
                display = target.mention if target else f"<@{target_id}>"
                return await ctx.reply(
                    embed=warn_embed(f"**{player_name}** was not found in {display}'s squad."),
                    mention_author=False,
                )

            canonical_name = row[0]
            conn.execute(
                "DELETE FROM squad WHERE user_id = ? AND player_name = ? COLLATE NOCASE",
                (target_id, player_name),
            )
            conn.commit()

            raw_squad = rebuild_raw_squad(conn, target_id)
            conn.execute(
                "INSERT INTO squad_meta (user_id, raw_squad) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET raw_squad = excluded.raw_squad",
                (target_id, raw_squad),
            )
            conn.commit()

            size = squad_size(conn, target_id)
            target = ctx.guild.get_member(int(target_id))
            display = target.mention if target else f"<@{target_id}>"

            await ctx.reply(
                embed=success_embed(
                    title="Player Released",
                    description=f"**{canonical_name}** has been released from {display}'s squad.",
                    fields=[
                        ("Squad Size", f"{size} player(s)", True),
                        ("Released by", ctx.author.display_name, True),
                    ],
                    footer="This player is now a free agent and can be signed by anyone.",
                ),
                mention_author=False,
            )
        finally:
            conn.close()

    @commands.command(name="resetsquad", aliases=["clearsquad", "squadreset"])
    async def reset_squad(self, ctx: commands.Context, *, target_input: str = None):
        """Reset a squad by removing all players and clearing the manager. Accepts a mention or Discord ID."""
        target_id, target_display_name = await self.resolve_reset_target(ctx, target_input)
        if not target_id:
            return await ctx.reply(
                embed=error_embed("❌ Could not find that user. Please provide a mention or Discord ID."),
                mention_author=False,
            )

        if target_id != str(ctx.author.id) and not is_admin(ctx.author):
            return await ctx.reply(embed=error_embed("⛔ Only admins can reset another user's squad."), mention_author=False)

        conn = get_db()
        try:
            existing_meta = conn.execute(
                "SELECT logo_url FROM squad_meta WHERE user_id = ?",
                (target_id,),
            ).fetchone()
            logo_url = existing_meta[0] if existing_meta else None
        finally:
            conn.close()

        class ResetSquadConfirmView(discord.ui.View):
            def __init__(self, author_id: int):
                super().__init__(timeout=60)
                self.author_id = author_id
                self.message = None

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
                    deleted_count = conn.execute("DELETE FROM squad WHERE user_id = ?", (target_id,)).rowcount
                    conn.execute(
                        "INSERT INTO squad_meta (user_id, manager, raw_squad, logo_url) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(user_id) DO UPDATE SET manager = excluded.manager, raw_squad = excluded.raw_squad, logo_url = excluded.logo_url",
                        (target_id, None, "", logo_url),
                    )
                    conn.commit()
                finally:
                    conn.close()

                for item in self.children:
                    item.disabled = True

                await interaction.response.edit_message(
                    embed=success_embed(
                        title="Squad Reset",
                        description=f"{target_display_name}'s squad has been cleared.",
                        fields=[
                            ("Players Removed", str(deleted_count), True),
                            ("Manager", "Removed", True),
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

        view = ResetSquadConfirmView(author_id=ctx.author.id)
        embed = discord.Embed(
            title="⚠️ Confirm Squad Reset",
            description=(
                f"This will remove every player and the manager from {target_display_name}'s squad.\n"
                f"User ID: {target_id}\n\n"
                "Are you sure?"
            ),
            color=0xED4245,
        )
        embed.set_footer(text="This action cannot be undone")

        msg = await ctx.reply(embed=embed, view=view, mention_author=False)
        view.message = msg

    @remove_player.error
    async def remove_player_error(self, ctx: commands.Context, error):
        await ctx.reply(embed=error_embed(f"An unexpected error occurred: `{error}`"), mention_author=False)


class ReleasePlayerSelect(discord.ui.Select):
    def __init__(self, view: "ReleasePlayersView", players: List[Tuple[int, str, Optional[str], int]], page: int = 0):
        self.view_ref = view
        self.page = page
        
        # Pagination: 25 players per page (Discord select limit)
        start_idx = page * 25
        end_idx = start_idx + 25
        page_players = players[start_idx:end_idx]
        
        options = []
        for player_id, player_name, position, on_loan in page_players:
            details = []
            if position:
                details.append(position)
            if on_loan:
                details.append("Loan")
            description = " • ".join(details) if details else "In squad"
            options.append(
                discord.SelectOption(
                    label=player_name[:100],
                    description=description[:100],
                    value=str(player_id),
                )
            )

        super().__init__(
            placeholder="Select players to release",
            min_values=1,
            max_values=min(25, len(options)),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.view_ref.selected_ids = [int(value) for value in self.values]
        self.view_ref.selected_names = [option.label for option in self.options if option.value in self.values]
        await interaction.response.edit_message(embed=self.view_ref.preview_embed(), view=self.view_ref)


class ReleasePlayersView(discord.ui.View):
    def __init__(self, bot: commands.Bot, author_id: int, target_id: str, target_name: str):
        super().__init__(timeout=180)
        self.bot = bot
        self.author_id = author_id
        self.target_id = target_id
        self.target_name = target_name
        self.selected_ids: List[int] = []
        self.selected_names: List[str] = []
        self.message: Optional[discord.Message] = None
        self.current_page = 0

        conn = get_db()
        try:
            self.players = get_squad_players(conn, target_id)
        finally:
            conn.close()

        self._update_select()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the command user can control this release menu.",
                ephemeral=True,
            )
            return False
        return True

    def _update_select(self):
        """Rebuild the select menu for the current page."""
        # Remove old select if it exists
        for item in self.children[:]:
            if isinstance(item, ReleasePlayerSelect):
                self.remove_item(item)
        
        # Add new select for current page
        self.add_item(ReleasePlayerSelect(self, self.players, self.current_page))
        
        # Update button states
        total_pages = (len(self.players) + 24) // 25
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.label == "⬅️ Previous":
                    item.disabled = self.current_page == 0
                elif item.label == "Next ➡️":
                    item.disabled = self.current_page >= total_pages - 1

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
            title="🗑️ Release Players",
            description=f"Selected for release from **{self.target_name}**'s squad.",
            color=0xF39C12,
        )
        embed.add_field(
            name="Selected Players",
            value="\n".join(f"• {name}" for name in self.selected_names) if self.selected_names else "None",
            inline=False,
        )
        total_pages = (len(self.players) + 24) // 25
        if total_pages > 1:
            embed.set_footer(text=f"Page {self.current_page + 1}/{total_pages} • Press Release Selected to confirm or Home to cancel")
        else:
            embed.set_footer(text="Press Release Selected to confirm or Home to cancel")
        return embed

    @discord.ui.button(label="✅ Release Selected", style=discord.ButtonStyle.danger)
    async def release_selected(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_ids:
            return await interaction.response.send_message(
                "Select at least one player first.",
                ephemeral=True,
            )

        conn = get_db()
        try:
            deleted_names = []
            for player_id in self.selected_ids:
                row = conn.execute(
                    "SELECT player_name FROM squad WHERE id = ? AND user_id = ?",
                    (player_id, self.target_id)
                ).fetchone()
                if not row:
                    continue
                deleted_names.append(row[0])
                conn.execute("DELETE FROM squad WHERE id = ?", (player_id,))

            conn.commit()

            raw_squad = rebuild_raw_squad(conn, self.target_id)
            conn.execute(
                "INSERT INTO squad_meta (user_id, raw_squad) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET raw_squad = excluded.raw_squad",
                (self.target_id, raw_squad)
            )
            conn.commit()
            size = squad_size(conn, self.target_id)
        finally:
            conn.close()

        if not deleted_names:
            return await interaction.response.edit_message(
                embed=warn_embed("No selected players were found anymore."),
                view=self,
            )

        target_display = f"{self.target_name}"
        embed = success_embed(
            title="Players Released",
            description=f"Released {len(deleted_names)} player(s) from {target_display}'s squad.",
            fields=[
                ("Released Players", "\n".join(f"• {name}" for name in deleted_names), False),
                ("Squad Size", f"{size} player(s)", True),
                ("Released by", interaction.user.display_name, True),
            ],
            footer="This player is now a free agent and can be signed by anyone.",
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🏠 Home", style=discord.ButtonStyle.secondary)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_ids = []
        self.selected_names = []
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🗑️ Release Players",
                description="Use `.release` again to open the player picker.",
                color=0x95A5A6,
            ),
            view=self,
        )

    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.selected_ids = []
            self.selected_names = []
            self._update_select()
            await interaction.response.edit_message(embed=self.preview_embed(), view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = (len(self.players) + 24) // 25
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self.selected_ids = []
            self.selected_names = []
            self._update_select()
            await interaction.response.edit_message(embed=self.preview_embed(), view=self)


# ─── Setup ────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot):
    await bot.add_cog(AddPlayer(bot))