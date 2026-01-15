# cogs/export_html.py
# Discord風HTMLログ（強化版）
# ✅ 日付区切り
# ✅ ロール色で名前色
# ✅ スタンプ/リアクション表示
# ✅ スレッド/返信強化（ジャンプリンク/スレッドリンク）
#
# /export_html channel:#ch limit:2000

from __future__ import annotations

import html
import os
import re
import tempfile
from datetime import datetime, date, timezone
from typing import Optional, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands


# ---------- helpers ----------
def _escape(s: str) -> str:
    return html.escape(s, quote=False)


def _escape_attr(s: str) -> str:
    return html.escape(s, quote=True)


def _nl2br(text: str) -> str:
    return text.replace("\n", "<br>")


_URL_RE = re.compile(r"(https?://[^\s<]+)")


def _linkify(text_html_escaped: str) -> str:
    # text_html_escaped は既に escape 済み想定
    def repl(m):
        u = m.group(1)
        esc = _escape_attr(u)
        return f'<a class="mdLink" href="{esc}" target="_blank" rel="noopener noreferrer">{_escape(u)}</a>'
    return _URL_RE.sub(repl, text_html_escaped)


def _format_content(msg: discord.Message) -> str:
    base = msg.clean_content or ""
    base = _escape(base)
    base = _linkify(base)
    base = _nl2br(base)
    return base


def _format_time(dt: datetime) -> str:
    # HTMLは「見た目がDiscordっぽい」優先で 24h 表記
    # JSTに寄せたいなら: dt.astimezone(datetime.now().astimezone().tzinfo)
    return dt.astimezone(datetime.now().astimezone().tzinfo)


def _format_date(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y/%m/%d")


def _avatar_url(user: discord.abc.User) -> str:
    a = user.display_avatar
    return a.url if a else ""


def _jump_url(guild_id: int, channel_id: int, message_id: int) -> str:
    # DiscordメッセージURL
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _hex_color_from_member(member: Optional[discord.Member]) -> str:
    # role color (0 means default)
    if not member:
        return "#dbdee1"
    c = member.color  # discord.Color
    if getattr(c, "value", 0) == 0:
        return "#dbdee1"
    return f"#{c.value:06x}"


def _emoji_to_html(r: discord.Reaction) -> str:
    # 絵文字の表示（カスタムは画像URLが取れることが多いのでimgに）
    e = r.emoji
    count = r.count
    if isinstance(e, discord.PartialEmoji) and e.is_custom_emoji():
        url = e.url
        return f"""
        <span class="react">
          <img class="reactImg" src="{_escape_attr(str(url))}" alt="{_escape_attr(str(e))}">
          <span class="reactCount">{count}</span>
        </span>
        """
    # unicode emoji
    return f"""
    <span class="react">
      <span class="reactEmoji">{_escape(str(e))}</span>
      <span class="reactCount">{count}</span>
    </span>
    """


# ---------- HTML ----------
HTML_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<style>
:root{{
  --bg:#313338;
  --panel:#2b2d31;
  --text:#dbdee1;
  --muted:#949ba4;
  --link:#00a8fc;
  --line:#3f4147;
  --chip:#1e1f22;
  --react:#232428;
  --reactBorder:#3f4147;
}}
*{{box-sizing:border-box}}
body{{
  margin:0; background:var(--bg); color:var(--text);
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans JP", sans-serif;
}}
.header{{
  position:sticky; top:0; z-index:5;
  background:linear-gradient(to bottom, rgba(43,45,49,.98), rgba(43,45,49,.90));
  border-bottom:1px solid var(--line);
  padding:14px 18px;
}}
.header .title{{font-weight:800; font-size:16px}}
.header .meta{{margin-top:4px; color:var(--muted); font-size:12px; line-height:1.4}}
.wrap{{max-width:980px; margin:0 auto; padding:8px 0 70px}}

.daySep{{
  display:flex; align-items:center; gap:12px;
  padding:18px 18px 8px;
}}
.daySep .line{{flex:1; height:1px; background:var(--line)}}
.daySep .label{{
  color:var(--muted); font-size:12px; font-weight:800;
  background:rgba(0,0,0,.10);
  padding:4px 10px; border-radius:999px; border:1px solid var(--line);
}}

.msg{{
  display:flex; gap:12px;
  padding:10px 18px;
}}
.msg:hover{{background:rgba(255,255,255,0.03)}}
.avatar{{
  width:40px; height:40px; border-radius:50%;
  flex:0 0 40px; background:#111;
  object-fit:cover;
}}
.main{{min-width:0; flex:1}}
.metaLine{{display:flex; gap:8px; align-items:baseline; flex-wrap:wrap}}
.name{{font-weight:800; font-size:14px}}
.time{{color:var(--muted); font-size:12px}}

.content{{margin-top:2px; font-size:14px; line-height:1.45; word-wrap:break-word}}
.mdLink{{color:var(--link); text-decoration:none}}
.mdLink:hover{{text-decoration:underline}}

.reply{{
  margin:0 0 6px;
  color:var(--muted); font-size:12px;
  display:flex; gap:8px; align-items:center; flex-wrap:wrap;
}}
.reply .hook{{
  width:16px; height:16px; border-left:2px solid var(--line); border-top:2px solid var(--line);
  border-radius:6px 0 0 0; margin-left:6px;
}}
.reply .who{{font-weight:800}}
.reply .jump{{color:var(--link); text-decoration:none}}
.reply .jump:hover{{text-decoration:underline}}

.threadRow{{
  margin-top:8px;
  display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  color:var(--muted); font-size:12px;
}}
.threadChip{{
  display:inline-flex; align-items:center; gap:8px;
  background:var(--chip);
  border:1px solid var(--line);
  padding:6px 10px; border-radius:999px;
}}
.threadChip a{{color:var(--link); text-decoration:none}}
.threadChip a:hover{{text-decoration:underline}}

.attachments{{margin-top:8px; display:flex; flex-direction:column; gap:8px}}
.file{{
  border:1px solid var(--line);
  background:var(--panel);
  border-radius:10px;
  padding:10px;
  font-size:13px;
}}
.img{{
  max-width:520px; border-radius:12px;
  border:1px solid var(--line);
}}

.stickers{{margin-top:8px; display:flex; gap:10px; flex-wrap:wrap; align-items:flex-end}}
.sticker{{
  display:flex; flex-direction:column; gap:6px;
  max-width:200px;
}}
.sticker img{{
  width:160px; height:auto; border-radius:12px;
  border:1px solid var(--line); background:rgba(0,0,0,.15);
}}
.sticker .cap{{color:var(--muted); font-size:12px}}

.reactions{{margin-top:8px; display:flex; gap:6px; flex-wrap:wrap}}
.react{{
  display:inline-flex; gap:6px; align-items:center;
  border:1px solid var(--reactBorder);
  background:var(--react);
  padding:4px 8px;
  border-radius:999px;
  font-size:12px;
}}
.reactImg{{width:16px; height:16px}}
.reactEmoji{{font-size:14px; line-height:1}}
.reactCount{{color:var(--text); font-weight:800}}

.embed{{
  margin-top:8px;
  border-left:4px solid #5865f2;
  background:rgba(0,0,0,.15);
  border-radius:10px;
  padding:10px 12px;
}}
.embedTitle{{font-weight:800; margin-bottom:6px}}
.embedDesc{{color:var(--text); font-size:13px; line-height:1.45}}

.footer{{
  position:fixed; left:0; right:0; bottom:0;
  background:rgba(49,51,56,.95);
  border-top:1px solid var(--line);
  padding:10px 18px;
  color:var(--muted); font-size:12px;
}}
</style>
</head>
<body>
  <div class="header">
    <div class="title">{title}</div>
    <div class="meta">{meta}</div>
  </div>
  <div class="wrap">
    {messages}
  </div>
  <div class="footer">
    Exported by Bot / HTML log (Discord-like)
  </div>
</body>
</html>
"""


def render_day_separator(label: str) -> str:
    return f"""
    <div class="daySep">
      <div class="line"></div>
      <div class="label">{_escape(label)}</div>
      <div class="line"></div>
    </div>
    """


def render_message(
    msg: discord.Message,
    guild: discord.Guild,
    member: Optional[discord.Member],
) -> str:
    author = msg.author
    name = _escape(getattr(author, "display_name", author.name))
    time_text = _escape(_format_time(msg.created_at))
    avatar = _escape_attr(_avatar_url(author))
    name_color = _escape_attr(_hex_color_from_member(member))

    # ---- reply (stronger) ----
    reply_html = ""
    if msg.reference and msg.reference.message_id:
        ref_mid = msg.reference.message_id
        ref_cid = msg.reference.channel_id or msg.channel.id
        jump = _jump_url(guild.id, ref_cid, ref_mid)

        ref_name = "不明"
        ref_snip = ""
        if isinstance(msg.reference.resolved, discord.Message):
            ref = msg.reference.resolved
            ref_name = getattr(ref.author, "display_name", ref.author.name)
            sn = (ref.clean_content or "").replace("\n", " ")
            ref_snip = sn[:80] + ("…" if len(sn) > 80 else "")
        else:
            ref_snip = "（取得できない返信先）"

        reply_html = f"""
        <div class="reply">
          <div class="hook"></div>
          <div>返信先 <span class="who">{_escape(ref_name)}</span>：{_escape(ref_snip)}
            · <a class="jump" href="{_escape_attr(jump)}" target="_blank" rel="noopener noreferrer">ジャンプ</a>
          </div>
        </div>
        """

    # ---- content ----
    content = _format_content(msg)
    if not content:
        content = ""

    # ---- attachments ----
    att_parts: List[str] = []
    for a in msg.attachments:
        url = _escape_attr(a.url)
        fname = _escape(a.filename)
        if a.content_type and a.content_type.startswith("image/"):
            att_parts.append(
                f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
                f'<img class="img" src="{url}" alt="{_escape_attr(a.filename)}"></a>'
            )
        else:
            att_parts.append(
                f'<div class="file">📎 <a class="mdLink" href="{url}" target="_blank" rel="noopener noreferrer">{fname}</a></div>'
            )
    attachments_html = f'<div class="attachments">{"".join(att_parts)}</div>' if att_parts else ""

    # ---- stickers ----
    sticker_parts: List[str] = []
    for st in getattr(msg, "stickers", []) or []:
        # st: discord.StickerItem (多くの場合 url を持つ)
        st_name = _escape(getattr(st, "name", "sticker"))
        st_url = getattr(st, "url", None)
        if st_url:
            sticker_parts.append(
                f'<div class="sticker"><a href="{_escape_attr(str(st_url))}" target="_blank" rel="noopener noreferrer">'
                f'<img src="{_escape_attr(str(st_url))}" alt="{_escape_attr(st_name)}"></a>'
                f'<div class="cap">🧷 {st_name}</div></div>'
            )
        else:
            sticker_parts.append(f'<div class="sticker"><div class="cap">🧷 {st_name}</div></div>')
    stickers_html = f'<div class="stickers">{"".join(sticker_parts)}</div>' if sticker_parts else ""

    # ---- reactions ----
    reacts = []
    for r in msg.reactions:
        # count は取得できる（ただし誰が押したかは不要）
        reacts.append(_emoji_to_html(r))
    reactions_html = f'<div class="reactions">{"".join(reacts)}</div>' if reacts else ""

    # ---- thread info (stronger) ----
    thread_html = ""
    try:
        # メッセージからスレッドが作られている場合
        if getattr(msg, "has_thread", False) and getattr(msg, "thread", None):
            th: discord.Thread = msg.thread
            th_url = _jump_url(guild.id, th.id, th.id)  # ざっくり（Discordはスレッドも channel として扱える）
            thread_html = f"""
            <div class="threadRow">
              <div class="threadChip">
                🧵 スレッド: <a href="{_escape_attr(th_url)}" target="_blank" rel="noopener noreferrer">{_escape(th.name)}</a>
              </div>
            </div>
            """
    except Exception:
        pass

    # チャンネル自体がスレッドの場合、ヘッダをメタとして表示したいケースがあるので、
    # ここは export 側で header meta に表示する（後述）。

    # ---- embed (simple) ----
    embed_html = ""
    if msg.embeds:
        e = msg.embeds[0]
        title = _escape(e.title or "")
        desc = _escape(e.description or "")
        if title or desc:
            embed_html = f"""
            <div class="embed">
              {f'<div class="embedTitle">{title}</div>' if title else ''}
              {f'<div class="embedDesc">{_nl2br(desc)}</div>' if desc else ''}
            </div>
            """

    return f"""
    <div class="msg">
      <img class="avatar" src="{avatar}" alt="">
      <div class="main">
        <div class="metaLine">
          <div class="name" style="color:{name_color}">{name}</div>
          <div class="time">{time_text}</div>
        </div>
        {reply_html}
        <div class="content">{content}</div>
        {attachments_html}
        {stickers_html}
        {reactions_html}
        {thread_html}
        {embed_html}
      </div>
    </div>
    """


# ---------- Cog ----------
class ExportHtmlCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="export_html", description="Discord風HTMLログを出力します（指定チャンネル）")
    @app_commands.describe(channel="ログを出力するチャンネル", limit="取得件数（最大5000）")
    async def export_html(
        self,
        interaction: discord.Interaction,
        channel: discord.abc.MessageableChannel,
        limit: app_commands.Range[int, 1, 5000] = 2000,
    ):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        guild = interaction.guild

        # TextChannel / Thread のみに絞る（ログの見た目が安定する）
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message("テキストチャンネル or スレッドを指定してください。", ephemeral=True)
            return

        # 権限チェック（閲覧履歴権限）
        me = guild.me
        if isinstance(channel, discord.TextChannel):
            if not channel.permissions_for(interaction.user).read_message_history:
                await interaction.response.send_message("そのチャンネルの履歴を読む権限がありません。", ephemeral=True)
                return
            if me and not channel.permissions_for(me).read_message_history:
                await interaction.response.send_message("Botに履歴を読む権限がありません。", ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True, thinking=True)

        msgs: List[discord.Message] = []
        async for m in channel.history(limit=limit, oldest_first=True):
            msgs.append(m)

        # メタ情報
        ch_title = f"#{channel.name}" if isinstance(channel, discord.TextChannel) else f"🧵 {channel.name}"
        title = f"{ch_title} のログ"
        meta = f"Guild: {guild.name} / Channel: {ch_title} / Messages: {len(msgs)}"
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if parent:
                meta += f" / Parent: #{parent.name}"

        # メンバーキャッシュ（ロール色のため）
        # guild.get_member はキャッシュ依存だが、通常十分。足りない場合でも白にフォールバックする。
        def get_member(user_id: int) -> Optional[discord.Member]:
            return guild.get_member(user_id)

        # 日付区切りを挿入しながらレンダ
        parts: List[str] = []
        last_day: Optional[str] = None

        for m in msgs:
            day = _format_date(m.created_at)
            if day != last_day:
                parts.append(render_day_separator(day))
                last_day = day
            member = get_member(m.author.id)
            parts.append(render_message(m, guild, member))

        body = "".join(parts)
        html_text = HTML_TEMPLATE.format(
            title=_escape(title),
            meta=_escape(meta),
            messages=body,
        )

        # 一時ファイルに保存して返す
        with tempfile.TemporaryDirectory() as d:
            filename_base = channel.name if hasattr(channel, "name") else "log"
            path = os.path.join(d, "discord_like_log.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html_text)

            await interaction.followup.send(
                content=f"✅ Discord風HTMLログを出力しました。\n{ch_title} / {len(msgs)}件",
                file=discord.File(path, filename=f"{filename_base}_discord_like.html"),
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ExportHtmlCog(bot))
