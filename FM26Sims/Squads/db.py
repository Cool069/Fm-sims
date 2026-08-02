import sqlite3
import discord


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect("squad.db")
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS squad (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            player_name TEXT NOT NULL COLLATE NOCASE,
            position    TEXT,
            on_loan     INTEGER NOT NULL DEFAULT 0,
            added_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, player_name),
            UNIQUE(player_name)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS squad_meta (
            user_id     TEXT PRIMARY KEY,
            manager     TEXT,
            raw_squad   TEXT,
            logo_url    TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS balances (
            user_id     TEXT PRIMARY KEY,
            amount      INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS managers_owned (
            user_id      TEXT NOT NULL,
            manager_name TEXT NOT NULL,
            PRIMARY KEY(user_id, manager_name)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS managers_market (
            name        TEXT PRIMARY KEY,
            salary_raw  TEXT,
            salary      INTEGER DEFAULT 0,
            formation   TEXT,
            norm_name   TEXT
        )
    """)

    # Migrate existing DBs — add columns if missing
    existing_squad = {row[1] for row in conn.execute("PRAGMA table_info(squad)")}
    if "position" not in existing_squad:
        conn.execute("ALTER TABLE squad ADD COLUMN position TEXT")
    if "on_loan" not in existing_squad:
        conn.execute("ALTER TABLE squad ADD COLUMN on_loan INTEGER NOT NULL DEFAULT 0")

    existing_meta = {row[1] for row in conn.execute("PRAGMA table_info(squad_meta)")}
    if "raw_squad" not in existing_meta:
        conn.execute("ALTER TABLE squad_meta ADD COLUMN raw_squad TEXT")
    if "logo_url" not in existing_meta:
        conn.execute("ALTER TABLE squad_meta ADD COLUMN logo_url TEXT")

    conn.commit()
    return conn


# ─── Embeds ───────────────────────────────────────────────────────────────────

def error_embed(description: str) -> discord.Embed:
    return discord.Embed(description=description, color=0xED4245)

def warn_embed(description: str) -> discord.Embed:
    return discord.Embed(description=description, color=0xFEE75C)

def success_embed(title: str, description: str, fields: list, footer: str) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=0x57F287)
    for name, value, inline in fields:
        embed.add_field(name=name, value=value, inline=inline)
    embed.set_footer(text=footer)
    return embed


def get_setting(key: str, default=None):
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()