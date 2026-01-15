# cogs/session_channels.py
import json
import os
import re
import time
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands


DATA_DIR = "data"
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SESSIONS_PATH):
        with open(SESSIONS_PATH, "w", encoding="utf-8") as f:
            json.dump({"sessions": {}}, f, ensure_ascii=False, indent=2)


def load_db() -> dict:
    ensure_data_dir()
    with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_db(db: dict):
    ensure_data_dir()
    with open(SESSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def make_session_id(guild_id: int) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{ts}-{guild_id}"


def safe_channel_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥ー\-]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    if not name:
        name = "session"
    return name[:90].lower()


def mention_list(user_ids: List[int]) -> str:
    if not user_ids:
        return "（まだいません）"
    return "\n".join(f"- <@{uid}>" for uid in user_ids)


class SessionPanelView(discord.ui.View):
    def __init__(self, cog: "SessionChannelsCog", session_id: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id

    async def _refresh_panel(self, interaction: discord.Interaction):
        await self.cog.refresh_panel(self.session_id, interaction=interaction)

    @discord.ui.button(label="参加", style=discord.ButtonStyle.success, custom_id="session_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, msg = self.cog.add_player(self.session_id, interaction.user.id)
        await interaction.response.send_message(msg, ephemeral=True)
        if ok:
            await self._refresh_panel(interaction)

    @discord.ui.button(label="辞退", style=discord.ButtonStyle.secondary, custom_id="session_leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, msg = self.cog.remove_player(self.session_id, interaction.user.id)
        await interaction.response.send_message(msg, ephemeral=True)
        if ok:
            await self._refresh_panel(interaction)

    @discord.ui.button(label="チャンネル作成/更新", style=discord.ButtonStyle.primary, custom_id="session_build")
    async def build(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.cog.get_session(self.session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s["gm_id"]:
            await interaction.response.send_message("この操作はGMのみ実行できます。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.cog.build_or_update_channels(self.session_id, interaction.guild)
            await interaction.followup.send(result, ephemeral=True)
            await self._refresh_panel(interaction)
        except Exception as e:
            await interaction.followup.send(f"作成/更新中にエラー: {e}", ephemeral=True)

    @discord.ui.button(label="参加ロック/解除", style=discord.ButtonStyle.danger, custom_id="session_lock")
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.cog.get_session(self.session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s["gm_id"]:
            await interaction.response.send_message("この操作はGMのみ実行できます。", ephemeral=True)
            return
        s["locked"] = not s.get("locked", False)
        self.cog.save_session(s)
        await interaction.response.send_message(
            f"参加を {'ロック' if s['locked'] else '解除'} しました。",
            ephemeral=True
        )
        await self._refresh_panel(interaction)


class SessionChannelsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_data_dir()

        # 永続View復元
        db = load_db()
        for sid in db.get("sessions", {}).keys():
            self.bot.add_view(SessionPanelView(self, sid))

    def get_session(self, session_id: str) -> Optional[dict]:
        db = load_db()
        return db.get("sessions", {}).get(session_id)

    def save_session(self, session: dict):
        db = load_db()
        db.setdefault("sessions", {})[session["id"]] = session
        save_db(db)

    def add_player(self, session_id: str, user_id: int) -> Tuple[bool, str]:
        s = self.get_session(session_id)
        if not s:
            return False, "セッションが見つかりません。"
        if s.get("locked"):
            return False, "参加はロックされています（GMに連絡してください）。"
        players = s.setdefault("players", [])
        if user_id in players:
            return False, "すでに参加しています。"
        players.append(user_id)
        self.save_session(s)
        return True, "参加しました！"

    def remove_player(self, session_id: str, user_id: int) -> Tuple[bool, str]:
        s = self.get_session(session_id)
        if not s:
            return False, "セッションが見つかりません。"
        players = s.setdefault("players", [])
        if user_id not in players:
            return False, "参加していません。"
        players.remove(user_id)
        self.save_session(s)
        return True, "辞退しました。"

    def build_embed(self, session: dict) -> discord.Embed:
        e = discord.Embed(
            title=f"🎭 セッション参加パネル：{session['name']}",
            description=f"ID: `{session['id']}`\nGM: <@{session['gm_id']}>\n参加ロック: **{'ON' if session.get('locked') else 'OFF'}**",
            color=discord.Color.pink(),
        )
        e.add_field(name=f"参加者（{len(session.get('players', []))}）", value=mention_list(session.get("players", [])), inline=False)

        if session.get("category_id"):
            e.add_field(name="カテゴリ", value=f"<#{session['category_id']}>", inline=False)
        if session.get("channel_all_id"):
            e.add_field(name="参加者全体", value=f"<#{session['channel_all_id']}>", inline=True)
        if session.get("channel_gm_id"):
            e.add_field(name="GM", value=f"<#{session['channel_gm_id']}>", inline=True)

        return e

    async def refresh_panel(self, session_id: str, interaction: Optional[discord.Interaction] = None):
        s = self.get_session(session_id)
        if not s:
            return
        channel_id = s.get("panel_channel_id")
        message_id = s.get("panel_message_id")
        if not channel_id or not message_id:
            return

        guild = interaction.guild if interaction else self.bot.get_guild(s["guild_id"])
        if not guild:
            return
        ch = guild.get_channel(channel_id)
        if not isinstance(ch, discord.TextChannel):
            return

        try:
            msg = await ch.fetch_message(message_id)
        except Exception:
            return

        view = SessionPanelView(self, session_id)
        await msg.edit(embed=self.build_embed(s), view=view)

    async def _apply_all_channel_overwrites(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        gm_member: discord.Member,
        player_ids: List[int],
    ):
        """
        参加者全体チャンネルの権限を「参加者＋GMだけ見える」に揃える
        """
        everyone = guild.default_role

        overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            gm_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        for uid in player_ids:
            m = guild.get_member(uid)
            if m:
                overwrites[m] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        await channel.edit(overwrites=overwrites, reason="session participants updated")

    async def build_or_update_channels(self, session_id: str, guild: discord.Guild) -> str:
        s = self.get_session(session_id)
        if not s:
            return "セッションが見つかりません。"

        players = s.get("players", [])
        if not players:
            return "参加者がいません。先に参加ボタンで参加者を集めてください。"

        gm_id = s["gm_id"]
        gm_member = guild.get_member(gm_id)
        if gm_member is None:
            return "GMがこのサーバーに見つかりません。"

        # カテゴリ
        category: Optional[discord.CategoryChannel] = None
        if s.get("category_id"):
            cat = guild.get_channel(s["category_id"])
            if isinstance(cat, discord.CategoryChannel):
                category = cat

        if category is None:
            category = await guild.create_category(name=f"🎭{s['name']}", reason="session auto build")
            s["category_id"] = category.id

        base = safe_channel_name(s["name"])

        # 参加者全体チャンネル（1つ）
        all_ch: Optional[discord.TextChannel] = None
        if s.get("channel_all_id"):
            ch = guild.get_channel(s["channel_all_id"])
            if isinstance(ch, discord.TextChannel):
                all_ch = ch

        if all_ch is None:
            everyone = guild.default_role
            overwrites_all = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                gm_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }
            for uid in players:
                m = guild.get_member(uid)
                if m:
                    overwrites_all[m] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

            all_ch = await category.create_text_channel(
                name=f"参加者-{base}",
                overwrites=overwrites_all,
                topic=f"Session {s['id']} / 参加者全体",
                reason="session auto build",
            )
            s["channel_all_id"] = all_ch.id
        else:
            # 既存なら「新参加者」を反映
            await self._apply_all_channel_overwrites(guild, all_ch, gm_member, players)

        # GM専用（不要ならここ丸ごと削除OK）
        gm_ch: Optional[discord.TextChannel] = None
        if s.get("channel_gm_id"):
            ch = guild.get_channel(s["channel_gm_id"])
            if isinstance(ch, discord.TextChannel):
                gm_ch = ch

        if gm_ch is None:
            everyone = guild.default_role
            overwrites_gm = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                gm_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }
            gm_ch = await category.create_text_channel(
                name=f"gm-{base}",
                overwrites=overwrites_gm,
                topic=f"Session {s['id']} / GM only",
                reason="session auto build",
            )
            s["channel_gm_id"] = gm_ch.id

        self.save_session(s)

        # 初回案内
        if all_ch:
            await all_ch.send(
                f"✅ セッション **{s['name']}** の参加者チャンネルです。\n"
                f"GM: <@{gm_id}>\n"
                f"参加者一覧は参加パネルを確認してね。"
            )

        return "✅ 参加者全体チャンネル（＋GM専用）を作成/更新しました。"

    # ---- commands ----
    @app_commands.command(name="session_create", description="参加登録パネルを作成します（GM用）")
    @app_commands.describe(name="セッション名")
    async def session_create(self, interaction: discord.Interaction, name: str):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        session_id = make_session_id(interaction.guild.id)
        session = {
            "id": session_id,
            "guild_id": interaction.guild.id,
            "name": name,
            "gm_id": interaction.user.id,
            "players": [],
            "locked": False,
            "panel_channel_id": interaction.channel_id,
            "panel_message_id": None,
            "category_id": None,
            "channel_all_id": None,
            "channel_gm_id": None,
        }
        self.save_session(session)

        embed = self.build_embed(session)
        view = SessionPanelView(self, session_id)

        self.bot.add_view(view)  # 永続化
        await interaction.response.send_message(embed=embed, view=view)

        msg = await interaction.original_response()
        session["panel_message_id"] = msg.id
        self.save_session(session)

    @app_commands.command(name="session_info", description="セッション情報を表示します（ID指定）")
    @app_commands.describe(session_id="セッションID")
    async def session_info(self, interaction: discord.Interaction, session_id: str):
        s = self.get_session(session_id)
        if not s:
            await interaction.response.send_message("見つかりません。", ephemeral=True)
            return
        await interaction.response.send_message(embed=self.build_embed(s), ephemeral=True)

    @app_commands.command(name="session_add", description="参加者を手動追加（GM用）")
    @app_commands.describe(session_id="セッションID", member="追加するメンバー")
    async def session_add(self, interaction: discord.Interaction, session_id: str, member: discord.Member):
        s = self.get_session(session_id)
        if not s:
            await interaction.response.send_message("見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s["gm_id"]:
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return
        ok, msg = self.add_player(session_id, member.id)
        await interaction.response.send_message(msg, ephemeral=True)
        if ok:
            await self.refresh_panel(session_id, interaction=interaction)

    @app_commands.command(name="session_remove", description="参加者を手動削除（GM用）")
    @app_commands.describe(session_id="セッションID", member="削除するメンバー")
    async def session_remove(self, interaction: discord.Interaction, session_id: str, member: discord.Member):
        s = self.get_session(session_id)
        if not s:
            await interaction.response.send_message("見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s["gm_id"]:
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return
        ok, msg = self.remove_player(session_id, member.id)
        await interaction.response.send_message(msg, ephemeral=True)
        if ok:
            await self.refresh_panel(session_id, interaction=interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionChannelsCog(bot))
