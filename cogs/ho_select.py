# cogs/ho_select.py
# ✅ HO選択UI（ドロップダウン）
# ✅ HOを選んだ瞬間に「個別テキストch」を自動作成/更新
#    - チャンネル名: 「HO名 + ユーザ名 + 日本日付(JST)」
#    - GM も必ず閲覧/発言できる
# ✅ HOは重複不可 / 変更も可（変更時も個別chを更新）
#
# コマンド:
# /ho_setup session_id:<id> hos:"HO1,HO2,HO3"
# /ho_panel session_id:<id>
# /ho_lock session_id:<id> locked:true/false
# /ho_status session_id:<id> (GMのみ：割当一覧)

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

DATA_DIR = "data"
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")

JST = timezone(timedelta(hours=9))


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
    parts = [p.strip() for p in text.split(",")]
    parts = [p for p in parts if p]
    return parts[:25]  # Selectは最大25


def safe_channel_name(name: str, max_len: int = 90) -> str:
    """
    Discordチャンネル名として安全な形に。
    日本語はOK。空白→ハイフン、記号を軽く除去。
    """
    s = (name or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\wぁ-んァ-ン一-龥ー\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "member"
    return s[:max_len].lower()


def jst_date_string() -> str:
    # 日本日付（タイトル用）: 2026-01-16 みたいにしておく（チャンネル名に安全）
    return datetime.now(JST).strftime("%Y-%m-%d")


class HOSelectCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_data_dir()

        # 永続View復元（ho_panel_message_id があるセッションだけ）
        db = load_db()
        for sid, s in (db.get("sessions") or {}).items():
            if s.get("ho_panel_message_id"):
                self.bot.add_view(HOSelectView(self, sid))

    # ----- DB helpers -----
    def get_session(self, session_id: str) -> Optional[dict]:
        db = load_db()
        return (db.get("sessions") or {}).get(session_id)

    def save_session(self, session: dict):
        db = load_db()
        db.setdefault("sessions", {})[session["id"]] = session
        save_db(db)

    # ----- HO logic -----
    def assign_ho(self, session_id: str, user_id: int, ho: str) -> Tuple[bool, str]:
        s = self.get_session(session_id)
        if not s:
            return False, "セッションが見つかりません。"
        hos = s.get("ho_options") or []
        if ho not in hos:
            return False, "そのHOは候補にありません。"

        assignments: Dict[str, str] = s.setdefault("ho_assignments", {})  # user_id(str)->ho
        taken: Dict[str, str] = s.setdefault("ho_taken", {})              # ho->user_id(str)

        uid_s = str(user_id)

        # 既に同じHO
        if assignments.get(uid_s) == ho:
            return False, f"すでに **{ho}** を選択しています。"

        # HOが他人に取られている
        owner = taken.get(ho)
        if owner and owner != uid_s:
            return False, f"そのHO（**{ho}**）は既に別の参加者が選択しています。"

        # 旧HO開放
        old = assignments.get(uid_s)
        if old and taken.get(old) == uid_s:
            del taken[old]

        assignments[uid_s] = ho
        taken[ho] = uid_s

        self.save_session(s)
        return True, f"HOを **{ho}** に設定しました。"

    # ----- Channel creation -----
    async def ensure_ho_category(self, guild: discord.Guild, session: dict) -> discord.CategoryChannel:
        # セッションカテゴリがあるならその中に置く（なければHO用カテゴリを作る）
        cat = None
        if session.get("category_id"):
            ch = guild.get_channel(int(session["category_id"]))
            if isinstance(ch, discord.CategoryChannel):
                cat = ch

        # それでも無ければHO専用カテゴリを作って保持
        if not cat:
            ho_cat_id = session.get("ho_category_id")
            if ho_cat_id:
                ch = guild.get_channel(int(ho_cat_id))
                if isinstance(ch, discord.CategoryChannel):
                    cat = ch

        if not cat:
            name = f"🧩HO個別：{session.get('name','session')}"
            cat = await guild.create_category(name=name, reason="HO personal channels")
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
        gm = guild.get_member(int(session["gm_id"]))
        if not gm:
            raise RuntimeError("GMがサーバーに見つかりません。")

        cat = await self.ensure_ho_category(guild, session)

        # チャンネル名（タイトル）
        # 「HO名＋ユーザ名＋日本日付」
        date_s = jst_date_string()
        raw_name = f"{ho}-{member.display_name}-{date_s}"
        ch_name = safe_channel_name(raw_name)

        # 権限：本人 + GM + bot のみ
        everyone = guild.default_role
        overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            gm: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        # 既存ch再利用ロジック：
        # 1) 以前作ったch ID を覚えていればそれを更新（名前は変えてもOK）
        ho_channels = session.setdefault("ho_personal_channels", {})  # user_id(str)->channel_id
        uid_s = str(member.id)

        existing: Optional[discord.TextChannel] = None
        if ho_channels.get(uid_s):
            ch = guild.get_channel(int(ho_channels[uid_s]))
            if isinstance(ch, discord.TextChannel):
                existing = ch

        # 2) 同名がカテゴリ内にあればそれを使う（衝突時）
        if not existing:
            for ch in cat.text_channels:
                if ch.name == ch_name:
                    existing = ch
                    break

        topic = f"Session {session['id']} / HO:{ho} / Player:{member}({member.id}) / GM:{gm}({gm.id})"

        if existing:
            await existing.edit(
                name=ch_name,
                overwrites=overwrites,
                topic=topic,
                reason="HO personal channel update",
            )
            ho_channels[uid_s] = existing.id
            self.save_session(session)
            return existing

        # 無ければ作成
        new_ch = await cat.create_text_channel(
            name=ch_name,
            overwrites=overwrites,
            topic=topic,
            reason="HO personal channel create",
        )
        ho_channels[uid_s] = new_ch.id
        self.save_session(session)

        # 初回案内
        await new_ch.send(
            f"🧩 **HO個別チャンネル** を作成しました。\n"
            f"- HO：**{ho}**\n"
            f"- 参加者：{member.mention}\n"
            f"- GM：{gm.mention}\n"
            f"- 日付：{date_s}（JST）"
        )
        return new_ch

    # ----- Rendering -----
    def build_ho_embed(self, session: dict) -> discord.Embed:
        e = discord.Embed(
            title=f"🧩 HO選択：{session.get('name','session')}",
            description=f"セッションID: `{session['id']}`\nGM: <@{session['gm_id']}>",
            color=discord.Color.blurple(),
        )
        e.add_field(name="状態", value=("🔒 ロック中" if session.get("ho_locked") else "🔓 選択可能"), inline=True)

        hos = session.get("ho_options") or []
        taken = session.get("ho_taken") or {}

        lines = []
        for ho in hos:
            if taken.get(ho):
                lines.append(f"✅ {ho}（埋まり）")
            else:
                lines.append(f"⬜ {ho}（空き）")
        e.add_field(name="HO一覧", value="\n".join(lines) if lines else "未設定", inline=False)

        e.set_footer(text="HOを選ぶと、あなた専用の個別チャンネルが自動作成されます（GMも入ります）")
        return e

    async def refresh_ho_panel(self, session_id: str, guild: Optional[discord.Guild]):
        s = self.get_session(session_id)
        if not s:
            return
        ch_id = s.get("ho_panel_channel_id")
        msg_id = s.get("ho_panel_message_id")
        if not ch_id or not msg_id or not guild:
            return

        ch = guild.get_channel(int(ch_id))
        if not isinstance(ch, discord.TextChannel):
            return

        try:
            msg = await ch.fetch_message(int(msg_id))
        except Exception:
            return

        view = HOSelectView(self, session_id)
        await msg.edit(embed=self.build_ho_embed(s), view=view)

    # ----- Commands -----
    @app_commands.command(name="ho_setup", description="HO候補を登録します（GM用）")
    @app_commands.describe(session_id="セッションID", hos="HO候補（カンマ区切り）例: HO1,HO2,HO3")
    async def ho_setup(self, interaction: discord.Interaction, session_id: str, hos: str):
        s = self.get_session(session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id"):
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return

        ho_list = parse_hos(hos)
        if not ho_list:
            await interaction.response.send_message("HO候補が空です。", ephemeral=True)
            return

        # 候補を保存（既存割当はリセット）
        s["ho_options"] = ho_list
        s["ho_assignments"] = {}
        s["ho_taken"] = {}
        s.setdefault("ho_locked", False)

        # 個別ch対応の記録領域
        s.setdefault("ho_personal_channels", {})
        # s.setdefault("ho_category_id", None)  # 必要になったら作る

        self.save_session(s)
        await interaction.response.send_message(
            "✅ HO候補を登録しました（最大25）。\n" + "\n".join(f"- {x}" for x in ho_list),
            ephemeral=True
        )
        try:
            await self.refresh_ho_panel(session_id, interaction.guild)
        except Exception:
            pass

    @app_commands.command(name="ho_panel", description="HO選択パネルを投稿します（GM用）")
    @app_commands.describe(session_id="セッションID")
    async def ho_panel(self, interaction: discord.Interaction, session_id: str):
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
        if not (s.get("ho_options") or []):
            await interaction.response.send_message("先に /ho_setup でHO候補を登録してください。", ephemeral=True)
            return

        view = HOSelectView(self, session_id)
        self.bot.add_view(view)

        await interaction.response.send_message(embed=self.build_ho_embed(s), view=view)

        msg = await interaction.original_response()
        s["ho_panel_channel_id"] = interaction.channel_id
        s["ho_panel_message_id"] = msg.id
        self.save_session(s)

    @app_commands.command(name="ho_lock", description="HO選択をロック/解除します（GM用）")
    @app_commands.describe(session_id="セッションID", locked="trueでロック / falseで解除")
    async def ho_lock(self, interaction: discord.Interaction, session_id: str, locked: bool):
        s = self.get_session(session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id"):
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return

        s["ho_locked"] = bool(locked)
        self.save_session(s)

        await interaction.response.send_message(
            f"HO選択を **{'ロック' if locked else '解除'}** しました。",
            ephemeral=True
        )
        try:
            await self.refresh_ho_panel(session_id, interaction.guild)
        except Exception:
            pass

    @app_commands.command(name="ho_status", description="HO割当一覧を表示します（GM用）")
    @app_commands.describe(session_id="セッションID")
    async def ho_status(self, interaction: discord.Interaction, session_id: str):
        s = self.get_session(session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id"):
            await interaction.response.send_message("GMのみ実行できます。", ephemeral=True)
            return

        hos = s.get("ho_options") or []
        taken = s.get("ho_taken") or {}
        lines = []
        for ho in hos:
            uid = taken.get(ho)
            lines.append(f"- **{ho}** → {f'<@{uid}>' if uid else '（未選択）'}")

        await interaction.response.send_message("📋 HO割当一覧\n" + ("\n".join(lines) if lines else "（未設定）"), ephemeral=True)


class HOSelect(discord.ui.Select):
    def __init__(self, cog: HOSelectCog, session_id: str):
        self.cog = cog
        self.session_id = session_id

        s = self.cog.get_session(session_id)
        hos = (s.get("ho_options") or []) if s else []
        options = [discord.SelectOption(label=ho, value=ho) for ho in hos[:25]]

        super().__init__(
            placeholder="HOを選択…（1人1HO / 重複不可）",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"ho_select:{session_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        s = self.cog.get_session(self.session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        if s.get("ho_locked"):
            await interaction.response.send_message("HO選択はロックされています（GMに連絡してください）。", ephemeral=True)
            return

        uid = interaction.user.id
        players = s.get("players", [])
        if uid != s.get("gm_id") and uid not in players:
            await interaction.response.send_message("このセッションの参加者ではありません。", ephemeral=True)
            return

        chosen = self.values[0]

        ok, msg = self.cog.assign_ho(self.session_id, uid, chosen)
        # まずは応答（※この後にチャンネル作成で時間がかかる可能性があるので defer）
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            return

        # ✅ 個別チャンネル作成/更新
        try:
            ch = await self.cog.create_or_update_personal_channel(interaction.guild, s, interaction.user, chosen)
            await interaction.followup.send(f"{msg}\n✅ 個別チャンネル：{ch.mention}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"{msg}\n⚠️ 個別チャンネル作成に失敗: {e}", ephemeral=True)

        # パネル更新（空き/埋まりの反映）
        try:
            await self.cog.refresh_ho_panel(self.session_id, interaction.guild)
        except Exception:
            pass


class HOSelectView(discord.ui.View):
    def __init__(self, cog: HOSelectCog, session_id: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        self.add_item(HOSelect(cog, session_id))

    @discord.ui.button(label="割当状況を見る", style=discord.ButtonStyle.secondary, custom_id="ho_status_public")
    async def show_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.cog.get_session(self.session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        my = (s.get("ho_assignments") or {}).get(str(interaction.user.id))
        if my:
            await interaction.response.send_message(f"あなたのHO：**{my}**", ephemeral=True)
        else:
            await interaction.response.send_message("あなたはまだHOを選んでいません。", ephemeral=True)

    @discord.ui.button(label="HOロック/解除(GM)", style=discord.ButtonStyle.danger, custom_id="ho_lock_toggle")
    async def toggle_lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.cog.get_session(self.session_id)
        if not s:
            await interaction.response.send_message("セッションが見つかりません。", ephemeral=True)
            return
        if interaction.user.id != s.get("gm_id"):
            await interaction.response.send_message("GMのみ操作できます。", ephemeral=True)
            return

        s["ho_locked"] = not s.get("ho_locked", False)
        self.cog.save_session(s)

        await interaction.response.send_message(
            f"HO選択を **{'ロック' if s['ho_locked'] else '解除'}** しました。",
            ephemeral=True
        )
        try:
            await self.cog.refresh_ho_panel(self.session_id, interaction.guild)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(HOSelectCog(bot))
