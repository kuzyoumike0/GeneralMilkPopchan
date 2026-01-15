# cogs/vc_setup.py
# !setup で「共有ch作成」「個別ch作成」ボタンを出す
# - 対象VC: !setup 実行者が入っているVC
# - 共有ch: VCメンバー全員 + 見学ロール + 実行者 + 管理者 が閲覧/発言
# - 個別ch: メンバーごとに1つ（同名があれば流用して権限更新）
#
# 必要権限:
# - Bot: Manage Channels / View Channels / Send Messages
#
# ※ 見学ロールIDは必要に応じて変更してください

from __future__ import annotations

import re
import discord
from discord.ext import commands

SPECTATOR_ROLE_ID = 1396919553413353503  # 見学ロール（不要なら None にしてもOK）


def safe_name(name: str, max_len: int = 90) -> str:
    """
    Discordのチャンネル名に安全な形へ（日本語OK）
    """
    s = (name or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\wぁ-んァ-ン一-龥ー\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "member"
    return s[:max_len].lower()


def is_admin(member: discord.Member) -> bool:
    p = member.guild_permissions
    return p.administrator or p.manage_channels


async def ensure_category(guild: discord.Guild, base_name: str) -> discord.CategoryChannel:
    # 既存カテゴリを探す（同名があればそれを使う）
    for c in guild.categories:
        if c.name == base_name:
            return c
    return await guild.create_category(base_name, reason="VC setup auto category")


def build_overwrites_common(
    guild: discord.Guild,
    members: list[discord.Member],
    setup_owner: discord.Member,
) -> dict:
    """
    共有に使う基本Overwrites
    """
    everyone = guild.default_role
    ow = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }

    # 管理者/実行者は見える
    ow[setup_owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    # 見学ロール
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
    # 既存チャンネル同名があれば流用
    for ch in category.text_channels:
        if ch.name == name:
            await ch.edit(overwrites=overwrites, topic=topic, reason="VC setup update perms")
            return ch

    return await category.create_text_channel(
        name=name,
        overwrites=overwrites,
        topic=topic,
        reason="VC setup create channel",
    )


class VCSetupView(discord.ui.View):
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

    @discord.ui.button(label="✅ 共有テキストch作成/更新", style=discord.ButtonStyle.success, custom_id="vc_setup_shared")
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

        cat = await ensure_category(interaction.guild, f"🔊VCテキスト：{vc.name}")
        ow = build_overwrites_common(interaction.guild, members, interaction.user)

        ch_name = safe_name(f"共有-{vc.name}")
        topic = f"VC: {vc.name}（ID:{vc.id}）参加者共有"

        ch = await create_or_update_text_channel(cat, ch_name, ow, topic)
        await interaction.followup.send(f"✅ 共有チャンネルを作成/更新しました：{ch.mention}", ephemeral=True)

    @discord.ui.button(label="🧩 個別テキストch一括作成/更新", style=discord.ButtonStyle.primary, custom_id="vc_setup_individual")
    async def create_individual(self, interaction: discord.Interaction, button: discord.ui.Button):
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

        cat = await ensure_category(interaction.guild, f"🔒個別：{vc.name}")

        # 見学ロール（任意）
        spectator_role = interaction.guild.get_role(SPECTATOR_ROLE_ID) if SPECTATOR_ROLE_ID else None

        created = 0
        updated = 0

        for m in members:
            # 個別チャンネル名：VC表示名そのまま（ただしチャンネル名として安全化）
            ch_name = safe_name(m.display_name)

            everyone = interaction.guild.default_role
            ow = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),

                # 本人
                m: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),

                # !setup 実行者（GM想定）
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }

            # 見学ロール：閲覧/発言OK（不要なら send_messages=False に）
            if spectator_role:
                ow[spectator_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

            topic = f"VC: {vc.name}（ID:{vc.id}）個別 / owner: {m} ({m.id})"

            # 既存なら更新、無ければ作成
            existed = any(ch.name == ch_name for ch in cat.text_channels)
            await create_or_update_text_channel(cat, ch_name, ow, topic)
            if existed:
                updated += 1
            else:
                created += 1

        await interaction.followup.send(
            f"✅ 個別チャンネルを作成/更新しました（作成 {created} / 更新 {updated}）。",
            ephemeral=True
        )


class VCSetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="setup")
    async def setup_cmd(self, ctx: commands.Context):
        """
        !setup を打つと、今入っているVC対象のボタンを出す
        """
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
            title="🎛 VCテキストチャンネル作成パネル",
            description=(
                f"対象VC: **{vc.name}**\n"
                f"VC内の参加者全員向けに、\n"
                f"✅ 共有テキストch / 🧩 個別テキストch を作成・更新します。"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="※ ボタン操作は !setup 実行者 または 管理者のみ")

        view = VCSetupView(self.bot, setup_owner_id=ctx.author.id, vc_id=vc.id)
        await ctx.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(VCSetupCog(bot))
