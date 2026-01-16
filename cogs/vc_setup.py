# cogs/vc_setup.py
# !setup で「VC参加者全員の共有テキストch 作成/更新」ボタンだけを出す
#
# - 対象VC: !setup 実行者が入っているVC
# - 共有ch: VCメンバー全員 +（任意）見学ロール + Bot が閲覧/発言
# - すでに存在する場合も「権限を自動更新」
#
# 必要権限:
# - Bot: Manage Channels / View Channels / Send Messages / Read Message History

from __future__ import annotations

import re
import discord
from discord.ext import commands

SPECTATOR_ROLE_ID = 1396919553413353503  # 見学ロール（不要なら None）


def safe_name(name: str, max_len: int = 90) -> str:
    s = (name or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\wぁ-んァ-ン一-龥ー\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "shared"
    return s[:max_len].lower()


def is_admin(member: discord.Member) -> bool:
    p = member.guild_permissions
    return p.administrator or p.manage_channels


async def ensure_category(guild: discord.Guild, base_name: str) -> discord.CategoryChannel:
    for c in guild.categories:
        if c.name == base_name:
            return c
    return await guild.create_category(base_name, reason="VC shared setup auto category")


def build_overwrites_shared(
    guild: discord.Guild,
    members: list[discord.Member],
) -> dict:
    everyone = guild.default_role
    ow = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }

    # 見学ロール（任意）
    if SPECTATOR_ROLE_ID:
        role = guild.get_role(SPECTATOR_ROLE_ID)
        if role:
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    # VC参加者
    for m in members:
        ow[m] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    return ow


async def create_or_update_text_channel(
    category: discord.CategoryChannel,
    name: str,
    overwrites: dict,
    topic: str,
) -> discord.TextChannel:
    for ch in category.text_channels:
        if ch.name == name:
            await ch.edit(overwrites=overwrites, topic=topic, reason="VC shared setup update perms")
            return ch

    return await category.create_text_channel(
        name=name,
        overwrites=overwrites,
        topic=topic,
        reason="VC shared setup create channel",
    )


class VCSharedSetupView(discord.ui.View):
    def __init__(self, bot: commands.Bot, setup_owner_id: int, vc_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.setup_owner_id = setup_owner_id
        self.vc_id = vc_id

    def _can_use(self, member: discord.Member) -> bool:
        return member.id == self.setup_owner_id or is_admin(member)

    async def _get_target_vc_members(self, guild: discord.Guild) -> tuple[discord.VoiceChannel | None, list[discord.Member]]:
        vc = guild.get_channel(self.vc_id)
        if not isinstance(vc, discord.VoiceChannel):
            return None, []
        members = [m for m in vc.members if not m.bot]
        return vc, members

    @discord.ui.button(label="✅ 共有テキストch作成/更新", style=discord.ButtonStyle.success, custom_id="vc_setup_shared_only")
    async def create_shared(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        if not self._can_use(interaction.user):
            await interaction.response.send_message("この操作は `!setup` 実行者 または 管理者のみ可能です。", ephemeral=True)
            return

        vc, members = await self._get_target_vc_members(interaction.guild)
        if not vc:
            await interaction.response.send_message("対象のVCが見つかりません。", ephemeral=True)
            return
        if not members:
            await interaction.response.send_message("VCに参加者がいません。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        cat = await ensure_category(interaction.guild, f"🔊VC共有：{vc.name}")
        ow = build_overwrites_shared(interaction.guild, members)

        ch_name = safe_name(f"共有-{vc.name}")
        topic = f"VC: {vc.name}（ID:{vc.id}）参加者共有 / members:{len(members)}"

        ch = await create_or_update_text_channel(cat, ch_name, ow, topic)
        await interaction.followup.send(f"✅ 共有チャンネルを作成/更新しました：{ch.mention}", ephemeral=True)


class VCSetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="setup")
    async def setup_cmd(self, ctx: commands.Context):
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return

        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.reply("❌ まずVCに入ってから `!setup` してください。")
            return

        vc = ctx.author.voice.channel
        if not isinstance(vc, discord.VoiceChannel):
            await ctx.reply("❌ 対象は通常のボイスチャンネルのみです。")
            return

        embed = discord.Embed(
            title="🎛 VC共有テキスト 作成パネル",
            description=(
                f"対象VC: **{vc.name}**\n"
                f"VC内の参加者全員が見れる共有テキストchを作成・更新します。\n"
                f"（参加者が増減したら、もう一度押すと権限が更新されます）"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="※ ボタン操作は !setup 実行者 または 管理者のみ")

        view = VCSharedSetupView(self.bot, setup_owner_id=ctx.author.id, vc_id=vc.id)
        await ctx.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(VCSetupCog(bot))
