# cogs/ho_select.py
# HO選択 → ニックネーム変更（HO名＠元の名前） → 個別ch自動作成（GM含む）

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

# =========================
# 定数・パス
# =========================
DATA_DIR = "data"
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")
JST = timezone(timedelta(hours=9))


# =========================
# Utility
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


def parse_hos(text: str) -> List[str]:
    return [x.strip() for x in text.split(",") if x.strip()][:25]


def jst_date() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def safe_channel_name(text: str, max_len: int = 90) -> str:
    s = text.strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\wぁ-んァ-ン一-龥ー\-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len].lower() if s else "personal"


async def set_nickname_ho(member: discord.Member, ho: str) -> tuple[bool, str]:
    """
    ニックネームを「HO名＠元の名前」に変更
    """
    base = member.name
    new_nick = f"{ho}＠{base}"
    if len(new_nick) > 32:
        new_nick = new_nick[:32]

    try:
        await member.edit(nick=new_nick, reason="HO selected")
        return True, f"ニックネームを **{new_nick}** に変更しました。"
    except discord.Forbidden:
        return False, "権限不足でニックネームを変更できません（Manage Nicknames / ロール順位）。"
    except Exception as e:
        return False, f"ニックネーム変更に失敗: {e}"


# =========================
# Cog 本体
# =========================
class HOSelectCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_data_dir()

        # 永続View復元
        db = load_db()
        for sid, s in db.get("sessions", {}).items():
            if s.get("ho_panel_message_id"):
                bot.add_view(HOSelectView(self, sid))

    # ---------- DB helpers ----------
    def get_session(self, session_id: str) -> Optional[dict]:
        return load_db().get("sessions", {}).get(session_id)

    def save_session(self, session: dict):
        db = load_db()
        db.setdefault("sessions", {})[session["id"]] = session
        save_db(db)

    # ---------- HO割当 ----------
    def assign_ho(self, session_id: str, user_id: int, ho: str) -> Tuple[bool, str]:
        s = self.get_session(session_id)
        if not s:
            return False, "セッションが見つかりません。"

        if ho not in (s.get("ho_options") or []):
            return False, "そのHOは候補にありません。"

        assignments = s.setdefault("ho_assignments", {})
        taken = s.setdefault("ho_taken", {})
        uid = str(user_id)

        if assignments.get(uid) == ho:
            return False, f"すでに **{ho}** を選択しています。"

        if ho in taken and taken[ho] != uid:
            return False, f"そのHO（**{ho}**）は既に使用されています。"

        old = assignments.get(uid)
        if old and taken.get(old) == uid:
            del taken[old]

        assignments[uid] = ho
        taken[ho] = uid
        self.save_session(s)
        return True, f"HOを **{ho}** に設定しました。"

    # ---------- チャンネル生成 ----------
    async def ensure_category(self, guild: discord.Guild, session: dict) -> discord.CategoryChannel:
        cid = session.get("ho_category_id")
        if cid:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.CategoryChannel):
                return ch

        cat = await guild.create_category(f"🧩HO個別：{session.get('name','session')}")
        session["ho_category_id"] = cat.id
        self.save_session(session)
        return cat

    async def create_or_update_personal_channel(
        self,
        guild: discord.Guild,
        session: dict,
        member: discord.Member,
        ho: str,
    ) -> discord.TextChannel:
        gm = guild.get_member(session["gm_id"])
        if not gm:
            raise RuntimeError("GMが見つかりません。")

        cat = await self.ensure_category(guild, session)
        date = jst_date()

        raw = f"{ho}-{member.display_name}-{date}"
        name = safe_channel_name(raw)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            gm: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        topic = f"Session:{session['id']} HO:{ho} Player:{member.id} GM:{gm.id}"

        record = session.setdefault("ho_personal_channels", {})
        uid = str(member.id)

        if uid in record:
            ch = guild.get_channel(record[uid])
            if isinstance(ch, discord.TextChannel):
                await ch.edit(name=name, overwrites=overwrites, topic=topic)
                return ch

        ch = await cat.create_text_channel(name=name, overwrites=overwrites, topic=topic)
        record[uid] = ch.id
        self.save_session(session)

        await ch.send(
            f"🧩 **HO個別チャンネル**\n"
            f"- HO：**{ho}**\n"
            f"- PL：{member.mention}\n"
            f"- GM：{gm.mention}\n"
            f"- 日付：{date}（JST）"
        )
        return ch

    # ---------- 表示 ----------
    def build_embed(self, session: dict) -> discord.Embed:
        e = discord.Embed(
            title=f"🧩 HO選択：{session.get('name','session')}",
            description=f"Session ID: `{session['id']}`\nGM: <@{session['gm_id']}>",
            color=discord.Color.blurple(),
        )
        e.add_field(
            name="状態",
            value="🔒 ロック中" if session.get("ho_locked") else "🔓 選択可能",
            inline=True,
        )

        hos = session.get("ho_options", [])
        taken = session.get("ho_taken", {})
        lines = []
        for ho in hos:
            lines.append(f"{'✅' if ho in taken else '⬜'} {ho}")
        e.add_field(name="HO一覧", value="\n".join(lines), inline=False)

        e.set_footer(text="HOを選ぶと、ニックネーム変更＋個別chが自動作成されます")
        return e

    async def refresh_panel(self, session_id: str, guild: discord.Guild):
        s = self.get_session(session_id)
        if not s:
            return
        ch = guild.get_channel(s.get("ho_panel_channel_id", 0))
        if not isinstance(ch, discord.TextChannel):
            return
        msg = await ch.fetch_message(s["ho_panel_message_id"])
        await msg.edit(embed=self.build_embed(s), view=HOSelectView(self, session_id))

    # ---------- Slash commands ----------
    @app_commands.command(name="ho_setup")
    async def ho_setup(self, interaction: discord.Interaction, session_id: str, hos: str):
        s = self.get_session(session_id)
        if not s or interaction.user.id != s.get("gm_id"):
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return

        s["ho_options"] = parse_hos(hos)
        s["ho_assignments"] = {}
        s["ho_taken"] = {}
        s["ho_personal_channels"] = {}
        s["ho_locked"] = False
        self.save_session(s)

        await interaction.response.send_message("✅ HO候補を登録しました。", ephemeral=True)

    @app_commands.command(name="ho_panel")
    async def ho_panel(self, interaction: discord.Interaction, session_id: str):
        s = self.get_session(session_id)
        if not s or interaction.user.id != s.get("gm_id"):
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return

        view = HOSelectView(self, session_id)
        self.bot.add_view(view)
        await interaction.response.send_message(embed=self.build_embed(s), view=view)

        msg = await interaction.original_response()
        s["ho_panel_channel_id"] = interaction.channel_id
        s["ho_panel_message_id"] = msg.id
        self.save_session(s)


# =========================
# UI
# =========================
class HOSelect(discord.ui.Select):
    def __init__(self, cog: HOSelectCog, session_id: str):
        self.cog = cog
        self.session_id = session_id
        s = cog.get_session(session_id)
        options = [discord.SelectOption(label=ho, value=ho) for ho in s.get("ho_options", [])]

        super().__init__(
            placeholder="HOを選択",
            options=options,
            min_values=1,
            max_values=1,
            custom_id=f"ho_select:{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        s = self.cog.get_session(self.session_id)
        if not s or s.get("ho_locked"):
            await interaction.response.send_message("HO選択はできません。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        ok, msg = self.cog.assign_ho(self.session_id, interaction.user.id, self.values[0])
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return

        nick_ok, nick_msg = await set_nickname_ho(interaction.user, self.values[0])

        try:
            ch = await self.cog.create_or_update_personal_channel(
                interaction.guild, s, interaction.user, self.values[0]
            )
            await interaction.followup.send(
                f"{msg}\n{nick_msg if nick_ok else '⚠️ ' + nick_msg}\n個別ch：{ch.mention}",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"{msg}\n⚠️ チャンネル作成失敗: {e}", ephemeral=True)

        await self.cog.refresh_panel(self.session_id, interaction.guild)


class HOSelectView(discord.ui.View):
    def __init__(self, cog: HOSelectCog, session_id: str):
        super().__init__(timeout=None)
        self.add_item(HOSelect(cog, session_id))


async def setup(bot: commands.Bot):
    await bot.add_cog(HOSelectCog(bot))
