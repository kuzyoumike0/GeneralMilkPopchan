# cogs/ho_select.py
# ✅ /setup <name> <PCx> でセッション作成 + 共有ch(VC参加者全員)作成 + HOパネルを共有chへ投稿
# ✅ HO選択 → nick「PCx＠元名」+ 個別ch作成（GM含む）
# ✅ HO選択時に「共有ch権限」も自動更新（選んだ人を追加）
# ✅ 見学ボタン（見学者ch作成 + 個別ch閲覧のみ権限）
# ✅ パネルに
#    - 🗄️アーカイブ（閲覧のみ）(確認付き)
#    - 🧨完全削除（危険）(二段階確認)
# ✅ /sessionend <name> で nick復元 + 全削除（個別/見学/共有/カテゴリ/パネル/DB）
#
# ✅ 追加仕様:
# - /setup 実行者が入っているVCと同じカテゴリに、VCの“上”へチャンネルを積む
# - 積む順番を固定: 共有 → 個別(PC順) → 見学 → VC

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
    s = re.sub(r"\s+", "-", (text or "").strip())
    s = re.sub(r"[^\wぁ-んァ-ン一-龥ー\-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s.lower()[:90] or "channel")


def build_ho_nick(member: discord.Member, ho: str) -> str:
    nick = f"{ho}＠{member.name}"
    return nick[:32]


async def try_set_nickname(member: discord.Member, nick: Optional[str], reason: str) -> Tuple[bool, str]:
    try:
        await member.edit(nick=nick, reason=reason)
        if nick is None:
            return True, "ニックネームを元に戻しました。"
        return True, f"ニックネームを **{nick}** に変更しました。"
    except discord.Forbidden:
        return False, "権限不足でニックネームを変更できません（Manage Nicknames/ロール順位）。"
    except Exception as e:
        return False, f"ニックネーム変更に失敗: {e}"


def is_admin(m: discord.Member) -> bool:
    p = m.guild_permissions
    return p.administrator or p.manage_channels


def parse_pc_count(pc_text: str) -> Optional[int]:
    m = re.fullmatch(r"pc(\d{1,2})", (pc_text or "").strip(), re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1))
    if 1 <= n <= MAX_PC:
        return n
    return None


# =========================
# メインCog
# =========================
class HOSelectCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_data_dir()

        # 永続View復元（パネルがあるセッションのみ）
        db = load_db()
        for sid, s in db.get("sessions", {}).items():
            if s.get("panel_message_id"):
                bot.add_view(HOSelectView(self, sid))

    # ---------- session helpers ----------
    def new_session_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def get_session(self, sid: str) -> Optional[dict]:
        db = load_db()
        return db.get("sessions", {}).get(sid)

    def save_session(self, session: dict):
        db = load_db()
        db.setdefault("sessions", {})[session["id"]] = session
        save_db(db)

    def delete_session_from_db(self, sid: str):
        db = load_db()
        if sid in db.get("sessions", {}):
            del db["sessions"][sid]
            save_db(db)

    def find_session_by_name(self, name: str, requester: discord.Member) -> Optional[dict]:
        db = load_db()
        sessions = list(db.get("sessions", {}).values())

        # GM本人優先
        for s in sessions:
            if s.get("name") == name and s.get("gm_id") == requester.id:
                return s

        # 管理者なら同名の最初のもの
        if is_admin(requester):
            for s in sessions:
                if s.get("name") == name:
                    return s

        return None

    # ---------- embed/panel ----------
    def build_embed(self, session: dict) -> discord.Embed:
        archived = bool(session.get("archived", False))
        e = discord.Embed(
            title=f"🧩 HO選択：{session.get('name','session')}",
            description=f"Session ID: `{session.get('id')}`\nGM: <@{session.get('gm_id')}>",
            color=discord.Color.blurple(),
        )
        e.add_field(name="PC人数", value=str(session.get("pc_count", "未設定")), inline=True)
        e.add_field(name="状態", value=("🗄️ アーカイブ" if archived else "🟢 進行中"), inline=True)
        e.add_field(name="見学者", value=f"{len(session.get('spectators') or [])}人", inline=True)

        taken = session.get("ho_taken") or {}
        lines = []
        for ho in (session.get("ho_options") or []):
            lines.append(f"{'✅' if ho in taken else '⬜'} {ho}")
        e.add_field(name="PC一覧", value=("\n".join(lines) if lines else "（未設定）"), inline=False)

        e.set_footer(text="PCを選ぶと、ニックネーム変更＋個別ch作成。見学はボタンで追加。")
        return e

    async def refresh_panel(self, sid: str, guild: discord.Guild):
        s = self.get_session(sid)
        if not s:
            return
        ch_id = s.get("panel_channel_id")
        msg_id = s.get("panel_message_id")
        if not ch_id or not msg_id:
            return
        ch = guild.get_channel(int(ch_id))
        if not isinstance(ch, discord.TextChannel):
            return
        try:
            msg = await ch.fetch_message(int(msg_id))
        except discord.NotFound:
            return
        await msg.edit(embed=self.build_embed(s), view=HOSelectView(self, sid))

    # ---------- anchor / ordering ----------
    def _get_anchor_vc_and_category(
        self, guild: discord.Guild, session: dict
    ) -> Tuple[Optional[discord.VoiceChannel], Optional[discord.CategoryChannel]]:
        vc = None
        cat = None

        vc_id = session.get("anchor_vc_id")
        if vc_id:
            ch = guild.get_channel(int(vc_id))
            if isinstance(ch, discord.VoiceChannel):
                vc = ch

        cat_id = session.get("anchor_category_id")
        if cat_id:
            c = guild.get_channel(int(cat_id))
            if isinstance(c, discord.CategoryChannel):
                cat = c

        # VCが見つかったのにカテゴリIDが未保存/無効なら、VCのカテゴリを採用
        if vc and not cat and vc.category:
            cat = vc.category
            session["anchor_category_id"] = cat.id
            self.save_session(session)

        return vc, cat

    def _base_position_in_anchor_category(
        self, session: dict, anchor_vc: Optional[discord.VoiceChannel], cat: Optional[discord.CategoryChannel]
    ) -> Optional[int]:
        """
        Discordのpositionはカテゴリ内の並び。
        VCの“上”に固定順で積むため、基準position（= VC.position）を返す。
        """
        if not anchor_vc or not cat:
            return None
        try:
            if anchor_vc.category_id == cat.id:
                return max(0, int(anchor_vc.position))
        except Exception:
            pass
        return None

    def _pos_shared(self, base: Optional[int]) -> Optional[int]:
        return base

    def _pos_personal(self, base: Optional[int], session: dict, ho: str) -> Optional[int]:
        if base is None:
            return None
        hos = session.get("ho_options") or []
        try:
            idx = hos.index(ho)
        except ValueError:
            idx = 0
        # 共有の直下から、PC順に並べる
        return base + 1 + idx

    def _pos_spectator(self, base: Optional[int], session: dict, spectator_uid: int) -> Optional[int]:
        if base is None:
            return None
        pc_count = int(session.get("pc_count") or 0)
        specs = [int(x) for x in (session.get("spectators") or [])]
        specs_sorted = sorted(set(specs))
        try:
            idx = specs_sorted.index(int(spectator_uid))
        except ValueError:
            idx = len(specs_sorted)
        # 個別の後ろに見学を並べる
        return base + 1 + pc_count + idx

    # ---------- category ----------
    async def ensure_category(self, guild: discord.Guild, session: dict, key: str, title: str) -> discord.CategoryChannel:
        cid = session.get(key)
        if cid:
            ch = guild.get_channel(int(cid))
            if isinstance(ch, discord.CategoryChannel):
                return ch
        cat = await guild.create_category(title)
        session[key] = cat.id
        self.save_session(session)
        return cat

    async def ensure_archive_category(self, guild: discord.Guild, session: dict) -> discord.CategoryChannel:
        key = "archive_category_id"
        title = f"🗄️アーカイブ：{session.get('name','session')}"
        return await self.ensure_category(guild, session, key, title)

    # ---------- permissions builders ----------
    def _make_personal_overwrites(
        self,
        guild: discord.Guild,
        gm: discord.Member,
        player: discord.Member,
        session: dict,
        *,
        archived: bool,
    ) -> dict:
        everyone = guild.default_role
        ow = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            gm: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            player: discord.PermissionOverwrite(view_channel=True, send_messages=(not archived), read_message_history=True),
        }
        # 見学者は閲覧のみ
        for uid_s in (session.get("spectators") or []):
            m = guild.get_member(int(uid_s))
            if m:
                ow[m] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)
        return ow

    def _make_spectator_overwrites(
        self,
        guild: discord.Guild,
        gm: discord.Member,
        spectator: discord.Member,
        *,
        archived: bool,
    ) -> dict:
        everyone = guild.default_role
        return {
            everyone: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            gm: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            spectator: discord.PermissionOverwrite(view_channel=True, send_messages=(not archived), read_message_history=True),
        }

    def _make_shared_overwrites(
        self,
        guild: discord.Guild,
        gm: discord.Member,
        member_ids: List[int],
        *,
        archived: bool,
    ) -> dict:
        everyone = guild.default_role
        ow = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            gm: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        for uid in member_ids:
            m = guild.get_member(int(uid))
            if m:
                ow[m] = discord.PermissionOverwrite(
                    view_channel=True,
                    read_message_history=True,
                    send_messages=(not archived),
                )
        return ow

    # ---------- shared channel ----------
    async def create_or_update_shared_channel_from_vc(
        self,
        guild: discord.Guild,
        session: dict,
        voice_channel: discord.VoiceChannel,
        *,
        post_panel: bool,
    ) -> discord.TextChannel:
        gm = guild.get_member(int(session["gm_id"]))
        if not gm:
            raise RuntimeError("GMが見つかりません。")

        archived = bool(session.get("archived", False))

        # VC参加者（bot除外）
        vc_members = [m for m in voice_channel.members if not m.bot]
        member_ids = sorted({m.id for m in vc_members} | {gm.id})

        # 保存（参加者リストとしても使う）
        session["participants"] = [str(x) for x in member_ids]
        # anchor再保存（安全）
        session["anchor_vc_id"] = voice_channel.id
        session["anchor_category_id"] = voice_channel.category_id or None
        self.save_session(session)

        # ✅ VCと同じカテゴリに置く（カテゴリが無い場合のみ、従来通りカテゴリ作成）
        anchor_vc = voice_channel
        anchor_cat = voice_channel.category  # None あり
        use_cat = anchor_cat
        if use_cat is None:
            use_cat = await self.ensure_category(
                guild, session, "shared_category_id", f"📣共有：{session.get('name','session')}"
            )

        base = self._base_position_in_anchor_category(session, anchor_vc, use_cat)
        desired_pos = self._pos_shared(base)

        ch_name = safe_channel_name(f"共有-{session.get('name','session')}")
        ow = self._make_shared_overwrites(guild, gm, member_ids, archived=archived)
        topic = f"Session:{session['id']} VC:{voice_channel.id} GM:{gm.id}"

        # 既存があれば更新
        if session.get("shared_channel_id"):
            ch = guild.get_channel(int(session["shared_channel_id"]))
            if isinstance(ch, discord.TextChannel):
                await ch.edit(
                    name=ch_name,
                    category=use_cat,
                    overwrites=ow,
                    topic=topic,
                    position=desired_pos,
                    reason="update shared",
                )
                if post_panel:
                    view = HOSelectView(self, session["id"])
                    self.bot.add_view(view)
                    msg = await ch.send(embed=self.build_embed(session), view=view)
                    session["panel_channel_id"] = ch.id
                    session["panel_message_id"] = msg.id
                    self.save_session(session)
                return ch

        # 無ければ新規作成
        ch = await guild.create_text_channel(
            name=ch_name,
            category=use_cat,
            overwrites=ow,
            topic=topic,
            position=desired_pos,
            reason="create shared",
        )
        session["shared_channel_id"] = ch.id
        self.save_session(session)

        if post_panel:
            view = HOSelectView(self, session["id"])
            self.bot.add_view(view)
            msg = await ch.send(embed=self.build_embed(session), view=view)
            session["panel_channel_id"] = ch.id
            session["panel_message_id"] = msg.id
            self.save_session(session)

        return ch

    async def ensure_shared_channel_has_member(self, guild: discord.Guild, session: dict, member: discord.Member):
        """
        HO選択などで「参加者が増えた」時に、共有chの閲覧/書込み権限を自動付与
        """
        if not session.get("shared_channel_id"):
            return
        ch = guild.get_channel(int(session["shared_channel_id"]))
        if not isinstance(ch, discord.TextChannel):
            return

        gm = guild.get_member(int(session["gm_id"]))
        if not gm:
            return

        archived = bool(session.get("archived", False))
        participants = set(session.get("participants") or [])
        uid_s = str(member.id)
        if uid_s in participants:
            return

        participants.add(uid_s)
        session["participants"] = sorted(participants)
        self.save_session(session)

        # overwrite追加（個別に付与）
        ow = ch.overwrites
        ow[member] = discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=(not archived),
        )
        await ch.edit(overwrites=ow, reason="add participant to shared")

    # ---------- channels create/update ----------
    async def create_or_update_personal_ch(self, guild: discord.Guild, session: dict, member: discord.Member, ho: str) -> discord.TextChannel:
        gm = guild.get_member(int(session["gm_id"]))
        if not gm:
            raise RuntimeError("GMが見つかりません。")

        archived = bool(session.get("archived", False))

        # ✅ VCと同じカテゴリへ（無いなら従来カテゴリ作成）
        anchor_vc, anchor_cat = self._get_anchor_vc_and_category(guild, session)
        cat = anchor_cat
        if cat is None:
            cat = await self.ensure_category(guild, session, "ho_category_id", f"🧩HO個別：{session.get('name','session')}")

        base = self._base_position_in_anchor_category(session, anchor_vc, cat)
        desired_pos = self._pos_personal(base, session, ho)

        ow = self._make_personal_overwrites(guild, gm, member, session, archived=archived)
        name = safe_channel_name(f"{ho}-{member.display_name}-{jst_date()}")
        topic = f"Session:{session['id']} HO:{ho} Player:{member.id} GM:{gm.id}"

        rec = session.setdefault("ho_personal_channels", {})
        uid = str(member.id)

        if uid in rec:
            ch = guild.get_channel(int(rec[uid]))
            if isinstance(ch, discord.TextChannel):
                await ch.edit(name=name, overwrites=ow, category=cat, topic=topic, position=desired_pos, reason="update personal")
                return ch

        ch = await cat.create_text_channel(name=name, overwrites=ow, topic=topic, position=desired_pos, reason="create personal")
        rec[uid] = ch.id
        self.save_session(session)
        return ch

    async def create_or_update_spectator_ch(self, guild: discord.Guild, session: dict, member: discord.Member) -> discord.TextChannel:
        gm = guild.get_member(int(session["gm_id"]))
        if not gm:
            raise RuntimeError("GMが見つかりません。")

        archived = bool(session.get("archived", False))

        # ✅ VCと同じカテゴリへ（無いなら従来カテゴリ作成）
        anchor_vc, anchor_cat = self._get_anchor_vc_and_category(guild, session)
        cat = anchor_cat
        if cat is None:
            cat = await self.ensure_category(guild, session, "spectator_category_id", f"👀見学：{session.get('name','session')}")

        base = self._base_position_in_anchor_category(session, anchor_vc, cat)
        desired_pos = self._pos_spectator(base, session, member.id)

        ow = self._make_spectator_overwrites(guild, gm, member, archived=archived)
        name = safe_channel_name(f"見学-{member.display_name}")
        topic = f"Session:{session['id']} Spectator:{member.id} GM:{gm.id}"

        rec = session.setdefault("spectator_channels", {})
        uid = str(member.id)

        if uid in rec:
            ch = guild.get_channel(int(rec[uid]))
            if isinstance(ch, discord.TextChannel):
                await ch.edit(name=name, overwrites=ow, category=cat, topic=topic, position=desired_pos, reason="update spectator")
                return ch

        ch = await cat.create_text_channel(name=name, overwrites=ow, topic=topic, position=desired_pos, reason="create spectator")
        rec[uid] = ch.id
        self.save_session(session)
        return ch

    async def apply_spectator_to_all_personals(self, guild: discord.Guild, session: dict, spectator: discord.Member, enable: bool) -> Tuple[int, int]:
        updated = 0
        failed = 0
        personal_map = session.get("ho_personal_channels") or {}
        for cid in personal_map.values():
            ch = guild.get_channel(int(cid))
            if not isinstance(ch, discord.TextChannel):
                continue
            try:
                ow = ch.overwrites
                if enable:
                    ow[spectator] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)
                else:
                    if spectator in ow:
                        del ow[spectator]
                await ch.edit(overwrites=ow, reason="spectator perms sync")
                updated += 1
            except Exception:
                failed += 1
        return updated, failed

    # ---------- restore nickname ----------
    async def restore_all_nicks(self, guild: discord.Guild, session: dict) -> Tuple[int, int, List[str]]:
        restored = 0
        failed = 0
        fail_lines: List[str] = []
        original_nicks: Dict[str, Optional[str]] = session.get("original_nicks") or {}
        for uid_s, orig in original_nicks.items():
            m = guild.get_member(int(uid_s))
            if not m:
                continue
            ok, msg = await try_set_nickname(m, orig, reason=f"session end restore ({session.get('name')})")
            if ok:
                restored += 1
            else:
                failed += 1
                fail_lines.append(f"- {m.mention}: {msg}")
        return restored, failed, fail_lines

    # ---------- archive ----------
    async def archive_session(self, guild: discord.Guild, session: dict) -> Dict[str, int]:
        """
        閲覧のみアーカイブ:
        - 共有/個別/見学chを archive category に移動
        - send_messages を False
        - session.archived=True
        """
        stats = {"moved": 0, "failed": 0}

        gm = guild.get_member(int(session["gm_id"]))
        if not gm:
            raise RuntimeError("GMが見つかりません。")

        archive_cat = await self.ensure_archive_category(guild, session)
        session["archived"] = True
        self.save_session(session)

        # 共有ch
        try:
            scid = session.get("shared_channel_id")
            if scid:
                ch = guild.get_channel(int(scid))
                if isinstance(ch, discord.TextChannel):
                    member_ids = [int(x) for x in (session.get("participants") or [])]
                    ow = self._make_shared_overwrites(guild, gm, member_ids, archived=True)
                    await ch.edit(category=archive_cat, overwrites=ow, reason="archive shared")
                    stats["moved"] += 1
        except Exception:
            stats["failed"] += 1

        # 個別ch
        personal_map = session.get("ho_personal_channels") or {}
        for uid_s, cid in personal_map.items():
            ch = guild.get_channel(int(cid))
            if not isinstance(ch, discord.TextChannel):
                continue
            try:
                player = guild.get_member(int(uid_s))
                if not player:
                    continue
                ow = self._make_personal_overwrites(guild, gm, player, session, archived=True)
                await ch.edit(category=archive_cat, overwrites=ow, reason="archive personal")
                stats["moved"] += 1
            except Exception:
                stats["failed"] += 1

        # 見学ch
        spec_map = session.get("spectator_channels") or {}
        for uid_s, cid in spec_map.items():
            ch = guild.get_channel(int(cid))
            if not isinstance(ch, discord.TextChannel):
                continue
            try:
                sp = guild.get_member(int(uid_s))
                if not sp:
                    continue
                ow = self._make_spectator_overwrites(guild, gm, sp, archived=True)
                await ch.edit(category=archive_cat, overwrites=ow, reason="archive spectator")
                stats["moved"] += 1
            except Exception:
                stats["failed"] += 1

        # 元カテゴリは空なら削除（※VCと同じカテゴリを使っている場合、IDが入ってないので消さない）
        for key in ("shared_category_id", "ho_category_id", "spectator_category_id"):
            try:
                cid = session.get(key)
                if not cid:
                    continue
                cat = guild.get_channel(int(cid))
                if isinstance(cat, discord.CategoryChannel) and len(cat.channels) == 0:
                    await cat.delete(reason="archive cleanup empty category")
            except Exception:
                stats["failed"] += 1

        self.save_session(session)
        return stats

    # ---------- delete ----------
    async def delete_session_everything(self, guild: discord.Guild, session: dict) -> Dict[str, int]:
        stats = {
            "deleted_shared": 0,
            "deleted_personals": 0,
            "deleted_spectators": 0,
            "deleted_categories": 0,
            "deleted_panel": 0,
            "failed": 0,
        }

        # パネル削除
        try:
            ch_id = session.get("panel_channel_id")
            msg_id = session.get("panel_message_id")
            if ch_id and msg_id:
                ch = guild.get_channel(int(ch_id))
                if isinstance(ch, discord.TextChannel):
                    try:
                        msg = await ch.fetch_message(int(msg_id))
                        await msg.delete()
                        stats["deleted_panel"] += 1
                    except discord.NotFound:
                        pass
        except Exception:
            stats["failed"] += 1

        # 共有ch削除
        try:
            scid = session.get("shared_channel_id")
            if scid:
                ch = guild.get_channel(int(scid))
                if isinstance(ch, discord.TextChannel):
                    await ch.delete(reason=f"session delete ({session.get('name')})")
                    stats["deleted_shared"] += 1
        except Exception:
            stats["failed"] += 1

        # 個別ch削除
        for cid in list((session.get("ho_personal_channels") or {}).values()):
            try:
                ch = guild.get_channel(int(cid))
                if isinstance(ch, discord.TextChannel):
                    await ch.delete(reason=f"session delete ({session.get('name')})")
                    stats["deleted_personals"] += 1
            except Exception:
                stats["failed"] += 1

        # 見学ch削除
        for cid in list((session.get("spectator_channels") or {}).values()):
            try:
                ch = guild.get_channel(int(cid))
                if isinstance(ch, discord.TextChannel):
                    await ch.delete(reason=f"session delete ({session.get('name')})")
                    stats["deleted_spectators"] += 1
            except Exception:
                stats["failed"] += 1

        # カテゴリ削除（念のため中身も削除）
        for key in ("shared_category_id", "ho_category_id", "spectator_category_id", "archive_category_id"):
            cid = session.get(key)
            if not cid:
                continue
            try:
                cat = guild.get_channel(int(cid))
                if isinstance(cat, discord.CategoryChannel):
                    for ch in list(cat.channels):
                        try:
                            await ch.delete(reason="session delete cleanup category")
                        except Exception:
                            stats["failed"] += 1
                    await cat.delete(reason="session delete category")
                    stats["deleted_categories"] += 1
            except Exception:
                stats["failed"] += 1

        # DB削除
        try:
            self.delete_session_from_db(session["id"])
        except Exception:
            stats["failed"] += 1

        return stats

    # =========================
    # Slash commands
    # =========================
    @app_commands.command(name="setup", description="セッション準備（/setup 第1話 PC6）：VC参加者全員の共有chも作成")
    @app_commands.describe(name="セッション名", pc="PC数（PC1〜PC12）")
    async def setup_session(self, interaction: discord.Interaction, name: str, pc: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        pc_count = parse_pc_count(pc)
        if not pc_count:
            await interaction.response.send_message("PC指定が不正です。`PC1`〜`PC12` の形式で指定してください。", ephemeral=True)
            return

        # 実行者が入っているVC
        voice = interaction.user.voice
        if not voice or not isinstance(voice.channel, discord.VoiceChannel):
            await interaction.response.send_message("先にVCへ入ってから `/setup` を実行してください。", ephemeral=True)
            return

        db = load_db()
        sessions = db.setdefault("sessions", {})
        sid = self.new_session_id()
        while sid in sessions:
            sid = self.new_session_id()

        session = {
            "id": sid,
            "name": (name or "session").strip()[:50],
            "gm_id": interaction.user.id,
            "pc_count": pc_count,
            "ho_options": make_pc_hos(pc_count),

            # ✅ 追加：VC基準（このVCの“上”にCHを固定順で積む）
            "anchor_vc_id": voice.channel.id,
            "anchor_category_id": (voice.channel.category_id or None),

            "ho_assignments": {},
            "ho_taken": {},
            "ho_personal_channels": {},

            "original_nicks": {},

            "spectators": [],
            "spectator_channels": {},

            "participants": [],  # VC参加者+GM（共有ch権限対象）
            "shared_channel_id": None,

            # ※VCと同カテゴリを使える場合はここは空のまま運用される（作ったときだけIDが入る）
            "shared_category_id": None,
            "ho_category_id": None,
            "spectator_category_id": None,
            "archive_category_id": None,

            "panel_channel_id": None,
            "panel_message_id": None,

            "archived": False,
        }

        sessions[sid] = session
        save_db(db)

        # 共有ch作成 + パネル投稿（共有ch内）
        try:
            shared = await self.create_or_update_shared_channel_from_vc(
                interaction.guild,
                session,
                voice.channel,
                post_panel=True,
            )
        except Exception as e:
            await interaction.response.send_message(f"⚠️ 共有ch作成に失敗: {e}", ephemeral=True)
            # DBは残るので、後から削除/修正可能
            return

        await interaction.response.send_message(
            f"✅ セッション準備完了：**{session['name']}**（ID:`{sid}` / PC1〜PC{pc_count}）\n"
            f"共有ch：{shared.mention}\n"
            f"（HOパネルも共有chに投稿しました）",
            ephemeral=True,
        )

    @app_commands.command(name="sessionend", description="セッション終了（完全削除）：/sessionend 第1話")
    @app_commands.describe(name="セッション名（/setup で作成した名前）")
    async def sessionend(self, interaction: discord.Interaction, name: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        s = self.find_session_by_name(name, interaction.user)
        if not s:
            await interaction.response.send_message("セッションが見つかりません（自分がGMのもののみ）。", ephemeral=True)
            return

        if interaction.user.id != s.get("gm_id") and not is_admin(interaction.user):
            await interaction.response.send_message("GM（または管理者）のみ実行できます。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # nick復元
        restored, failed, fail_lines = await self.restore_all_nicks(interaction.guild, s)

        # 完全削除
        stats = await self.delete_session_everything(interaction.guild, s)

        text = (
            f"🧨 **完全削除 完了**：{name}\n"
            f"ニック復元：{restored}（失敗 {failed}）\n"
            f"削除：共有 {stats['deleted_shared']} / 個別 {stats['deleted_personals']} / 見学 {stats['deleted_spectators']} / "
            f"カテゴリ {stats['deleted_categories']} / パネル {stats['deleted_panel']}\n"
            f"失敗：{stats['failed']}"
        )
        if fail_lines:
            text += "\n\n⚠️ ニック復元失敗（抜粋）:\n" + "\n".join(fail_lines[:10])

        await interaction.followup.send(text, ephemeral=True)


# =========================
# UI: セレクト
# =========================
class HOSelect(discord.ui.Select):
    def __init__(self, cog: HOSelectCog, sid: str):
        self.cog = cog
        self.sid = sid
        s = cog.get_session(sid) or {}

        opts = [discord.SelectOption(label=h, value=h) for h in (s.get("ho_options") or [])]

        super().__init__(
            placeholder="PCを選択（重複不可）",
            options=opts,
            min_values=1,
            max_values=1,
            custom_id=f"ho_select:{sid}",
        )

    async def callback(self, interaction: discord.Interaction):
        s = self.cog.get_session(self.sid)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if s.get("archived"):
            await interaction.response.send_message("このセッションはアーカイブ済みです。", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        ho = self.values[0]
        taken = s.setdefault("ho_taken", {})
        if ho in taken and taken[ho] != str(interaction.user.id):
            await interaction.response.send_message("そのPCは使用済みです。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        uid = str(interaction.user.id)

        # 旧割当の解除
        assignments = s.setdefault("ho_assignments", {})
        old = assignments.get(uid)
        if old and taken.get(old) == uid:
            del taken[old]

        assignments[uid] = ho
        taken[ho] = uid

        # 元nick保存（初回だけ）
        originals = s.setdefault("original_nicks", {})
        if uid not in originals:
            originals[uid] = interaction.user.nick  # Noneなら解除状態

        # nick変更
        desired = build_ho_nick(interaction.user, ho)
        nick_ok, nick_msg = await try_set_nickname(interaction.user, desired, reason="PC selected")

        # 共有ch権限を自動更新（参加者に追加）
        try:
            await self.cog.ensure_shared_channel_has_member(interaction.guild, s, interaction.user)
        except Exception:
            pass

        # 個別ch作成/更新（固定順: 共有→個別→見学）
        try:
            ch = await self.cog.create_or_update_personal_ch(interaction.guild, s, interaction.user, ho)
            self.cog.save_session(s)
            await interaction.followup.send(
                f"✅ {ho} を選択しました。\n{nick_msg if nick_ok else '⚠️ '+nick_msg}\n個別ch：{ch.mention}",
                ephemeral=True,
            )
        except Exception as e:
            self.cog.save_session(s)
            await interaction.followup.send(
                f"✅ {ho} を選択しました。\n{nick_msg if nick_ok else '⚠️ '+nick_msg}\n⚠️ 個別ch作成失敗: {e}",
                ephemeral=True,
            )

        # パネル更新
        try:
            await self.cog.refresh_panel(self.sid, interaction.guild)
        except Exception:
            pass


# =========================
# UI: 誤爆防止Confirm
# =========================
class ConfirmView(discord.ui.View):
    def __init__(self, on_confirm, confirm_label: str, cancel_label: str = "キャンセル"):
        super().__init__(timeout=60)
        self._on_confirm = on_confirm
        self._confirmed = False
        self.confirm_label = confirm_label
        self.cancel_label = cancel_label

    @discord.ui.button(label="CONFIRM", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._confirmed:
            await interaction.response.send_message("すでに処理済みです。", ephemeral=True)
            return
        self._confirmed = True
        await self._on_confirm(interaction)
        self.stop()

    @discord.ui.button(label="CANCEL", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("キャンセルしました。", ephemeral=True)
        self.stop()

    def bind_labels(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.style == discord.ButtonStyle.danger:
                    item.label = self.confirm_label
                else:
                    item.label = self.cancel_label


# =========================
# UI: View（見学/アーカイブ/削除）
# =========================
class HOSelectView(discord.ui.View):
    def __init__(self, cog: HOSelectCog, sid: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.sid = sid
        self.add_item(HOSelect(cog, sid))

    @discord.ui.button(
        label="👀 見学する / 解除",
        style=discord.ButtonStyle.secondary,
        custom_id="btn_spectate_toggle:__SID__",
    )
    async def spectate(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.cog.get_session(self.sid)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if s.get("archived"):
            await interaction.response.send_message("アーカイブ済みです（見学の追加/解除はできません）。", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        spectators = s.setdefault("spectators", [])
        uid = str(interaction.user.id)
        enable = uid not in spectators

        if enable:
            spectators.append(uid)
            try:
                sch = await self.cog.create_or_update_spectator_ch(interaction.guild, s, interaction.user)
                spec_msg = f"✅ 見学開始：{sch.mention}"
            except Exception as e:
                spec_msg = f"⚠️ 見学ch作成失敗: {e}"
        else:
            spectators.remove(uid)
            spec_msg = "✅ 見学解除"

        # 個別chへの閲覧権限反映
        try:
            updated, failed = await self.cog.apply_spectator_to_all_personals(interaction.guild, s, interaction.user, enable)
            perm_msg = f"個別ch権限：更新 {updated} / 失敗 {failed}"
        except Exception as e:
            perm_msg = f"⚠️ 個別ch権限反映失敗: {e}"

        self.cog.save_session(s)

        await interaction.followup.send(f"{spec_msg}\n{perm_msg}", ephemeral=True)

        try:
            await self.cog.refresh_panel(self.sid, interaction.guild)
        except Exception:
            pass

    @discord.ui.button(
        label="🗄️ アーカイブ（閲覧のみ）",
        style=discord.ButtonStyle.primary,
        custom_id="btn_archive:__SID__",
    )
    async def archive(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.cog.get_session(self.sid)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id") and not is_admin(interaction.user):
            await interaction.response.send_message("GM（または管理者）のみ操作できます。", ephemeral=True)
            return

        async def _do(inter: discord.Interaction):
            await inter.response.defer(ephemeral=True, thinking=True)

            restored, failed, fail_lines = await self.cog.restore_all_nicks(inter.guild, s)

            try:
                stats = await self.cog.archive_session(inter.guild, s)
                msg = (
                    f"🗄️ **アーカイブ完了**\n"
                    f"ニック復元：{restored}（失敗 {failed}）\n"
                    f"移動/更新：{stats['moved']} / 失敗：{stats['failed']}\n"
                    f"※ 共有/個別/見学は “閲覧のみ” になりました"
                )
                if fail_lines:
                    msg += "\n\n⚠️ ニック復元失敗（抜粋）:\n" + "\n".join(fail_lines[:10])
                await inter.followup.send(msg, ephemeral=True)
            except Exception as e:
                await inter.followup.send(f"⚠️ アーカイブ失敗: {e}", ephemeral=True)

            try:
                await self.cog.refresh_panel(self.sid, inter.guild)
            except Exception:
                pass

        v = ConfirmView(_do, confirm_label="アーカイブ実行", cancel_label="やめる")
        v.bind_labels()
        await interaction.response.send_message(
            "🗄️ **アーカイブ（閲覧のみ）**にします。\n"
            "- 共有/個別/見学chは残ります（閲覧のみ）\n"
            "- ニックネームは元に戻します\n\n実行しますか？",
            ephemeral=True,
            view=v
        )

    @discord.ui.button(
        label="🧨 完全削除（危険）",
        style=discord.ButtonStyle.danger,
        custom_id="btn_delete:__SID__",
    )
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.cog.get_session(self.sid)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id") and not is_admin(interaction.user):
            await interaction.response.send_message("GM（または管理者）のみ操作できます。", ephemeral=True)
            return

        async def _do(inter: discord.Interaction):
            await inter.response.defer(ephemeral=True, thinking=True)

            restored, failed, fail_lines = await self.cog.restore_all_nicks(inter.guild, s)
            stats = await self.cog.delete_session_everything(inter.guild, s)

            msg = (
                f"🧨 **完全削除 完了**\n"
                f"ニック復元：{restored}（失敗 {failed}）\n"
                f"削除：共有 {stats['deleted_shared']} / 個別 {stats['deleted_personals']} / 見学 {stats['deleted_spectators']} / "
                f"カテゴリ {stats['deleted_categories']} / パネル {stats['deleted_panel']}\n"
                f"失敗：{stats['failed']}\n"
                f"DBからも削除しました。"
            )
            if fail_lines:
                msg += "\n\n⚠️ ニック復元失敗（抜粋）:\n" + "\n".join(fail_lines[:10])

            await inter.followup.send(msg, ephemeral=True)

        v = ConfirmView(_do, confirm_label="本当に完全削除する", cancel_label="やめる")
        v.bind_labels()
        await interaction.response.send_message(
            "🧨 **危険：完全削除**します。\n"
            "- 共有ch / 個別ch / 見学ch / アーカイブカテゴリ含む関連カテゴリ\n"
            "- HOパネルメッセージ\n"
            "- DBのセッション情報\n\n"
            "すべて消えます。**取り消し不可**。\n\n実行しますか？",
            ephemeral=True,
            view=v
        )

    # ✅ custom_id の __SID__ を実セッションIDへ置換（永続View用）
    def _fix_custom_ids(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id and "__SID__" in item.custom_id:
                item.custom_id = item.custom_id.replace("__SID__", self.sid)


async def setup(bot: commands.Bot):
    # 永続View復元のため、Cog側で add_view しているのでここは通常通り
    await bot.add_cog(HOSelectCog(bot))


# =========================
# View生成時に custom_id を確定させるための小フック
# （discord.py は View.__init__ 終了時点で custom_id を持っていればOK）
# =========================
_orig_view_init = HOSelectView.__init__
def _patched_view_init(self, cog: HOSelectCog, sid: str):
    _orig_view_init(self, cog, sid)
    self._fix_custom_ids()
HOSelectView.__init__ = _patched_view_init
