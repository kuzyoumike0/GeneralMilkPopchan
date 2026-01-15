# cogs/dice.py
# ✅ /roll でダイスを振る（NdM+K, NdM*X, 1d20, d100 など）
# ✅ kh / kl（上位/下位 keep）対応：3d6kh2, 4d6kl3
# ✅ 内訳表示
# ✅ 1回のロールで最大100個、面は最大100000（安全対策）

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands


MAX_DICE = 100
MAX_SIDES = 100000


@dataclass
class DiceSpec:
    n: int
    sides: int
    keep_mode: Optional[str] = None   # "kh" or "kl"
    keep_n: Optional[int] = None
    tail_expr: str = ""               # "+3-1*2" みたいな部分（演算だけ許可）


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
    expr = expr.strip().lower()
    expr = expr.replace(" ", "")

    # "d20" -> "1d20"
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
    """
    数字と四則演算と()だけの式を eval する（builtins無し）
    """
    expr = expr.strip()
    if expr == "":
        return 0
    # 先頭が + なら許容（+3みたいな）
    if expr.startswith("+"):
        expr = expr[1:]
    if expr == "":
        return 0
    return int(eval(expr, {"__builtins__": {}}, {}))


def roll(spec: DiceSpec) -> Tuple[List[int], List[int], int, int]:
    """
    returns:
      all_rolls, kept_rolls, kept_sum, total
    """
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
    # keptの強調（同値があるので multiset的に数える）
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


class DiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="roll", description="ダイスを振ります（例: 1d100, 2d6+3, 4d6kh3）")
    @app_commands.describe(expr="ダイス式（例: 1d100 / 2d6+3 / 4d6kh3）", secret="自分だけに見える（GM向け）")
    async def roll_cmd(
        self,
        interaction: discord.Interaction,
        expr: str,
        secret: bool = False,
    ):
        try:
            spec = parse_expr(expr)
            all_rolls, kept, kept_sum, total = roll(spec)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        # 表示用の式
        head = f"{spec.n}d{spec.sides}"
        if spec.keep_mode and spec.keep_n:
            head += f"{spec.keep_mode}{spec.keep_n}"
        shown_expr = head + (spec.tail_expr or "")

        # 内訳：全ロール、keep強調、計算
        all_text = fmt_list(all_rolls, mark_kept=kept if (spec.keep_mode and spec.keep_n) else None)

        detail_lines = []
        detail_lines.append(f"🎲 `{shown_expr}`")
        detail_lines.append(f"出目: {all_text}")

        if spec.keep_mode and spec.keep_n:
            detail_lines.append(f"採用({spec.keep_mode}): {', '.join(map(str, kept))}  → 合計 {kept_sum}")
        else:
            detail_lines.append(f"合計: {kept_sum}")

        if spec.tail_expr:
            tv = safe_eval_arith(spec.tail_expr)
            sign_expr = spec.tail_expr
            detail_lines.append(f"補正: `{sign_expr}` (= {tv:+d})")

        embed = discord.Embed(
            title=f"🎲 {interaction.user.display_name} のロール",
            description="\n".join(detail_lines),
            color=discord.Color.green(),
        )
        embed.add_field(name="結果", value=f"**{total}**", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=secret)


async def setup(bot: commands.Bot):
    await bot.add_cog(DiceCog(bot))
