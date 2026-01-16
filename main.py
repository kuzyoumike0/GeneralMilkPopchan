# main.py
# Discord Bot main entry
# - prefix command: !
# - slash commands / buttons / selects 対応
# - 各種マーダーミステリー用 Cog をロード

import os
import asyncio
import logging

import discord
from discord.ext import commands

# =========================
# 設定
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")  # Railway / Render / ローカル .env 想定

COMMAND_PREFIX = "!"

INTENTS = discord.Intents.default()
INTENTS.message_content = True   # !setup 用
INTENTS.members = True           # 権限管理・表示名取得
INTENTS.guilds = True
INTENTS.voice_states = True      # VC参加者取得

# =========================
# Bot クラス
# =========================
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=COMMAND_PREFIX,
            intents=INTENTS,
            help_command=None,
        )

    async def setup_hook(self):
        """
        Bot 起動時に一度だけ呼ばれる
        - Cogロード
        - スラッシュコマンド同期
        """

        # ========= Cog ロード =========
        EXTENSIONS = [
            # VC共有テキスト作成（!setup）
            "cogs.vc_setup",

            # HO選択 → 個別ch自動作成
            "cogs.ho_select",

            # ダイス
            "cogs.dice",

            # /choice, /secretroll
            "cogs.dice_plus",

            # （ログHTML書き出しを使うなら）
            # "cogs.export_html",
        ]

        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                print(f"[LOAD] {ext}")
            except Exception as e:
                print(f"[ERROR] Failed to load {ext}: {e}")

        # ========= Slash Command Sync =========
        # グローバル同期（反映まで最大1時間）
        try:
            synced = await self.tree.sync()
            print(f"[SYNC] {len(synced)} commands synced globally")
        except Exception as e:
            print(f"[ERROR] Command sync failed: {e}")

    async def on_ready(self):
        print("===================================")
        print(f"Logged in as: {self.user}")
        print(f"Bot ID: {self.user.id}")
        print("===================================")

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandNotFound):
            return
        await ctx.reply(f"❌ エラーが発生しました: `{error}`")


# =========================
# エントリーポイント
# =========================
async def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN が設定されていません")

    bot = Bot()
    await bot.start(TOKEN)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
