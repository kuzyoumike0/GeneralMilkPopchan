# cogs/export_html.py
# Discord風HTMLログを書き出す Cog（discord.py 2.x）
# /export_html channel:#ch limit:2000
# - メッセージを時系列でHTML化
# - アバター/表示名/時刻/本文/添付(画像はインライン)/返信(簡易)
# - 色はDiscordっぽいダークテーマ

from __future__ import annotations

import html
import os
import re
import tempfile
from datetime import timezone
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands


def _escape(s: str) -> str:
    return html.escape(s, quote=False)


def _linkify(text: str) -> str:
    # URLをリンク化（簡易）
    url_re = re.compile(r"(https?://[^\s<]+)")
    def repl(m):
        u = m.group(1)
        esc = html.escape(u, quote=True)
        return f'<a class="mdLink" href="{esc}" target="_blank" rel="noopener noreferrer">{esc}</a>'
    return url_re.sub(repl, text)


def _nl2br(text: str) -> str:
    return text.replace("\n", "<br>")


def _format_content(msg: discord.Message) -> str:
    # mention などは .clean_content でテキスト化してから装飾
    base = msg.clean_content or ""
    base = _escape(base)
    base = _linkify(base)
    base = _nl2br(base)
    return base


def _ts(dt: discord.utils.snowflake_time) -> str:
    # JSTで表示したい場合は dt.astimezone(...) を使う
    # ここは Discord風にローカル相当の表示（UTC）→ 端末側で気になればJST変換に変更OK
    # dt は aware
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _avatar_url(user: discord.abc.User) -> str:
    a = user.display_avatar
    return a.url if a else ""


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
  --bubble:#2b2d31;
  --code:#1e1f22;
  --quote:#1f2125;
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
.wrap{{max-width:980px; margin:0 auto; padding:10px 0 60px}}
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
  display:flex; gap:8px; align-items:center;
}}
.reply .bar{{width:22px; height:2px; background:var(--line); border-radius:2px}}
.reply .who{{font-weight:700; color:var(--muted)}}

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
hr.sep{{border:none; border-top:1px solid var(--line); margin:0}}
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


def render_message(msg: discord.Message, guild: Optional[discord.Guild]) -> str:
    author = msg.author
    name = _escape(getattr(author, "display_name", author.name))
    time_text = _escape(_ts(msg.created_at))
    avatar = html.escape(_avatar_url(author), quote=True)

    # 返信（簡易）
    reply_html = ""
    if msg.reference and isinstance(msg.reference.resolved, discord.Message):
        ref = msg.reference.resolved
        ref_name = _escape(getattr(ref.author, "display_name", ref.author.name))
        snippet = (ref.clean_content or "").replace("\n", " ")
        snippet = _escape(snippet[:60] + ("…" if len(snippet) > 60 else ""))
        reply_html = f"""
        <div class="reply">
          <div class="bar"></div>
          <div>返信先 <span class="who">{ref_name}</span>：{snippet}</div>
        </div>
        """

    content = _format_content(msg)
    if not content:
        content = ""  # 空でも添付や埋め込みがある

    # 添付（画像は表示、他はリンク）
    att_parts: List[str] = []
    for a in msg.attachments:
        url = html.escape(a.url, quote=True)
        fname = _escape(a.filename)
        if a.content_type and a.content_type.startswith("image/"):
            att_parts.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer"><img class="img" src="{url}" alt="{fname}"></a>')
        else:
            att_parts.append(f'<div class="file">📎 <a class="mdLink" href="{url}" target="_blank" rel="noopener noreferrer">{fname}</a></div>')

    attachments_html = ""
    if att_parts:
        attachments_html = f'<div class="attachments">{"".join(att_parts)}</div>'

    # Embed（簡易）
    embed_html = ""
    if msg.embeds:
        # 最初の1つだけ軽く表示（必要ならループで全部出せる）
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
          <div class="name">{name}</div>
          <div class="time">{time_text}</div>
        </div>
        {reply_html}
        <div class="content">{content}</div>
        {attachments_html}
        {embed_html}
      </div>
    </div>
    """


class ExportHtmlCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="export_html", description="Discord風HTMLログを出力します（指定チャンネル）")
    @app_commands.describe(channel="ログを出力するチャンネル", limit="取得件数（最大5000推奨）")
    async def export_html(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        limit: app_commands.Range[int, 1, 5000] = 2000,
    ):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        # 権限チェック（閲覧できる人だけ実行できるように）
        if not channel.permissions_for(interaction.user).read_message_history:
            await interaction.response.send_message("そのチャンネルの履歴を読む権限がありません。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        msgs: List[discord.Message] = []
        async for m in channel.history(limit=limit, oldest_first=True):
            msgs.append(m)

        title = f"#{channel.name} のログ"
        meta = f"Guild: {interaction.guild.name} / Channel: #{channel.name} / Messages: {len(msgs)}"

        body = "".join(render_message(m, interaction.guild) for m in msgs)
        html_text = HTML_TEMPLATE.format(
            title=_escape(title),
            meta=_escape(meta),
            messages=body
        )

        # 一時ファイルに保存
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "discord_like_log.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html_text)

            await interaction.followup.send(
                content=f"✅ HTMLログを出力しました（Discord風）。\n`{channel.name}` / {len(msgs)}件",
                file=discord.File(path, filename=f"{channel.name}_log.html"),
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ExportHtmlCog(bot))
