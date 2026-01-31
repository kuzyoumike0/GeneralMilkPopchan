import os
import re
import uuid
import random
import asyncio
import discord
from discord.ext import commands

# =====================
# ENV
# =====================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DEBUG_LOG = os.getenv("DEBUG_LOG", "0") == "1"

# 関西おばちゃん寄り（環境変数で調整）
TTS_VOICE  = os.getenv("TTS_VOICE", "ja-JP-NanamiNeural")  # 例: ja-JP-KeitaNeural
TTS_RATE   = os.getenv("TTS_RATE", "+15%")                 # 早口気味
TTS_PITCH  = os.getenv("TTS_PITCH", "+2Hz")                # 少し高め
TTS_VOLUME = os.getenv("TTS_VOLUME", "+10%")               # 少し大きめ

# 入退室しゃべりのクールダウン（秒）
VC_EVENT_COOLDOWN_SEC = int(os.getenv("VC_EVENT_COOLDOWN_SEC", "6"))

# =====================
# Intents
# =====================
intents = discord.Intents.default()
intents.message_content = True  # DevPortalでON必須
intents.guilds = True
intents.members = True          # ニック安定（DevPortalでON推奨）
intents.voice_states = True     # 入退室検知に必須

bot = commands.Bot(command_prefix="!", intents=intents)

# =====================
# 状態管理（ギルドごと）
# =====================
# 常駐先VC（guild_id -> voice_channel_id）
STAY_VC: dict[int, int] = {}

# 再生キュー（guild_id -> asyncio.Queue[str])
SPEAK_Q: dict[int, asyncio.Queue] = {}

# 再生タスク（guild_id -> asyncio.Task）
SPEAK_TASK: dict[int, asyncio.Task] = {}

# 入退室喋りの連打抑制
LAST_VC_EVENT_AT: dict[tuple[int, int], float] = {}  # (guild_id, user_id) -> monotonic time

# =====================
# ユーティリティ
# =====================
def now_mono() -> float:
    return asyncio.get_event_loop().time()

def make_call_name(author: discord.abc.User) -> str:
    name = getattr(author, "display_name", None) or getattr(author, "name", "あんた")
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 10:
        name = name[:10]
    if not re.search(r"[A-Za-z0-9ぁ-んァ-ン一-龥]", name):
        name = "あんた"
    suffix = random.choice(["ちゃん", "さん", ""])
    return f"{name}{suffix}"

def has_call(text: str) -> bool:
    return text.strip().startswith("おばちゃん")

def strip_call(text: str) -> str:
    t = text.strip()
    return t[len("おばちゃん"):].strip() if t.startswith("おばちゃん") else t

async def safe_respond(message: discord.Message, text: str):
    try:
        await message.reply(text, mention_author=False)
        return
    except Exception:
        pass
    try:
        await message.channel.send(text)
    except Exception as e:
        print("[send failed]", e)

# =====================
# おばちゃん文章（4行固定）
# =====================
TAILS = ["やで", "やん", "しよか", "せやな", "ほな", "大丈夫や"]
PAUSES = ["…", ""]
EMOJIS = ["", "🙂"]

CALL_PREFIX = [
    "{name}、",
    "{name}な、",
    "{name}、ちょい聞きぃ、",
    "{name}、こっちおいで、",
]

EMPATHY = ["それはしんどかったな", "よう言うてくれたな", "大変やったんやな"]
TSUKKOMI = ["無理しすぎやで", "抱え込みすぎやん", "根性論では乗り切れん話や"]
BASE_PRAISE = [
    "でもここに書けてるのは偉い",
    "今日も生きてるのは立派や",
    "呼びかけられた時点で基盤は残ってる",
]
SUGGEST = ["今は深呼吸だけでええで", "水かご飯、どっちか入れよ", "今日は最低限で済ませよ"]

CATEGORY_ADDON = {
    "work_tired": "仕事で削られてるやん、今日は最低限でええ",
    "work": "仕事は全部背負わんでええ",
    "tired": "今日は休む日やと思ってええ",
    "love": "それ、気持ちがちゃんと動いてる証拠や",
    "life": "生活回してるだけで十分や",
}

SENSITIVE_REPLY = [
    [
        "…それ、相当しんどかったんやな",
        "ここで話してくれてありがとう",
        "一人で抱えんでええで",
        "今、安全な場所におる？",
    ],
]

def detect_category(text: str) -> str:
    t = text
    if any(k in t for k in ["死にたい", "消えたい", "自殺", "自傷", "切りたい"]):
        return "sensitive"

    is_work = any(k in t for k in ["仕事", "会社", "上司", "残業", "会議", "納期"])
    is_tired = any(k in t for k in ["疲れ", "しんど", "無理", "限界", "眠", "だる", "つらい"])
    if is_work and is_tired:
        return "work_tired"

    if any(k in t for k in ["好き", "恋", "彼氏", "彼女", "既読", "未読", "告白"]):
        return "love"
    if is_work:
        return "work"
    if is_tired:
        return "tired"
    if any(k in t for k in ["家事", "生活", "掃除", "洗濯", "ご飯", "風呂", "片付け"]):
        return "life"
    return "general"

def make_reply(category: str, call_name: str) -> str:
    if category == "sensitive":
        lines = random.choice(SENSITIVE_REPLY).copy()
        if random.random() < 0.25:
            lines[0] = random.choice(CALL_PREFIX).format(name=call_name) + lines[0]
        return "\n".join(lines)

    tail = random.choice(TAILS)
    pause = random.choice(PAUSES)
    emoji = random.choice(EMOJIS)

    line1 = random.choice(EMPATHY) + pause + tail
    line2 = (CATEGORY_ADDON.get(category) or random.choice(TSUKKOMI)) + tail
    line3 = random.choice(BASE_PRAISE) + tail
    line4 = random.choice(SUGGEST) + tail + emoji

    if random.random() < 0.60:
        prefix = random.choice(CALL_PREFIX).format(name=call_name)
        if random.random() < 0.70:
            line1 = prefix + line1
        else:
            line2 = prefix + line2

    return "\n".join([line1, line2, line3, line4])

# =====================
# TTS（edge-tts）＋関西っぽい“間”整形
# =====================
def to_kansai_speak(full_reply: str) -> str:
    """
    4行を“しゃべり向き”に整形：
    - 行の終わりに軽い間
    - 読み上げ長すぎ防止
    """
    lines = [l.strip() for l in full_reply.split("\n") if l.strip()][:4]

    # 関西っぽい間（「…」と「、」でノリを作る）
    # 句点を増やすと、抑揚っぽく聞こえることがある
    cooked = []
    for i, ln in enumerate(lines):
        ln = ln.replace("やで🙂", "やで。🙂")
        ln = ln.replace("やで", "やで、") if ("やで" in ln and "やで、" not in ln) else ln
        ln = ln.replace("やん", "やん、") if ("やん" in ln and "やん、" not in ln) else ln
        cooked.append(ln)

    speak = "… ".join(cooked) + "。"

    # 上限（荒らし/負荷対策）
    if len(speak) > 260:
        speak = speak[:260] + "…"
    return speak

async def tts_to_mp3(text: str, out_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE,
        rate=TTS_RATE,
        pitch=TTS_PITCH,
        volume=TTS_VOLUME,
    )
    await communicate.save(out_path)

async def ensure_queue(guild_id: int) -> asyncio.Queue:
    q = SPEAK_Q.get(guild_id)
    if q is None:
        q = asyncio.Queue()
        SPEAK_Q[guild_id] = q
    return q

async def get_or_connect_vc(guild: discord.Guild, channel: discord.VoiceChannel) -> discord.VoiceClient:
    vc = discord.utils.get(bot.voice_clients, guild=guild)
    if vc and vc.is_connected():
        if vc.channel and vc.channel.id != channel.id:
            await vc.move_to(channel)
        return vc
    return await channel.connect(timeout=10)

async def play_mp3(vc: discord.VoiceClient, mp3_path: str):
    done = asyncio.Event()

    def after_play(err):
        if err:
            print("[VC play error]", err)
        done.set()

    src = discord.FFmpegPCMAudio(mp3_path)
    vc.play(src, after=after_play)
    await done.wait()

async def speaker_worker(guild_id: int):
    """
    ギルドごとの再生ワーカー：
    キューに入った音声を順番に再生する。
    """
    q = await ensure_queue(guild_id)

    while True:
        item = await q.get()
        if item is None:
            q.task_done()
            return

        (voice_channel_id, text) = item
        try:
            guild = bot.get_guild(guild_id)
            if guild is None:
                q.task_done()
                continue

            ch = guild.get_channel(voice_channel_id)
            if not isinstance(ch, discord.VoiceChannel):
                q.task_done()
                continue

            vc = await get_or_connect_vc(guild, ch)
            tmp = f"tts_{uuid.uuid4().hex}.mp3"

            speak_text = to_kansai_speak(text)
            if DEBUG_LOG:
                print("[TTS]", speak_text)

            await tts_to_mp3(speak_text, tmp)
            await play_mp3(vc, tmp)

            try:
                os.remove(tmp)
            except Exception:
                pass

            # 常駐先が設定されていないなら、再生後に退出
            if STAY_VC.get(guild_id) is None:
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass

        except Exception as e:
            print("[speaker_worker error]", e)

        finally:
            q.task_done()

async def enqueue_speech(guild_id: int, voice_channel_id: int, full_reply: str):
    q = await ensure_queue(guild_id)
    await q.put((voice_channel_id, full_reply))

    if guild_id not in SPEAK_TASK or SPEAK_TASK[guild_id].done():
        SPEAK_TASK[guild_id] = asyncio.create_task(speaker_worker(guild_id))

# =====================
# VC常駐コマンド（プレフィックス）
# =====================
@bot.command(name="join")
async def join_cmd(ctx: commands.Context):
    """
    !join  : コマンド実行者がいるVCに常駐
    """
    if not isinstance(ctx.author, discord.Member):
        return
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("先にVC入ってから呼んでな。")
        return
    vc_ch = ctx.author.voice.channel
    STAY_VC[ctx.guild.id] = vc_ch.id
    await ctx.send(f"ほな、ここ常駐するわ：{vc_ch.name}")

    # ついでに接続しておく
    try:
        await get_or_connect_vc(ctx.guild, vc_ch)
    except Exception as e:
        await ctx.send(f"入れんかった…権限（Connect/Speak）ある？ {e}")

@bot.command(name="leave")
async def leave_cmd(ctx: commands.Context):
    """
    !leave : 常駐解除して退出
    """
    gid = ctx.guild.id
    STAY_VC.pop(gid, None)
    vc = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if vc and vc.is_connected():
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass
    await ctx.send("ほな、また呼んでな。")

# =====================
# 入退室で喋る（常駐中のみ）
# =====================
JOIN_VOICE = [
    "{name}来たん？ えらいえらい。無理せんと座り。",
    "{name}おかえりやで。まず息しよか。",
]
LEAVE_VOICE = [
    "{name}抜けたんやな。おつかれさん、ちゃんと休みや。",
    "{name}、またな。来れた時点で勝ちやで。",
]
MOVE_VOICE = [
    "{name}部屋移動したんやな。迷子なってへん？",
    "{name}移動おつ。落ち着くとこ行こか。",
]

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    guild = member.guild
    gid = guild.id

    stay_id = STAY_VC.get(gid)
    if not stay_id:
        return  # 常駐してないなら喋らない

    # 送信するVCは「常駐VC」
    # （入退室した人が別VCだったら喋らない、が自然）
    target_vc = guild.get_channel(stay_id)
    if not isinstance(target_vc, discord.VoiceChannel):
        return

    # クールダウン（同じ人の連打抑制）
    key = (gid, member.id)
    last = LAST_VC_EVENT_AT.get(key, 0.0)
    if now_mono() - last < VC_EVENT_COOLDOWN_SEC:
        return
    LAST_VC_EVENT_AT[key] = now_mono()

    # 参加/退出/移動 判定
    name = make_call_name(member)

    # 参加：None -> target_vc
    if before.channel is None and after.channel and after.channel.id == target_vc.id:
        text = random.choice(JOIN_VOICE).format(name=name)
        await enqueue_speech(gid, target_vc.id, text + "\n" + "ほな、ゆっくりしよか。\n" + "来れた時点で偉い。\n" + "今日は何があったん？")
        return

    # 退出：target_vc -> None
    if before.channel and before.channel.id == target_vc.id and after.channel is None:
        text = random.choice(LEAVE_VOICE).format(name=name)
        await enqueue_speech(gid, target_vc.id, text + "\n" + "無理は禁物やで。\n" + "今日はよう頑張った。\n" + "あったかくして寝ぇ。")
        return

    # 移動：target_vc <-> 別VC
    if before.channel and after.channel and before.channel.id != after.channel.id:
        if before.channel.id == target_vc.id or after.channel.id == target_vc.id:
            text = random.choice(MOVE_VOICE).format(name=name)
            await enqueue_speech(gid, target_vc.id, text + "\n" + "落ち着く場所が正解や。\n" + "ええ判断やで。\n" + "水飲んどき。")
            return

# =====================
# チャット反応 → 常駐VCで喋る（常駐が無ければ送信者VC）
# =====================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    if not has_call(message.content):
        return

    body = strip_call(message.content)
    call_name = make_call_name(message.author)

    if body == "":
        reply = f"{call_name}、どしたん？\n無理せんでええ。\n呼べた時点で偉い。\n今いちばんしんどいのどれ？"
        await safe_respond(message, reply)

        # 喋る先：常駐VCがあればそこ、無ければ送信者VC
        gid = message.guild.id
        vc_id = STAY_VC.get(gid)
        if not vc_id:
            if isinstance(message.author, discord.Member) and message.author.voice and message.author.voice.channel:
                vc_id = message.author.voice.channel.id
        if vc_id:
            await enqueue_speech(gid, vc_id, reply)
        return

    category = detect_category(body)
    reply = make_reply(category, call_name)
    await safe_respond(message, reply)

    gid = message.guild.id

    # 喋る先：常駐VCがあればそこ、無ければ送信者VC
    vc_id = STAY_VC.get(gid)
    if not vc_id:
        if isinstance(message.author, discord.Member) and message.author.voice and message.author.voice.channel:
            vc_id = message.author.voice.channel.id

    if vc_id:
        await enqueue_speech(gid, vc_id, reply)
    else:
        await safe_respond(message, "VC入ってへんやん？ 先に入ってから呼んでな。")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing")

bot.run(DISCORD_TOKEN)
