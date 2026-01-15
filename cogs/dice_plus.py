# cogs/dice_plus.py
# ✅ /choice : 候補からランダムに選ぶ
# ✅ /secretroll : ダイスを振って「実行者のDM」に結果を送る（サーバーには出さない）
#
# 依存: なし（dice.py のパーサを内蔵しているので単体で動く）

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands


# ---------- dice parsing (same spirit as dice.py) ----------
MAX_DICE = 100
MAX_SIDES = 100000

@dataclass
class DiceSpec:
    n: int
    sides: int
    keep_mode: Optional[str] = None   # "kh" or "kl"
    keep_n: Optional[int] = None
    tail_expr: str = ""               # "+3-1*2" みたいな末尾演算


_DICE_RE = re.compile(
    r"^\s*"
    r"(?:(\d+)\s*)?"
    r"d\s*(\d+)"
    r"(?:\s*(k[hl])\s*(\d+))?"
    r"\s*(.*)\s*$",
    re.IGNORECASE
)

_ALLOWED_TAIL_RE = re.compile(r"^[0-9+\-*/().\s]*$")


def parse_expr(expr: str) -> DiceSpec:
    expr = expr.strip().lower().replace(" ", "")

    if expr.startswith("d"):
        expr = "1" + expr

    m = _DICE_RE.match(expr)
    if not m:
        raise ValueError("式が読めません。例: 1d100 / 2d6+3 / 4d6kh3")

    n_s, sides_s, keep_mode, keep_n_s, tail = m.groups()

    n = int(n_s) if n_s else 1
    sides = int(sides_s)

    if n < 1 or n > MAX_DICE:
        raise ValueError(f"ダイス個数は 1〜{MAX_DICE} です。")
    if sides < 2 or sides > MAX_SIDES:
        raise ValueError(f"面数は 2〜{MAX_SIDES} です。")

    km = keep_mode.lower() if keep_mode else None
    kn = int(keep_n_s) if keep_n_s else None
    if km:
        if kn is None:
            raise ValueError("kh/kl の後に数字が必要です。例: 4d6kh3")
        if kn < 1 or kn > n:
            raise ValueError("keep数は 1〜ダイス個数 の範囲です。")

    tail = tail or ""
    if tail and not _ALLOWED_TAIL_RE.match(tail):
        raise ValueError("末尾の計算は数字と + - * / ( ) のみ使えます。")

    return DiceSpec(n=n, sides=sides, keep_mode=km, keep_n=kn, tail_expr=tail)


def safe_eval_arith(expr: str) -> int:
    expr = (expr or "").strip()
    if expr == "":
        return 0
    if expr.startswith("+"):
        expr = expr[1:]
    if expr == "":
        return 0
    return int(eval(expr, {"__builtins__": {}}, {}))


def roll(spec: DiceSpec) -> Tuple[List[int], List[int], int, int]:
    all_rolls = [random.randint(1, spec.sides) for _ in range(spec.n)]

    kept = list(all_rolls)
    if spec.keep_mode and spec.keep_n:
        if spec.keep_mode == "kh":
            kept = sorted(all_rolls, reverse=True)[: spec.keep_n]
        else:
            kept = sorted(all_rolls)[: spec.keep_n]

    kept_sum = sum(kept)
    tail_val = safe_eval_arith(spec.tail_expr) if spec.tail_expr else 0
    total = kept_sum + tail_val
    return all_rolls, kept, kept_sum, total


def fmt_list(nums: List[int], *, mark_kept: Optional[List[int]] = None) -> str:
    if not nums:
        return ""
    if not mark_kept:
        return ", ".join(map(str, nums))

    remaining = {}
    for x in mark_kept:
        remaining[x] = remaining.get(x, 0) + 1

    out = []
    for x in nums:
        if remaining.get(x, 0) > 0:
            remaining[x] -= 1
            out.append(f"**{x}**")
        else:
            out.append(str(x))
    return ", ".join(out)


def build_roll_embed(user: discord.User | discord.Member, expr: str, spec: DiceSpec, all_rolls: List[int], kept: List[int], kept_sum: int, total: int) -> discord.Embed:
    head = f"{spec.n}d{spec.sides}"
    if spec.keep_mode and spec.keep_n:
        head += f"{spec.keep_mode}{spec.keep_n}"
    shown_expr = head + (spec.tail_expr or "")

    all_text = fmt_list(all_rolls, mark_kept=kept if (spec.keep_mode and spec.keep_n) else None)

    lines = [f"🎲 `{shown_expr}`", f"出目: {all_text}"]
    if spec.keep_mode and spec.keep_n:
        lines.append(f"採用({spec.keep_mode}): {', '.join(map(str, kept))} → 合計 {kept_sum}")
    else:
        lines.append(f"合計: {kept_sum}")

    if spec.tail_expr:
        tv = safe_eval_arith(spec.tail_expr)
        lines.append(f"補正: `{spec.tail_expr}` (= {tv:+d})")

    e = discord.Embed(
        title=f"🎲 {user.display_name} のシークレットロール",
        description="\n".join(lines),
        color=discord.Color.dark_teal(),
    )
    e.add_field(name="結果", value=f"**{total}**", inline=False)
    return e


# ---------- choice parsing ----------
def parse_choices(text: str) -> List[str]:
    # "a,b,c" / "a | b | c" / "a\nb\nc" どれでもOK
    raw = text.strip()
    if not raw:
        return []
    if "\n" in raw:
        parts = [p.strip() for p in raw.splitlines()]
    elif "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
    else:
        parts = [p.strip() for p in raw.split(",")]
    parts = [p for p in parts if p]
    return parts[:50]  # 念のため上限


class DicePlusCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="choice", description="候補からランダムに1つ選びます（例: A,B,C）")
    @app_commands.describe(options="候補（カンマ区切り / | 区切り / 改行区切り どれでもOK）", secret="自分だけに見える")
    async def choice_cmd(self, interaction: discord.Interaction, options: str, secret: bool = False):
        items = parse_choices(options)
        if len(items) < 2:
            await interaction.response.send_message("候補を2つ以上ください。例: `A,B,C`", ephemeral=True)
            return

        pick = random.choice(items)
        embed = discord.Embed(
            title="🎯 Choice",
            description=f"候補: {', '.join(items)}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="結果", value=f"**{pick}**", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=secret)

    @app_commands.command(name="secretroll", description="ダイスを振って『実行者のDM』に結果を送ります")
    @app_commands.describe(expr="ダイス式（例: 1d100 / 2d6+3 / 4d6kh3）")
    async def secretroll_cmd(self, interaction: discord.Interaction, expr: str):
        # まず式チェック
        try:
            spec = parse_expr(expr)
            all_rolls, kept, kept_sum, total = roll(spec)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        # DMへ送信（失敗する可能性あり）
        embed = build_roll_embed(interaction.user, expr, spec, all_rolls, kept, kept_sum, total)

        try:
            dm = await interaction.user.create_dm()
            await dm.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ DMを送れませんでした（DM拒否/ブロック/サーバー設定など）。\n"
                "ユーザー設定でこのBotからのDMを許可してから再実行してください。",
                ephemeral=True
            )
            return
        except Exception as e:
            await interaction.response.send_message(f"❌ DM送信に失敗: {e}", ephemeral=True)
            return

        # サーバー側には“送った”だけを表示（結果は出さない）
        await interaction.response.send_message("✅ ダイス結果をあなたのDMに送りました。", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DicePlusCog(bot))
