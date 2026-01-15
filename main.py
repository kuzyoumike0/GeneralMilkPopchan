# main.py
import os
import asyncio
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()  # 省略可（グローバル同期は遅いので推奨）

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True  # 表示名/権限処理で必要

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)

    async def setup_hook(self):
        await self.load_extension("cogs.session_channels")

        # コマンド同期（ギルド指定があるなら速い）
        if GUILD_ID.isdigit():
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"[sync] guild={GUILD_ID}")
        else:
            await self.tree.sync()
            print("[sync] global (may take time to appear)")

bot = MyBot()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is empty. Set env DISCORD_TOKEN.")
    asyncio.run(bot.start(TOKEN))
