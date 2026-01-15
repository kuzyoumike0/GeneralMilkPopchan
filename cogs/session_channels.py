# cogs/session_channels.py
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

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
    # 例: 20260116-083012-1234567890
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{ts}-{guild_id}"


def safe_channel_name(name: str) -> str:
    """
    Discord channel name rules:
    - lower case recommended
    - only [a-z0-9-] ideally, but Discord allows more; still, we sanitize for safety.
    """
    name = name.strip()

    # 全角スペース等 → 半角スペース → ハイフン
    name = re.sub(r"\s+", "-", name)

    # 記号を削る（日本語は残してOKだが、ここではより安全に）
    # 日本語も通すなら下の行を緩めてOK。
    name = re.sub(r"[^0-9A-Za-zぁ-んァ-ン一-龥ー\-]", "", name)

    # 連続ハイフン整理
    name = re.sub(r"-{2,}", "-", name).strip("-")

    if not name:
        name = "player"

    # 100文字制限（discordは実際は100）
    return name[:90]


async def ensure_unique_text_channel(
    category: discord.CategoryChannel,
    base_name: str,
    overwrites: dict,
    topic: str,
) -> discord.TextChannel:
    """
    base_name が衝突したら -2, -3 を付けてユニーク化して作る
    """
    existing = {c.name for c in category.text_channels}
    name = base_name
    if name in existing:
        i = 2
        while f"{base_name}-{i}" in existing:
            i += 1
        name = f"{base_name}-{i}"

    ch = await category.create_text_channel(
        name=name,
        overwrites=overwrites,
        topic=topic[:1024],
        reason="session auto build",
    )
    return ch


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

    @discord.ui.button(label="チャンネル作成", style=discord.ButtonStyle.primary, custom_id="session_build")
    async def build(self, interaction: discord.Interaction, button: discord.ui.Button):
        # GMのみ
        s = self.cog.get_session(self.session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s["gm_id"]:
            await interaction.response.send_message("この操作はGMのみ実行できます。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.cog.build_channels(self.session_id, interaction.guild)
            await interaction.followup.send(result, ephemeral=True)
            await self._refresh_panel(interaction)
        except Exception as e:
            await interaction.followup.send(f"作成中にエラー: {e}", ephemeral=True)

    @discord.ui.button(label="ロック/解除", style=discord.ButtonStyle.danger, custom_id="session_lock")
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

        # 永続View（再起動してもボタン生きる）
        # ※ 既存セッション全部にViewを復元
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

        cat = session.get("category_id")
        if cat:
            e.add_field(name="カテゴリ", value=f"<#{cat}>", inline=False)

        all_ch = session.get("channel_all_id")
        if all_ch:
            e.add_field(name="全体", value=f"<#{all_ch}>", inline=True)
        gm_ch = session.get("channel_gm_id")
        if gm_ch:
            e.add_field(name="GM", value=f"<#{gm_ch}>", inline=True)

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

    async def build_channels(self, session_id: str, guild: discord.Guild) -> str:
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

        everyone = guild.default_role

        # 既にカテゴリ作ってたら再利用（IDが残ってる場合）
        category = None
        if s.get("category_id"):
            category = guild.get_channel(s["category_id"])
            if category and not isinstance(category, discord.CategoryChannel):
                category = None

        if category is None:
            # カテゴリ新規作成
            cat_name = f"🎭{s['name']}"
            category = await guild.create_category(name=cat_name, reason="session auto build")
            s["category_id"] = category.id

        # 全体ch
        if not s.get("channel_all_id") or not guild.get_channel(s["channel_all_id"]):
            overwrites_all = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }
            # GM＋参加者を許可
            overwrites_all[gm_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            for uid in players:
                m = guild.get_member(uid)
                if m:
                    overwrites_all[m] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

            ch_all = await ensure_unique_text_channel(
                category,
                base_name=f"全体-{safe_channel_name(s['name'])}",
                overwrites=overwrites_all,
                topic=f"Session {s['id']} / 全体",
            )
            s["channel_all_id"] = ch_all.id

        # GM ch
        if not s.get("channel_gm_id") or not guild.get_channel(s["channel_gm_id"]):
            overwrites_gm = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                gm_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }
            ch_gm = await ensure_unique_text_channel(
                category,
                base_name=f"gm-{safe_channel_name(s['name'])}",
                overwrites=overwrites_gm,
                topic=f"Session {s['id']} / GM only",
            )
            s["channel_gm_id"] = ch_gm.id

        # 個別ch（表示名をベースにする）
        # 保存形式: {user_id: channel_id}
        indi_map: Dict[str, int] = s.setdefault("individual_channels", {})

        for uid in players:
            key = str(uid)
            # 既存が生きてたらスキップ
            if key in indi_map:
                if guild.get_channel(indi_map[key]):
                    continue

            member = guild.get_member(uid)
            if not member:
                continue

            display = member.display_name
            base = safe_channel_name(display).lower()
            # 「個別-表示名」形式（要望どおり suffix なし）
            base_name = f"個別-{base}"

            overwrites_indi = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                gm_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }

            ch = await ensure_unique_text_channel(
                category,
                base_name=base_name,
                overwrites=overwrites_indi,
                topic=f"Session {s['id']} / 個別 / {member.display_name}",
            )
            indi_map[key] = ch.id

        self.save_session(s)

        # 作ったチャンネルに案内を書く（最初の1回だけ）
        ch_all = guild.get_channel(s["channel_all_id"])
        if isinstance(ch_all, discord.TextChannel):
            await ch_all.send(
                f"セッション **{s['name']}** のチャンネルを作成しました。\n"
                f"GM: <@{gm_id}>\n"
                f"個別chはカテゴリ内に作成済みです。"
            )

        return "✅ チャンネルを作成/更新しました。カテゴリを確認してね。"

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
            "individual_channels": {},
        }
        self.save_session(session)

        embed = self.build_embed(session)
        view = SessionPanelView(self, session_id)

        # 永続View登録（再起動してもOK）
        self.bot.add_view(view)

        await interaction.response.send_message(embed=embed, view=view)
        # 送ったメッセージIDを保存
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
