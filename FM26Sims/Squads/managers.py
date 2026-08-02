import unicodedata
import discord
from discord.ext import commands

from Squads.db import get_db, error_embed, success_embed, warn_embed
from Squads.addplayer import parse_args, rebuild_raw_squad, is_admin
from Economy.bal import get_balance
from Economy.balutils import parse_amount, fmt


def _norm(text: str) -> str:
    if not text:
        return ""
    nf = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nf if not unicodedata.combining(c)).lower().strip()


def _find_market_manager(manager_name: str):
    norm = _norm(manager_name)
    # load from DB and attempt exact then partial match
    conn = get_db()
    try:
        rows = conn.execute("SELECT name, salary_raw, salary, formation, norm_name FROM managers_market").fetchall()
    finally:
        conn.close()

    market = []
    for r in rows:
        market.append({"name": r[0], "salary_raw": r[1], "salary": r[2] or 0, "formation": r[3], "norm_name": r[4] or _norm(r[0])})

    for m in market:
        if m["norm_name"] == norm:
            return m
    for m in market:
        if norm in m["norm_name"] or m["norm_name"] in norm:
            return m
    return None



def _load_market():
    conn = get_db()
    try:
        rows = conn.execute("SELECT name, salary_raw, salary, formation, norm_name FROM managers_market").fetchall()
    finally:
        conn.close()

    market = []
    for r in rows:
        salary = r[2]
        if salary is None and r[1]:
            try:
                salary = parse_amount(r[1])
            except Exception:
                salary = 0
        market.append({
            "name": r[0],
            "salary_raw": r[1],
            "salary": salary or 0,
            "formation": r[3],
            "norm_name": r[4] or _norm(r[0])
        })
    return market


class Managers(commands.Cog):
    """Manager market and purchase commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="managermarket")
    async def manager_market(self, ctx: commands.Context):
        """Show available managers with salary and preferred formations."""
        sorted_market = sorted(_load_market(), key=lambda item: item["salary"], reverse=True)

        embed = discord.Embed(
            title="🛒 Manager Market",
            description=(
                "Choose a manager for your club.\n"
                "Buy one with `.buymanager <name>` and assign an owned manager with `.addmanager <name>`."
            ),
            color=0x2ECC71,
        )

        embed.add_field(
            name="How It Works",
            value=(
                "1. Check the market below\n"
                "2. Buy a manager with `.buymanager`\n"
                "3. Assign your owned manager with `.addmanager`"
            ),
            inline=False,
        )

        for index, m in enumerate(sorted_market, start=1):
            embed.add_field(
                name=f"{index}. {m['name']}",
                value=(
                    f"💷 Salary: **{fmt(m['salary'])}**\n"
                    f"🧠 Formation: {m['formation']}"
                ),
                inline=False,
            )

        embed.set_footer(text=f"FM26 Sims Manager Board • {len(sorted_market)} managers listed")
        await ctx.reply(embed=embed, mention_author=False)
    @commands.command(name="buymanager")
    async def buy_manager(self, ctx: commands.Context, *args):
        """Purchase a manager from the market and assign to a club (you pay)."""
        try:
            target_id, manager_input = parse_args(ctx, args)
        except PermissionError as e:
            return await ctx.reply(embed=error_embed(f"⛔ {e}"), mention_author=False)
        except ValueError:
            return await ctx.reply(embed=error_embed("Provide the manager's full name."), mention_author=False)

        if not manager_input:
            return await ctx.reply(embed=error_embed("Provide the manager's full name."), mention_author=False)

        manager_name = manager_input.strip()
        norm = _norm(manager_name)

        found = _find_market_manager(manager_name)
        if not found:
            return await ctx.reply(embed=error_embed("Manager not found in market. Use .managermarket to view available managers."), mention_author=False)

        buyer_id = str(ctx.author.id)
        cost = found.get("salary", 0)

        conn = get_db()
        try:
            row = conn.execute("SELECT amount FROM balances WHERE user_id = ?", (buyer_id,)).fetchone()
            balance = row[0] if row else 0
            if balance < cost:
                return await ctx.reply(embed=error_embed(f"Insufficient funds. Manager costs {fmt(cost)}, you have {fmt(balance)}."), mention_author=False)

            # Deduct buyer's balance
            new_bal = balance - cost
            if row:
                conn.execute("UPDATE balances SET amount = ? WHERE user_id = ?", (new_bal, buyer_id))
            else:
                conn.execute("INSERT INTO balances (user_id, amount) VALUES (?, ?)", (buyer_id, new_bal))

            # Record ownership for buyer
            conn.execute(
                "INSERT OR IGNORE INTO managers_owned (user_id, manager_name) VALUES (?, ?)",
                (buyer_id, found["name"]),
            )

            # Remove purchased manager from market
            conn.execute("DELETE FROM managers_market WHERE name = ?", (found["name"],))

            # Assign to target club (may be buyer or another if mentioned)
            conn.execute(
                "INSERT INTO squad_meta (user_id, manager, raw_squad, logo_url) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET manager = excluded.manager",
                (target_id, found["name"], "", None),
            )
            raw = rebuild_raw_squad(conn, target_id)
            conn.execute("UPDATE squad_meta SET raw_squad = ? WHERE user_id = ?", (raw, target_id))
            conn.commit()
        finally:
            conn.close()

        await ctx.reply(embed=success_embed(title="Manager Purchased", description=f"You bought **{found['name']}** for **{fmt(cost)}** and assigned them to the club.", fields=[("Manager", found['name'], True), ("Cost", fmt(cost), True)], footer="FM26 Sims"), mention_author=False)

    @commands.command(name="removemanager")
    async def remove_manager(self, ctx: commands.Context, member: discord.Member = None):
        """Remove the current manager from your club, or from another club if used by an admin."""
        target = member or ctx.author
        if target.id != ctx.author.id and not is_admin(ctx.author):
            return await ctx.reply(embed=error_embed("⛔ Only admins can remove another club's manager."), mention_author=False)

        conn = get_db()
        try:
            row = conn.execute("SELECT manager FROM squad_meta WHERE user_id = ?", (str(target.id),)).fetchone()
            current_manager = row[0] if row else None
            if not current_manager:
                return await ctx.reply(embed=warn_embed("This club does not have a manager set."), mention_author=False)

            conn.execute(
                "INSERT INTO squad_meta (user_id, manager, raw_squad, logo_url) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET manager = excluded.manager",
                (str(target.id), None, "", None),
            )
            raw = rebuild_raw_squad(conn, str(target.id))
            conn.execute("UPDATE squad_meta SET raw_squad = ? WHERE user_id = ?", (raw, str(target.id)))
            conn.commit()
        finally:
            conn.close()

        await ctx.reply(
            embed=success_embed(
                title="Manager Removed",
                description=f"**{current_manager}** was removed from {target.display_name}'s club.",
                fields=[("Club", target.display_name, True)],
                footer="FM26 Sims",
            ),
            mention_author=False,
        )

    @commands.command(name="addmanager")
    async def add_manager(self, ctx: commands.Context, *args):
        """Assign an owned manager to your club. Admins may assign without owning via mention."""
        try:
            target_id, manager_input = parse_args(ctx, args)
        except PermissionError as e:
            return await ctx.reply(embed=error_embed(f"⛔ {e}"), mention_author=False)
        except ValueError:
            return await ctx.reply(embed=error_embed("Provide the manager's full name."), mention_author=False)

        if not manager_input:
            return await ctx.reply(embed=error_embed("Provide the manager's full name."), mention_author=False)

        manager_name = manager_input.strip()
        norm = _norm(manager_name)

        # match market names for normalization
        found = _find_market_manager(manager_name)
        assigned_name = found["name"] if found else manager_name

        # If assigning to someone else and invoker is admin, allow bypass ownership
        assigning_for_other = (str(ctx.author.id) != target_id)
        if assigning_for_other and is_admin(ctx.author):
            bypass = True
        else:
            bypass = False

        conn = get_db()
        try:
            if not bypass and found:
                # ensure invoker owns this manager when it is a market manager
                row = conn.execute(
                    "SELECT 1 FROM managers_owned WHERE user_id = ? AND manager_name = ?",
                    (str(ctx.author.id), found["name"]),
                ).fetchone()
                if not row:
                    return await ctx.reply(embed=error_embed("You don't own this manager. Use .buymanager <name> to purchase first."), mention_author=False)

            # Assign manager
            conn.execute(
                "INSERT INTO squad_meta (user_id, manager, raw_squad, logo_url) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET manager = excluded.manager",
                (target_id, assigned_name, "", None),
            )
            raw = rebuild_raw_squad(conn, target_id)
            conn.execute("UPDATE squad_meta SET raw_squad = ? WHERE user_id = ?", (raw, target_id))
            conn.commit()
        finally:
            conn.close()

        await ctx.reply(embed=success_embed(title="Manager Assigned", description=f"**{assigned_name}** assigned to the club.", fields=[("Manager", assigned_name, True)], footer="FM26 Sims"), mention_author=False)

    @commands.command(name="addmarketmanager")
    async def add_market_manager(self, ctx: commands.Context, *args):
        """Admin: Add or update a manager in the market. Usage: .addmarketmanager Name | salary | formation"""
        if not is_admin(ctx.author):
            return await ctx.reply(embed=error_embed("⛔ Only admins can modify the manager market."), mention_author=False)

        if not args:
            return await ctx.reply(embed=error_embed("Provide manager details: Name | salary | formation"), mention_author=False)

        raw = " ".join(args).strip()
        parts = [p.strip() for p in raw.split("|")]
        name = parts[0] if parts else None
        salary_raw = parts[1] if len(parts) > 1 else None
        formation = parts[2] if len(parts) > 2 else None

        if not name:
            return await ctx.reply(embed=error_embed("Provide the manager's full name."), mention_author=False)

        try:
            salary = parse_amount(salary_raw) if salary_raw else 0
        except Exception:
            salary = 0

        norm_name = _norm(name)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO managers_market (name, salary_raw, salary, formation, norm_name) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET salary_raw = excluded.salary_raw, salary = excluded.salary, formation = excluded.formation, norm_name = excluded.norm_name",
                (name, salary_raw, salary, formation, norm_name),
            )
            conn.commit()
        finally:
            conn.close()

        await ctx.reply(embed=success_embed(title="Market Updated", description=f"Manager **{name}** added/updated in market.", fields=[("Name", name, True), ("Salary", fmt(salary), True)], footer="FM26 Sims"), mention_author=False)

    @commands.command(name="removemarketmanager")
    async def remove_market_manager(self, ctx: commands.Context, *args):
        """Admin: Remove a manager from the market. Usage: .removemarketmanager <name>"""
        if not is_admin(ctx.author):
            return await ctx.reply(embed=error_embed("⛔ Only admins can modify the manager market."), mention_author=False)

        if not args:
            return await ctx.reply(embed=error_embed("Provide the manager's full name."), mention_author=False)

        name = " ".join(args).strip()
        found = _find_market_manager(name)
        if not found:
            return await ctx.reply(embed=warn_embed("Manager not found in market."), mention_author=False)

        conn = get_db()
        try:
            conn.execute("DELETE FROM managers_market WHERE name = ?", (found["name"],))
            conn.commit()
        finally:
            conn.close()

        await ctx.reply(embed=success_embed(title="Market Updated", description=f"Manager **{found['name']}** removed from market.", fields=[("Manager", found['name'], True)], footer="FM26 Sims"), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Managers(bot))
