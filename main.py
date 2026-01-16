# main.py
# Discord Bot main entry
# - prefix command: !
# - slash commands / buttons / selects 対応
# - 各種マーダーミステリー用 Cog をロード
#
# ✅ 修正内容
# 1) cogs.vc_setup → cogs.session_channels に変更
# 2) PrivilegedIntentsRequired 対策：
#    - members / message_content を「環境変数でON/OFFできる」ように（Portal未ONでも落ちない）
#    - デフォルトは OFF（安全側）
# 3) TOKENのstrip + 空判定強化
# 4) discord.py 2.4+ の推奨: bot.close() を finally で確実に呼ぶ（aiohttp未クローズ警告低減）
# 5) 例外ログを少し見やすく

import os
import asyncio
import logging

import discord
from discord.ext import commands

# =========================
# 設定
# =========================

def env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    v = v.strip().lower()
    return v in ("1", "true", "yes", "y", "on")

TOKEN = (os.getenv("DISCORD_TOKEN", "") or "").strip()

COMMAND_PREFIX = "!"

# ---- Intents ----
# ✅ 特権インテントは「Developer PortalでONにしてないと落ちる」ので、
#    環境変数で必要な時だけONにする。
#
# - ENABLE_MEMBERS_INTENT=1 で members をON（Portalの SERVER MEMBERS INTENT もON必須）
# - ENABLE_MESSAGE_CONTENT=1 で message_content をON（Portalの MESSAGE CONTENT INTENT もON推奨）
#
# デフォルトはOFF（落ちない構成）
ENABLE_MEMBERS_INTENT = env_bool("ENABLE_MEMBERS_INTENT", default=False)
ENABLE_MESSAGE_CONTENT = env_bool("ENABLE_MESSAGE_CONTENT", default=False)

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.voice_states = True

# 特権インテント（必要な時だけ）
INTENTS.members = ENABLE_MEMBERS_INTENT
INTENTS.message_content = ENABLE_MESSAGE_CONTENT

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
            # VC共有テキスト作成（!setup など）
            "cogs.session_channels",   # ✅ ここを修正

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
                print(f"[ERROR] Failed to load {ext}: {type(e).__name__}: {e}")

        # ========= Slash Command Sync =========
        # グローバル同期（反映まで最大1時間）
        try:
            synced = await self.tree.sync()
            print(f"[SYNC] {len(synced)} commands synced globally")
        except Exception as e:
            print(f"[ERROR] Command sync failed: {type(e).__name__}: {e}")

    async def on_ready(self):
        print("===================================")
        print(f"Logged in as: {self.user}")
        print(f"Bot ID: {self.user.id}")
        print("Intents:")
        print(f" - guilds={self.intents.guilds}")
        print(f" - voice_states={self.intents.voice_states}")
        print(f" - members={self.intents.members} (ENABLE_MEMBERS_INTENT={ENABLE_MEMBERS_INTENT})")
        print(f" - message_content={self.intents.message_content} (ENABLE_MESSAGE_CONTENT={ENABLE_MESSAGE_CONTENT})")
        print("===================================")

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandNotFound):
            return
        try:
            await ctx.reply(f"❌ エラーが発生しました: `{type(error).__name__}: {error}`")
        except Exception:
            pass


# =========================
# エントリーポイント
# =========================
async def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN が未設定/空です（環境変数 DISCORD_TOKEN を確認してください）")

    bot = Bot()
    try:
        await bot.start(TOKEN)
    finally:
        # ✅ 例外落ちでもコネクタ未クローズ警告を減らす
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
