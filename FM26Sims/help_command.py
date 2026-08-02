import discord
from discord.ext import commands
from typing import List, Dict, Any, Optional


HELP_PAGES: List[Dict[str, Any]] = [
    {
        "label": "Economy",
        "emoji": "💰",
        "short": "Balance, transfer, and salary commands",
        "title": "💰 Economy Commands",
        "color": 0xF1C40F,
        "content": (
            "`{p}balance` / `{p}bal`\n"
            "View your club balance. Aliases: `{p}money`, `{p}wallet`.\n\n"
            "`{p}collect`\n"
            "Collect role-based salary (with cooldown). Aliases: `{p}claim`, `{p}salary`, `{p}collectmoney`.\n\n"
            "`{p}give @user <amount>`\n"
            "Transfer money to another user. (aliases: `{p}send`, `{p}pay`, `{p}givemoney`)\n\n"
            "`{p}transfer @user`\n"
            "Start a private two-sided trade proposal in DMs with player and cash options.\n\n"
            "`{p}addbal @user <amount>`\n"
            "Admin-only: add balance. (aliases: `{p}add`, `{p}ab`, `{p}addmoney`)\n\n"
            "`{p}removebal @user <amount>`\n"
            "Admin-only: remove balance. (aliases: `{p}rm`, `{p}remove`, `{p}removemoney`)\n\n"
            "`{p}lb`\n"
            "Admin-only: view top 10 money leaderboard.\n\n"
            "`{p}resetmoney @user` / `{p}resetbal @user`\n"
            "Admin-only: reset one user's balance to 0 after a yes/no confirmation.\n\n"
            "`{p}resetallbal`\n"
            "Admin-only: reset every user's balance to 0 after a yes/no confirmation.\n\n"
            "`{p}resetall`\n"
            "Admin-only: reset all balances and collect cooldowns.\n\n"
            "`{p}logs #channel`\n"
            "Admin-only: set money log channel.\n\n"
            "`{p}ssc #channel` / `{p}scc #channel`\n"
            "Admin-only: set the sold-channel used for completed transfer deals."
        ),
    },
    {
        "label": "Squad Management",
        "emoji": "🧩",
        "short": "Register and manage players",
        "title": "🧩 Squad Management",
        "color": 0x2ECC71,
        "content": (
            "`{p}addplayer <player name>`\n"
            "Add a player to your own squad.\n\n"
            "`{p}addplayer @user <player name>`\n"
            "Admin-only: add player to another user's squad.\n\n"
            "`{p}release <player name>`\n"
            "Release a player from your squad.\n\n"
            "`{p}release @user <player name>`\n"
            "Admin-only: release from another user's squad.\n\n"
            "`{p}resetsquad` / `{p}resetsquad @user`\n"
            "Clear all players and the manager from a squad after a yes/no confirmation. Admin-only for other users.\n\n"
            "`{p}squad` / `{p}squad @user`\n"
            "View your squad or another user's squad."
        ),
    },
    {
        "label": "Club Customization",
        "emoji": "🎨",
        "short": "Set visuals for your club",
        "title": "🎨 Club Customization",
        "color": 0x3498DB,
        "content": (
            "`{p}logo`\n"
            "Upload your club logo (JPG/PNG).\n"
            "It will appear in related squad and balance embeds."
        ),
    },
    {
        "label": "Daily Awards",
        "emoji": "🏆",
        "short": "Generate daily legacy award cards",
        "title": "🏆 Daily Awards",
        "color": 0xE67E22,
        "content": (
            "`{p}daily`\n"
            "Generate the FM26 Daily Award card (current setup: Dictator card)."
        ),
    },
    {
        "label": "Amount Format",
        "emoji": "🔢",
        "short": "How to write money values",
        "title": "🔢 Amount Format Guide",
        "color": 0x9B59B6,
        "content": (
            "Supported formats:\n"
            "`500`\n"
            "`100k`\n"
            "`1.5m`\n"
            "`2b`\n"
            "`1e6`\n"
            "`10e6`\n"
            "`10e5`\n"
            "`2.5e3`\n\n"
            "Examples:\n"
            "`{p}add @user 10e6`\n"
            "`{p}give @user 10e5`\n"
            "`{p}transfer @user`\n"
            "`{p}pay @user 250k`"
        ),
    },
]


class HelpSectionSelect(discord.ui.Select):
    def __init__(self, parent_view: "HelpView"):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(
                label=page["label"],
                description=page["short"],
                emoji=page["emoji"],
                value=str(i),
            )
            for i, page in enumerate(HELP_PAGES)
        ]
        super().__init__(
            placeholder="Select a section to explore",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        self.parent_view.current_index = idx
        embed = self.parent_view.page_embed()
        self.parent_view.sync_button_states()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class HelpView(discord.ui.View):
    def __init__(self, author_id: int, prefix: str):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.prefix = prefix
        self.current_index = -1  # -1 = home
        self.message: Optional[discord.Message] = None

        self.section_select = HelpSectionSelect(self)
        self.add_item(self.section_select)
        self.sync_button_states()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the command user can control this help menu.",
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

    def home_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="📘 FM26 Sims Help Center",
            description=(
                "Everything you need is here.\n"
                "Pick a section from the dropdown below to view details."
            ),
            color=0x57F287,
        )

        embed.add_field(
            name="Quick Start",
            value=(
                "1. Use `.addplayer` to register your squad\n"
                "2. Set a logo with `.logo`\n"
                "3. Check funds with `.bal`\n"
                "4. Collect salary using `.collect`\n"
                "5. Transfer cash with `.give` or build a trade with `.transfer`"
            ),
            inline=False,
        )

        embed.add_field(
            name="Popular Commands",
            value=(
                "`{p}help` Open this menu\n"
                "`{p}squad` View your squad\n"
                "`{p}bal` Check your balance\n"
                "`{p}collect` Collect salary"
            ).format(p=self.prefix),
            inline=False,
        )

        embed.set_footer(text="Use the dropdown to open sections • FM26 Sims")
        return embed

    def page_embed(self) -> discord.Embed:
        page = HELP_PAGES[self.current_index]
        embed = discord.Embed(
            title=page["title"],
            description=page["content"].format(p=self.prefix),
            color=page["color"],
        )
        embed.set_footer(
            text=f"Page {self.current_index + 1}/{len(HELP_PAGES)} • Use buttons to navigate"
        )
        return embed

    def sync_button_states(self):
        on_home = self.current_index == -1
        self.prev_btn.disabled = on_home or self.current_index <= 0
        self.next_btn.disabled = on_home or self.current_index >= len(HELP_PAGES) - 1
        self.home_btn.disabled = on_home

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.primary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index > 0:
            self.current_index -= 1
        embed = self.page_embed() if self.current_index >= 0 else self.home_embed()
        self.sync_button_states()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index < 0:
            self.current_index = 0
        elif self.current_index < len(HELP_PAGES) - 1:
            self.current_index += 1
        embed = self.page_embed()
        self.sync_button_states()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🏠 Home", style=discord.ButtonStyle.secondary)
    async def home_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_index = -1
        self.sync_button_states()
        await interaction.response.edit_message(embed=self.home_embed(), view=self)


class HelpCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help")
    async def help(self, ctx: commands.Context):
        view = HelpView(author_id=ctx.author.id, prefix=ctx.prefix)
        msg = await ctx.reply(embed=view.home_embed(), view=view, mention_author=False)
        view.message = msg


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCommand(bot))
