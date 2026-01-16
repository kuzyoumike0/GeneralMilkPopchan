# cogs/ho_select.py
# 最終統合版
# - /session <name> <PCx> でセッション作成＋HOパネル即投稿
# - HO選択 → nick「PCx＠元名」＋個別ch作成
# - 見学ボタン（見学者ch＋個別ch閲覧のみ）
# - /sessionend <name> で nick復元＋全チャンネル/カテゴリ/パネル削除

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

# =========================
# 定数
# =========================
DATA_DIR = "data"
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")
JST = timezone(timedelta(hours=9))
MAX_PC = 12


# =========================
# DB Utility
# =========================
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


# =========================
# Utility
# =========================
def jst_date() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def make_pc_hos(n: int) -> List[str]:
    if n < 1 or n > MAX_PC:
        raise ValueError("PC数は1〜12")
    return [f"PC{i}" for i in range(1, n + 1)]


def safe_channel_name(text: str) -> str:
    s = re.sub(r"\s+", "-", text.strip())
    s = re.sub(r"[^\wぁ-んァ-ン一-龥ー\-]", "", s)
    return s.lower()[:90] or "channel"


def build_ho_nick(member: discord.Member, ho: str) -> str:
    nick = f"{ho}＠{member.name}"
    return nick[:32]


async def try_set_nickname(member: discord.Member, nick: Optional[str], reason: str):
    try:
        await member.edit(nick=nick, reason=reason)
        return True
    except Exception:
        return False


def is_admin(m: discord.Member) -> bool:
    p = m.guild_permissions
    return p.administrator or p.manage_channels


# =========================
# メインCog
# =========================
class HOSelectCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_data_dir()

        # 永続View復元
        for sid in load_db().get("sessions", {}):
            bot.add_view(HOSelectView(self, sid))

    # ---------- session helpers ----------
    def new_session_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def get_session(self, sid: str) -> Optional[dict]:
        return load_db()["sessions"].get(sid)

    # ---------- category ----------
    async def ensure_category(self, guild: discord.Guild, session: dict, key: str, title: str):
        cid = session.get(key)
        if cid:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.CategoryChannel):
                return ch
        cat = await guild.create_category(title)
        session[key] = cat.id
        return cat

    # ---------- personal channel ----------
    async def create_personal_ch(self, guild, session, member, ho):
        gm = guild.get_member(session["gm_id"])
        cat = await self.ensure_category(
            guild, session, "ho_category_id", f"🧩HO個別：{session['name']}"
        )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True),
            gm: discord.PermissionOverwrite(view_channel=True),
            member: discord.PermissionOverwrite(view_channel=True),
        }

        for uid in session["spectators"]:
            sp = guild.get_member(int(uid))
            if sp:
                overwrites[sp] = discord.PermissionOverwrite(
                    view_channel=True,
                    read_message_history=True,
                    send_messages=False,
                )

        name = safe_channel_name(f"{ho}-{member.display_name}-{jst_date()}")
        ch = await cat.create_text_channel(name=name, overwrites=overwrites)
        session["ho_personal_channels"][str(member.id)] = ch.id
        return ch

    # ---------- spectator ----------
    async def create_spectator_ch(self, guild, session, member):
        gm = guild.get_member(session["gm_id"])
        cat = await self.ensure_category(
            guild, session, "spectator_category_id", f"👀見学：{session['name']}"
        )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True),
            gm: discord.PermissionOverwrite(view_channel=True),
            member: discord.PermissionOverwrite(view_channel=True),
        }

        name = safe_channel_name(f"見学-{member.display_name}")
        ch = await cat.create_text_channel(name=name, overwrites=overwrites)
        session["spectator_channels"][str(member.id)] = ch.id
        return ch

    # =========================
    # /session
    # =========================
    @app_commands.command(name="session", description="セッション作成（/session 第1話 PC6）")
    async def session(self, interaction: discord.Interaction, name: str, pc: str):
        if not interaction.guild:
            return

        m = re.fullmatch(r"pc(\d{1,2})", pc.lower())
        if not m:
            await interaction.response.send_message("PC指定は PC1〜PC12", ephemeral=True)
            return

        pc_count = int(m.group(1))
        hos = make_pc_hos(pc_count)

        db = load_db()
        sid = self.new_session_id()
        while sid in db["sessions"]:
            sid = self.new_session_id()

        session = {
            "id": sid,
            "name": name,
            "gm_id": interaction.user.id,
            "pc_count": pc_count,
            "ho_options": hos,
            "ho_assignments": {},
            "ho_taken": {},
            "ho_personal_channels": {},
            "original_nicks": {},
            "spectators": [],
            "spectator_channels": {},
            "ho_category_id": None,
            "spectator_category_id": None,
            "panel_channel_id": None,
            "panel_message_id": None,
        }

        db["sessions"][sid] = session
        save_db(db)

        view = HOSelectView(self, sid)
        self.bot.add_view(view)

        await interaction.response.send_message(
            f"✅ セッション **{name}** を作成しました（PC1〜PC{pc_count}）",
            ephemeral=True,
        )

        panel = await interaction.channel.send(
            embed=self.build_embed(session),
            view=view,
        )

        session["panel_channel_id"] = interaction.channel.id
        session["panel_message_id"] = panel.id
        save_db(db)

    # =========================
    # /sessionend
    # =========================
    @app_commands.command(name="sessionend", description="セッション終了（全削除）")
    async def sessionend(self, interaction: discord.Interaction, name: str):
        if not interaction.guild:
            return

        db = load_db()
        target = None
        for s in db["sessions"].values():
            if s["name"] == name and (
                s["gm_id"] == interaction.user.id or is_admin(interaction.user)
            ):
                target = s
                break

        if not target:
            await interaction.response.send_message("セッションが見つかりません", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # nick restore
        for uid, orig in target["original_nicks"].items():
            m = interaction.guild.get_member(int(uid))
            if m:
                await try_set_nickname(m, orig, "session end")

        # delete channels
        for cid in list(target["ho_personal_channels"].values()):
            ch = interaction.guild.get_channel(cid)
            if ch:
                await ch.delete()

        for cid in list(target["spectator_channels"].values()):
            ch = interaction.guild.get_channel(cid)
            if ch:
                await ch.delete()

        for key in ("ho_category_id", "spectator_category_id"):
            cid = target.get(key)
            if cid:
                cat = interaction.guild.get_channel(cid)
                if cat:
                    for ch in list(cat.channels):
                        await ch.delete()
                    await cat.delete()

        # delete panel
        try:
            ch = interaction.guild.get_channel(target["panel_channel_id"])
            if ch:
                msg = await ch.fetch_message(target["panel_message_id"])
                await msg.delete()
        except Exception:
            pass

        del db["sessions"][target["id"]]
        save_db(db)

        await interaction.followup.send(f"🧹 セッション **{name}** を完全に終了しました", ephemeral=True)

    # ---------- embed ----------
    def build_embed(self, session: dict) -> discord.Embed:
        e = discord.Embed(
            title=f"🧩 HO選択：{session['name']}",
            description=f"PC人数：{session['pc_count']}",
        )
        lines = []
        for ho in session["ho_options"]:
            mark = "✅" if ho in session["ho_taken"] else "⬜"
            lines.append(f"{mark} {ho}")
        e.add_field(name="PC一覧", value="\n".join(lines))
        return e


# =========================
# UI
# =========================
class HOSelect(discord.ui.Select):
    def __init__(self, cog: HOSelectCog, sid: str):
        self.cog = cog
        self.sid = sid
        s = cog.get_session(sid)
        super().__init__(
            placeholder="PCを選択",
            options=[discord.SelectOption(label=h) for h in s["ho_options"]],
        )

    async def callback(self, interaction: discord.Interaction):
        s = self.cog.get_session(self.sid)
        ho = self.values[0]

        if ho in s["ho_taken"]:
            await interaction.response.send_message("そのPCは使用済みです", ephemeral=True)
            return

        uid = str(interaction.user.id)
        s["ho_assignments"][uid] = ho
        s["ho_taken"][ho] = uid

        if uid not in s["original_nicks"]:
            s["original_nicks"][uid] = interaction.user.nick

        await try_set_nickname(interaction.user, build_ho_nick(interaction.user, ho), "HO select")

        ch = await self.cog.create_personal_ch(interaction.guild, s, interaction.user, ho)

        save_db(load_db())

        await interaction.response.send_message(
            f"{ho} を選択しました\n個別ch：{ch.mention}",
            ephemeral=True,
        )


class HOSelectView(discord.ui.View):
    def __init__(self, cog: HOSelectCog, sid: str):
        super().__init__(timeout=None)
        self.add_item(HOSelect(cog, sid))

    @discord.ui.button(label="👀 見学する / 解除", style=discord.ButtonStyle.secondary)
    async def spectate(self, interaction: discord.Interaction, _):
        s = self.cog.get_session(self.sid)
        uid = str(interaction.user.id)

        if uid not in s["spectators"]:
            s["spectators"].append(uid)
            await self.cog.create_spectator_ch(interaction.guild, s, interaction.user)
            await interaction.response.send_message("見学を開始しました", ephemeral=True)
        else:
            s["spectators"].remove(uid)
            await interaction.response.send_message("見学を解除しました", ephemeral=True)

        save_db(load_db())


async def setup(bot: commands.Bot):
    await bot.add_cog(HOSelectCog(bot))
