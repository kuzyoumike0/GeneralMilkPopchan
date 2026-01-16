# cogs/ho_select.py
# ✅ HO選択（ドロップダウン）
# ✅ HO選択 → ニックネーム変更（HO名＠元の名前）→ 個別ch自動作成（GM含む）
# ✅ 見学ロールは使わない
# ✅ HOパネルに「👀見学する」ボタン
#    - 見学ボタン押下で、見学者専用chを作成（GM+本人+Bot）
#    - セッションの個別ch（HO個別ch）を見学者に「閲覧のみ」で付与
# ✅ セッション終了で一括ニックネーム復元（/session_end）

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
    """
    Discordチャンネル名として安全な形に。
    日本語OK / 空白→ハイフン / 記号を軽く除去
    """
    s = (text or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\wぁ-んァ-ン一-龥ー\-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len].lower() if s else "channel"


def build_ho_nick(member: discord.Member, ho: str) -> str:
    """
    ニックネーム：HO名＠元の名前（元の名前はアカウント名）
    """
    base = member.name
    new_nick = f"{ho}＠{base}"
    if len(new_nick) > 32:
        new_nick = new_nick[:32]
    return new_nick


async def try_set_nickname(member: discord.Member, nick: Optional[str], reason: str) -> Tuple[bool, str]:
    """
    nick: None を渡すとニックネーム解除
    """
    try:
        await member.edit(nick=nick, reason=reason)
        if nick is None:
            return True, "ニックネームを元に戻しました。"
        return True, f"ニックネームを **{nick}** に変更しました。"
    except discord.Forbidden:
        return False, "権限不足でニックネームを変更できません（Manage Nicknames / ロール順位）。"
    except Exception as e:
        return False, f"ニックネーム変更に失敗: {e}"


def is_admin(member: discord.Member) -> bool:
    p = member.guild_permissions
    return p.administrator or p.manage_channels


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

    # ---------- カテゴリ生成 ----------
    async def ensure_ho_category(self, guild: discord.Guild, session: dict) -> discord.CategoryChannel:
        cid = session.get("ho_category_id")
        if cid:
            ch = guild.get_channel(int(cid))
            if isinstance(ch, discord.CategoryChannel):
                return ch

        cat = await guild.create_category(f"🧩HO個別：{session.get('name','session')}")
        session["ho_category_id"] = cat.id
        self.save_session(session)
        return cat

    async def ensure_spectator_category(self, guild: discord.Guild, session: dict) -> discord.CategoryChannel:
        cid = session.get("spectator_category_id")
        if cid:
            ch = guild.get_channel(int(cid))
            if isinstance(ch, discord.CategoryChannel):
                return ch

        cat = await guild.create_category(f"👀見学：{session.get('name','session')}")
        session["spectator_category_id"] = cat.id
        self.save_session(session)
        return cat

    # ---------- 見学者チャンネル ----------
    async def create_or_update_spectator_channel(
        self,
        guild: discord.Guild,
        session: dict,
        spectator: discord.Member,
    ) -> discord.TextChannel:
        gm = guild.get_member(int(session["gm_id"]))
        if not gm:
            raise RuntimeError("GMが見つかりません。")

        cat = await self.ensure_spectator_category(guild, session)

        # 見学者専用ch（見学-ユーザ名）
        raw = f"見学-{spectator.display_name}"
        ch_name = safe_channel_name(raw)

        everyone = guild.default_role
        overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            gm: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            spectator: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        topic = f"Session:{session['id']} Spectator:{spectator.id} GM:{gm.id}"

        # 既存を探す（記録優先→名前一致）
        record = session.setdefault("spectator_channels", {})  # user_id(str)->channel_id
        uid_s = str(spectator.id)

        if uid_s in record:
            ch = guild.get_channel(int(record[uid_s]))
            if isinstance(ch, discord.TextChannel):
                await ch.edit(name=ch_name, overwrites=overwrites, topic=topic, reason="spectator ch update")
                return ch

        for ch in cat.text_channels:
            if ch.name == ch_name:
                record[uid_s] = ch.id
                self.save_session(session)
                await ch.edit(overwrites=overwrites, topic=topic, reason="spectator ch perms update")
                return ch

        new_ch = await cat.create_text_channel(
            name=ch_name,
            overwrites=overwrites,
            topic=topic,
            reason="spectator ch create",
        )
        record[uid_s] = new_ch.id
        self.save_session(session)

        await new_ch.send(
            f"👀 **見学者チャンネル** を作成しました。\n"
            f"- 見学者：{spectator.mention}\n"
            f"- GM：{gm.mention}\n"
            f"- このセッションの個別chは “閲覧のみ” で見られます。"
        )
        return new_ch

    # ---------- 見学権限を個別chへ付与（閲覧のみ） ----------
    async def apply_spectator_to_all_personals(
        self,
        guild: discord.Guild,
        session: dict,
        spectator: discord.Member,
        *,
        enable: bool,
    ) -> Tuple[int, int]:
        """
        enable=True: 見学者を全個別chに追加（閲覧のみ）
        enable=False: 見学者を全個別chから削除
        returns: (updated_count, failed_count)
        """
        updated = 0
        failed = 0

        personal_map = session.get("ho_personal_channels") or {}
        ch_ids = [int(cid) for cid in personal_map.values()]

        for cid in ch_ids:
            ch = guild.get_channel(cid)
            if not isinstance(ch, discord.TextChannel):
                continue
            try:
                ow = ch.overwrites
                if enable:
                    ow[spectator] = discord.PermissionOverwrite(
                        view_channel=True,
                        read_message_history=True,
                        send_messages=False,
                    )
                else:
                    # remove overwrite
                    if spectator in ow:
                        del ow[spectator]
                await ch.edit(overwrites=ow, reason="spectator perms sync")
                updated += 1
            except Exception:
                failed += 1

        return updated, failed

    # ---------- 個別ch生成（見学者を自動反映） ----------
    async def create_or_update_personal_channel(
        self,
        guild: discord.Guild,
        session: dict,
        member: discord.Member,
        ho: str,
    ) -> discord.TextChannel:
        gm = guild.get_member(int(session["gm_id"]))
        if not gm:
            raise RuntimeError("GMが見つかりません。")

        cat = await self.ensure_ho_category(guild, session)
        date = jst_date()

        raw = f"{ho}-{member.display_name}-{date}"
        name = safe_channel_name(raw)

        everyone = guild.default_role
        overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            gm: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        # ✅ 現在の見学者を “閲覧のみ” で付与
        spectators = session.get("spectators") or []
        for uid_s in spectators:
            m = guild.get_member(int(uid_s))
            if m:
                overwrites[m] = discord.PermissionOverwrite(
                    view_channel=True,
                    read_message_history=True,
                    send_messages=False,
                )

        topic = f"Session:{session['id']} HO:{ho} Player:{member.id} GM:{gm.id}"

        record = session.setdefault("ho_personal_channels", {})
        uid = str(member.id)

        # 既存が記録されていれば更新
        if uid in record:
            ch = guild.get_channel(int(record[uid]))
            if isinstance(ch, discord.TextChannel):
                await ch.edit(name=name, overwrites=overwrites, topic=topic, reason="HO personal update")
                return ch

        # なければ作成
        ch = await cat.create_text_channel(name=name, overwrites=overwrites, topic=topic, reason="HO personal create")
        record[uid] = ch.id
        self.save_session(session)

        await ch.send(
            f"🧩 **HO個別チャンネル**\n"
            f"- HO：**{ho}**\n"
            f"- PL：{member.mention}\n"
            f"- GM：{gm.mention}\n"
            f"- 日付：{date}（JST）\n"
            f"- 見学者：{len(spectators)}人（閲覧のみ）"
        )
        return ch

    # ---------- 表示 ----------
    def build_embed(self, session: dict) -> discord.Embed:
        spectators = session.get("spectators") or []

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
        e.add_field(name="見学者", value=f"{len(spectators)}人（ボタンで参加/解除）", inline=True)

        hos = session.get("ho_options", [])
        taken = session.get("ho_taken", {})
        lines = []
        for ho in hos:
            lines.append(f"{'✅' if ho in taken else '⬜'} {ho}")
        e.add_field(name="HO一覧", value="\n".join(lines) if lines else "（未設定）", inline=False)

        e.set_footer(text="HOを選ぶと、ニックネーム変更＋個別ch作成。見学はボタンで追加。")
        return e

    async def refresh_panel(self, session_id: str, guild: discord.Guild):
        s = self.get_session(session_id)
        if not s:
            return
        ch = guild.get_channel(int(s.get("ho_panel_channel_id", 0)))
        if not isinstance(ch, discord.TextChannel):
            return
        msg = await ch.fetch_message(int(s["ho_panel_message_id"]))
        await msg.edit(embed=self.build_embed(s), view=HOSelectView(self, session_id))

    # =========================
    # Slash commands
    # =========================
    @app_commands.command(name="ho_setup", description="HO候補を登録します（GM用）")
    @app_commands.describe(session_id="セッションID", hos="HO候補（カンマ区切り）")
    async def ho_setup(self, interaction: discord.Interaction, session_id: str, hos: str):
        s = self.get_session(session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id"):
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return

        s["ho_options"] = parse_hos(hos)
        s["ho_assignments"] = {}
        s["ho_taken"] = {}
        s["ho_personal_channels"] = {}
        s["ho_locked"] = False

        # 元ニック退避（user_id(str)-> original nick or None）
        s["original_nicks"] = {}

        # 見学者関連
        s["spectators"] = []
        s["spectator_channels"] = {}
        # s["spectator_category_id"] は必要なら作る

        self.save_session(s)
        await interaction.response.send_message("✅ HO候補を登録しました。", ephemeral=True)

    @app_commands.command(name="ho_panel", description="HO選択パネルを投稿します（GM用）")
    @app_commands.describe(session_id="セッションID")
    async def ho_panel(self, interaction: discord.Interaction, session_id: str):
        s = self.get_session(session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id"):
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return

        view = HOSelectView(self, session_id)
        self.bot.add_view(view)

        await interaction.response.send_message(embed=self.build_embed(s), view=view)

        msg = await interaction.original_response()
        s["ho_panel_channel_id"] = interaction.channel_id
        s["ho_panel_message_id"] = msg.id
        self.save_session(s)

    @app_commands.command(name="session_end", description="セッション終了：参加者のニックネームを一括復元（GM用）")
    @app_commands.describe(session_id="セッションID", lock="終了後にHO選択をロックする")
    async def session_end(self, interaction: discord.Interaction, session_id: str, lock: bool = True):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        s = self.get_session(session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id"):
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        restored = 0
        failed = 0
        fail_lines: List[str] = []

        original_nicks: Dict[str, Optional[str]] = s.get("original_nicks") or {}
        for uid_s, orig in original_nicks.items():
            m = interaction.guild.get_member(int(uid_s))
            if not m:
                continue

            ok, msg = await try_set_nickname(m, orig, reason=f"Session end restore (session {session_id})")
            if ok:
                restored += 1
            else:
                failed += 1
                fail_lines.append(f"- {m.mention}: {msg}")

        if lock:
            s["ho_locked"] = True

        self.save_session(s)

        # パネル更新
        try:
            await self.refresh_panel(session_id, interaction.guild)
        except Exception:
            pass

        text = f"✅ セッション終了：ニックネーム復元 完了\n復元: {restored} / 失敗: {failed}"
        if fail_lines:
            joined = "\n".join(fail_lines[:15])
            if len(fail_lines) > 15:
                joined += f"\n…他 {len(fail_lines)-15}件"
            text += "\n\n⚠️ 失敗一覧:\n" + joined

        await interaction.followup.send(text, ephemeral=True)


# =========================
# UI
# =========================
class HOSelect(discord.ui.Select):
    def __init__(self, cog: HOSelectCog, session_id: str):
        self.cog = cog
        self.session_id = session_id
        s = cog.get_session(session_id) or {}
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
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if s.get("ho_locked"):
            await interaction.response.send_message("HO選択はロックされています。", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        ho = self.values[0]
        ok, msg = self.cog.assign_ho(self.session_id, interaction.user.id, ho)
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return

        # 元nickを最初の1回だけ保存（None = nick未設定）
        originals: Dict[str, Optional[str]] = s.setdefault("original_nicks", {})
        uid_s = str(interaction.user.id)
        if uid_s not in originals:
            originals[uid_s] = interaction.user.nick

        # ニックネーム変更
        desired_nick = build_ho_nick(interaction.user, ho)
        nick_ok, nick_msg = await try_set_nickname(interaction.user, desired_nick, reason="HO selected")

        # 個別ch作成/更新
        try:
            ch = await self.cog.create_or_update_personal_channel(interaction.guild, s, interaction.user, ho)
            self.cog.save_session(s)
            await interaction.followup.send(
                f"{msg}\n{nick_msg if nick_ok else '⚠️ ' + nick_msg}\n個別ch：{ch.mention}",
                ephemeral=True,
            )
        except Exception as e:
            self.cog.save_session(s)
            await interaction.followup.send(
                f"{msg}\n{nick_msg if nick_ok else '⚠️ ' + nick_msg}\n⚠️ チャンネル作成失敗: {e}",
                ephemeral=True,
            )

        # パネル更新
        try:
            await self.cog.refresh_panel(self.session_id, interaction.guild)
        except Exception:
            pass


class HOSelectView(discord.ui.View):
    def __init__(self, cog: HOSelectCog, session_id: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        self.add_item(HOSelect(cog, session_id))

    @discord.ui.button(label="👀 見学する / 解除", style=discord.ButtonStyle.secondary, custom_id="session_toggle_spectate")
    async def toggle_spectate(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.cog.get_session(self.session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        spectators: List[str] = s.setdefault("spectators", [])
        uid_s = str(interaction.user.id)

        # 追加/解除
        enable = uid_s not in spectators
        if enable:
            spectators.append(uid_s)
        else:
            spectators.remove(uid_s)

        # 見学者専用ch
        spectator_ch_msg = ""
        try:
            if enable:
                sch = await self.cog.create_or_update_spectator_channel(interaction.guild, s, interaction.user)
                spectator_ch_msg = f"\n✅ 見学者ch：{sch.mention}"
        except Exception as e:
            spectator_ch_msg = f"\n⚠️ 見学者ch作成に失敗: {e}"

        # 個別chへ閲覧権限を反映
        try:
            updated, failed = await self.cog.apply_spectator_to_all_personals(
                interaction.guild, s, interaction.user, enable=enable
            )
            perm_msg = f"\n個別ch権限：更新 {updated} / 失敗 {failed}"
        except Exception as e:
            perm_msg = f"\n⚠️ 個別ch権限反映に失敗: {e}"

        self.cog.save_session(s)

        await interaction.followup.send(
            ("👀 見学を **開始**しました。" if enable else "👀 見学を **解除**しました。")
            + spectator_ch_msg
            + perm_msg,
            ephemeral=True
        )

        # パネル更新
        try:
            await self.cog.refresh_panel(self.session_id, interaction.guild)
        except Exception:
            pass

    @discord.ui.button(label="🔒 HOロック/解除（GM）", style=discord.ButtonStyle.danger, custom_id="session_toggle_lock")
    async def toggle_lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.cog.get_session(self.session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id") and not is_admin(interaction.user):
            await interaction.response.send_message("GM（または管理者）のみ操作できます。", ephemeral=True)
            return

        s["ho_locked"] = not s.get("ho_locked", False)
        self.cog.save_session(s)

        await interaction.response.send_message(
            f"HO選択を **{'ロック' if s['ho_locked'] else '解除'}** しました。",
            ephemeral=True
        )
        try:
            await self.cog.refresh_panel(self.session_id, interaction.guild)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(HOSelectCog(bot))
