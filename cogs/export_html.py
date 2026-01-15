# cogs/export_html.py
# Discord風HTMLログ（最終強化）
# ✅ JST
# ✅ 日付区切り
# ✅ ロール色で名前色
# ✅ スタンプ/リアクション
# ✅ スレッド/返信強化（ジャンプリンク/スレッドリンク）
# ✅ edited 表示（最終編集時刻）
# ✅ 添付ファイルの埋め込みカード
# ✅ 連投束ね（同一ユーザーの近接投稿はアバター省略＋インデント）

from __future__ import annotations

import html
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands


# ---------- JST ----------
JST = timezone(timedelta(hours=9))

# ---------- grouping ----------
GROUP_WINDOW_SEC = 7 * 60  # 同一ユーザーの連投を束ねる時間（7分）


# ---------- helpers ----------
def _escape(s: str) -> str:
    return html.escape(s, quote=False)


def _escape_attr(s: str) -> str:
    return html.escape(s, quote=True)


def _nl2br(text: str) -> str:
    return text.replace("\n", "<br>")


_URL_RE = re.compile(r"(https?://[^\s<]+)")


def _linkify(text_html_escaped: str) -> str:
    # text_html_escaped は escape 済み
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


def _format_time_jst(dt: datetime) -> str:
    return dt.astimezone(JST).strftime("%H:%M")


def _format_date_jst(dt: datetime) -> str:
    return dt.astimezone(JST).strftime("%Y/%m/%d")


def _avatar_url(user: discord.abc.User) -> str:
    a = user.display_avatar
    return a.url if a else ""


def _jump_url(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _hex_color_from_member(member: Optional[discord.Member]) -> str:
    if not member:
        return "#dbdee1"
    c = member.color
    if getattr(c, "value", 0) == 0:
        return "#dbdee1"
    return f"#{c.value:06x}"


def _emoji_to_html(r: discord.Reaction) -> str:
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
    return f"""
    <span class="react">
      <span class="reactEmoji">{_escape(str(e))}</span>
      <span class="reactCount">{count}</span>
    </span>
    """


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n/1024/1024:.1f} MB"
    return f"{n/1024/1024/1024:.1f} GB"


def _attachment_card(a: discord.Attachment) -> str:
    url = _escape_attr(a.url)
    fname = _escape(a.filename)
    ctype = a.content_type or "file"
    size = _human_size(getattr(a, "size", 0))
    is_img = ctype.startswith("image/")

    icon = "🖼️" if is_img else "📎"

    preview_html = ""
    if is_img:
        preview_html = (
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
            f'<img class="attPreview" src="{url}" alt="{_escape_attr(a.filename)}"></a>'
        )

    return f"""
    <div class="attCard">
      {preview_html}
      <div class="attBody">
        <div class="attIcon">{icon}</div>
        <div class="attMeta">
          <div class="attName">{fname}</div>
          <div class="attSub">{_escape(ctype)} · {_escape(size)}</div>
          <div class="attActions">
            <a href="{url}" target="_blank" rel="noopener noreferrer">開く</a>
          </div>
        </div>
      </div>
    </div>
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

  --cardBg:#1f2125;
  --cardBorder:#3f4147;
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

.avatarCol{{ width:40px; flex:0 0 40px; }}
.avatar{{
  width:40px; height:40px; border-radius:50%;
  background:#111;
  object-fit:cover;
}}
.avatarSpacer{{ width:40px; height:40px; }} /* 束ね行：アバターの空白 */

.main{{min-width:0; flex:1}}

.metaLine{{display:flex; gap:8px; align-items:baseline; flex-wrap:wrap}}
.name{{font-weight:800; font-size:14px}}
.time{{color:var(--muted); font-size:12px}}
.edited{{color:var(--muted); font-size:12px}}

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

.stickers{{margin-top:8px; display:flex; gap:10px; flex-wrap:wrap; align-items:flex-end}}
.sticker{{display:flex; flex-direction:column; gap:6px; max-width:220px}}
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

.attachments{{margin-top:8px; display:flex; flex-direction:column; gap:10px}}
.attCard{{
  border:1px solid var(--cardBorder);
  background:var(--cardBg);
  border-radius:12px;
  overflow:hidden;
  max-width:560px;
}}
.attPreview{{
  display:block;
  width:100%;
  max-height:360px;
  object-fit:cover;
  background:#111;
}}
.attBody{{
  padding:10px 12px;
  display:flex;
  gap:10px;
  align-items:flex-start;
}}
.attIcon{{
  width:34px; height:34px;
  border-radius:10px;
  background:rgba(255,255,255,.06);
  display:flex; align-items:center; justify-content:center;
  flex:0 0 34px;
  font-weight:900;
}}
.attMeta{{min-width:0}}
.attName{{
  font-weight:800;
  font-size:13px;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  max-width:460px;
}}
.attSub{{margin-top:2px; color:var(--muted); font-size:12px}}
.attActions{{margin-top:6px}}
.attActions a{{color:var(--link); text-decoration:none; font-size:12px}}
.attActions a:hover{{text-decoration:underline}}

.embed{{
  margin-top:8px;
  border-left:4px solid #5865f2;
  background:rgba(0,0,0,.15);
  border-radius:10px;
  padding:10px 12px;
}}
.embedTitle{{font-weight:800; margin-bottom:6px}}
.embedDesc{{color:var(--text); font-size:13px; line-height:1.45}}

.contTime{{
  margin-left:8px;
  color:var(--muted);
  font-size:12px;
  opacity:0.0;
  transition:opacity .12s ease;
}}
.msg.cont:hover .contTime{{opacity:1.0}}
.msg.cont .metaLine{{display:none}} /* 束ね行：ヘッダを隠す */

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
    Exported by Bot / HTML log (Discord-like) / Timezone: JST
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


def _render_reply(msg: discord.Message, guild: discord.Guild) -> str:
    if not (msg.reference and msg.reference.message_id):
        return ""

    ref_mid = msg.reference.message_id
    ref_cid = msg.reference.channel_id or msg.channel.id
    jump = _jump_url(guild.id, ref_cid, ref_mid)

    ref_name = "不明"
    ref_snip = "（取得できない返信先）"
    if isinstance(msg.reference.resolved, discord.Message):
        ref = msg.reference.resolved
        ref_name = getattr(ref.author, "display_name", ref.author.name)
        sn = (ref.clean_content or "").replace("\n", " ")
        ref_snip = sn[:100] + ("…" if len(sn) > 100 else "")

    return f"""
    <div class="reply">
      <div class="hook"></div>
      <div>返信先 <span class="who">{_escape(ref_name)}</span>：{_escape(ref_snip)}
        · <a class="jump" href="{_escape_attr(jump)}" target="_blank" rel="noopener noreferrer">ジャンプ</a>
      </div>
    </div>
    """


def _render_attachments(msg: discord.Message) -> str:
    if not msg.attachments:
        return ""
    att_parts = [_attachment_card(a) for a in msg.attachments]
    return f'<div class="attachments">{"".join(att_parts)}</div>'


def _render_stickers(msg: discord.Message) -> str:
    sticker_parts: List[str] = []
    for st in getattr(msg, "stickers", []) or []:
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
    return f'<div class="stickers">{"".join(sticker_parts)}</div>' if sticker_parts else ""


def _render_reactions(msg: discord.Message) -> str:
    if not msg.reactions:
        return ""
    reacts = [_emoji_to_html(r) for r in msg.reactions]
    return f'<div class="reactions">{"".join(reacts)}</div>'


def _render_thread_info(msg: discord.Message, guild: discord.Guild) -> str:
    try:
        if getattr(msg, "has_thread", False) and getattr(msg, "thread", None):
            th: discord.Thread = msg.thread
            th_url = f"https://discord.com/channels/{guild.id}/{th.id}"
            return f"""
            <div class="threadRow">
              <div class="threadChip">
                🧵 スレッド: <a href="{_escape_attr(th_url)}" target="_blank" rel="noopener noreferrer">{_escape(th.name)}</a>
              </div>
            </div>
            """
    except Exception:
        pass
    return ""


def _render_embed_simple(msg: discord.Message) -> str:
    if not msg.embeds:
        return ""
    e = msg.embeds[0]
    title = _escape(e.title or "")
    desc = _escape(e.description or "")
    if not (title or desc):
        return ""
    return f"""
    <div class="embed">
      {f'<div class="embedTitle">{title}</div>' if title else ''}
      {f'<div class="embedDesc">{_nl2br(desc)}</div>' if desc else ''}
    </div>
    """


def render_message(
    msg: discord.Message,
    guild: discord.Guild,
    member: Optional[discord.Member],
    *,
    is_continuation: bool,
) -> str:
    author = msg.author

    # common pieces
    content = _format_content(msg)
    reply_html = _render_reply(msg, guild)
    attachments_html = _render_attachments(msg)
    stickers_html = _render_stickers(msg)
    reactions_html = _render_reactions(msg)
    thread_html = _render_thread_info(msg, guild)
    embed_html = _render_embed_simple(msg)

    edited_html = ""
    if msg.edited_at:
        edited_html = f'<span class="edited">（編集済み {_escape(_format_time_jst(msg.edited_at))}）</span>'

    if not is_continuation:
        name = _escape(getattr(author, "display_name", author.name))
        avatar = _escape_attr(_avatar_url(author))
        name_color = _escape_attr(_hex_color_from_member(member))
        time_text = _escape(_format_time_jst(msg.created_at))

        return f"""
        <div class="msg head">
          <div class="avatarCol"><img class="avatar" src="{avatar}" alt=""></div>
          <div class="main">
            <div class="metaLine">
              <div class="name" style="color:{name_color}">{name}</div>
              <div class="time">{time_text}</div>
              {edited_html}
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

    # continuation row (no avatar/name line)
    cont_time = _escape(_format_time_jst(msg.created_at))
    return f"""
    <div class="msg cont">
      <div class="avatarCol"><div class="avatarSpacer"></div></div>
      <div class="main">
        <span class="contTime">{cont_time}{(' ' + edited_html) if edited_html else ''}</span>
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


def _should_group(prev: Optional[discord.Message], cur: discord.Message) -> bool:
    if prev is None:
        return False
    if prev.author.id != cur.author.id:
        return False

    # 日付が変わったらグループを切る（JST）
    if _format_date_jst(prev.created_at) != _format_date_jst(cur.created_at):
        return False

    # 時間差
    dt = (cur.created_at - prev.created_at).total_seconds()
    if dt < 0:
        return False
    return dt <= GROUP_WINDOW_SEC


class ExportHtmlCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="export_html", description="Discord風HTMLログを出力します（JST/edited/添付カード/連投束ね）")
    @app_commands.describe(channel="ログを出力するチャンネル（テキスト/スレッド）", limit="取得件数（最大5000）")
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

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message("テキストチャンネル or スレッドを指定してください。", ephemeral=True)
            return

        # 権限チェック
        if isinstance(channel, discord.TextChannel):
            if not channel.permissions_for(interaction.user).read_message_history:
                await interaction.response.send_message("そのチャンネルの履歴を読む権限がありません。", ephemeral=True)
                return
            if guild.me and not channel.permissions_for(guild.me).read_message_history:
                await interaction.response.send_message("Botに履歴を読む権限がありません。", ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True, thinking=True)

        msgs: List[discord.Message] = []
        async for m in channel.history(limit=limit, oldest_first=True):
            msgs.append(m)

        ch_title = f"#{channel.name}" if isinstance(channel, discord.TextChannel) else f"🧵 {channel.name}"
        title = f"{ch_title} のログ"
        meta = f"Guild: {guild.name} / Channel: {ch_title} / Messages: {len(msgs)} / Timezone: JST / GroupWindow: {GROUP_WINDOW_SEC//60}min"
        if isinstance(channel, discord.Thread) and channel.parent:
            meta += f" / Parent: #{channel.parent.name}"

        def get_member(uid: int) -> Optional[discord.Member]:
            return guild.get_member(uid)

        parts: List[str] = []
        last_day: Optional[str] = None
        prev: Optional[discord.Message] = None

        for m in msgs:
            day = _format_date_jst(m.created_at)
            if day != last_day:
                parts.append(render_day_separator(day))
                last_day = day
                prev = None  # 日付区切りでグループ解除

            is_cont = _should_group(prev, m)
            parts.append(render_message(m, guild, get_member(m.author.id), is_continuation=is_cont))
            prev = m

        body = "".join(parts)
        html_text = HTML_TEMPLATE.format(
            title=_escape(title),
            meta=_escape(meta),
            messages=body,
        )

        with tempfile.TemporaryDirectory() as d:
            filename_base = channel.name if hasattr(channel, "name") else "log"
            path = os.path.join(d, "discord_like_log.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html_text)

            await interaction.followup.send(
                content=f"✅ Discord風HTMLログを出力しました（JST/edited/添付カード/連投束ね）。\n{ch_title} / {len(msgs)}件",
                file=discord.File(path, filename=f"{filename_base}_discord_like_JST_grouped.html"),
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ExportHtmlCog(bot))
