"""X - MUSIC PLAYER"""

# Copyright (C) 2021 USERGE-X
#
# Author : github.com/code-rgb [TG : @deleteduser420]
#          Plugin Help Written by -> @iTz_Black007
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import asyncio
import os
import re
from functools import wraps
from math import floor
from random import shuffle
from signal import SIGTERM
from typing import Dict, List, Optional, Set, Union

import youtube_dl
from pyrogram import filters
from pyrogram.errors import PeerIdInvalid, UserNotParticipant
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from userge import Config, Message, get_collection, pool, userge
from userge.plugins.bot.alive import _parse_arg
from userge.plugins.bot.utube_inline import BASE_YT_URL, get_yt_video_id, get_ytthumb
from userge.plugins.misc.upload import check_thumb
from userge.plugins.utils.songlink import find_url_from_msg, get_song_link
from userge.utils import (
    check_owner,
    escape_markdown,
    rand_key,
    safe_filename,
    time_formatter,
)
from wget import download
from youtube_dl.utils import DownloadError, ExtractorError, GeoRestrictedError
from youtubesearchpython.__future__ import VideosSearch

try:
    import ffmpeg
    from pytgcalls import GroupCall, GroupCallAction
except ModuleNotFoundError:
    os.system("pip3 install -U pytgcalls ffmpeg-python")
    import ffmpeg
    from pytgcalls import GroupCall, GroupCallAction


LOG = userge.getLogger(__name__)
SAVED_SETTINGS = get_collection("CONFIGS")
STREAM_LINK = re.compile(r"https?://[\S]+\.(?:m3u8?|audio|mp3|aac|[a-z]{1,4}:[0-9]+)")
FFMPEG_PROCESSES = {}
MAX_DURATION = int(os.environ.get("VC_SONG_MAX_DURATION", 600))
VC_GROUP_MODE_CHATS: Set[int] = set()


async def _init() -> None:
    global VC_GROUP_MODE_CHATS
    if gm_chats := await SAVED_SETTINGS.find_one({"_id": "VC_GROUP_MODE_CHAT"}):
        VC_GROUP_MODE_CHATS = set(gm_chats["chat_ids"])


class XPlayer(GroupCall):
    def __init__(self, chat_id: int):
        self.replay_songs = False
        self.is_active = False
        self.current_vol = 100
        self.playlist = []
        self.chat_id = chat_id
        self.chat_has_bot = False
        super().__init__(
            client=userge, play_on_repeat=self.replay_songs, path_to_log_file=""
        )

    def start_playout(self, key: str):
        self.input_filename = keypath(key)

    def replay(self) -> bool:
        self.play_on_repeat = self.replay_songs = not self.replay_songs
        return self.replay_songs

    def get_playlist(self) -> str:
        out = "🗒  **PLAYLIST**\n\n"
        if len(self.playlist) == 0:
            out += "`[ Empty ]`"
        else:
            current = self.playlist[0]
            out += f"▶️  **Now Playing :  🎵 [{escape_markdown(current['title'])}]({(BASE_YT_URL + current['id']) if current['yt_url'] else current['msg'].link})**\n"
            if len(self.playlist) > 1:
                out += "\n".join(
                    [
                        f"• **{x}.** [{escape_markdown(y['title'])}]({(BASE_YT_URL + y['id']) if y['yt_url'] else y['msg'].link})"
                        for x, y in enumerate(self.playlist[1:], start=1)
                    ]
                )
        return out

    async def join(self):
        # Joining the same group call can crash the bot
        # if not self.is_connected: (https://t.me/tgcallschat/7563)
        if not self.is_active:
            await super().start(self.chat_id)
            self.is_active = True

    async def leave(self):
        self.input_filename = ""
        # https://nekobin.com/nonaconeba.py
        try:
            await super().stop()
            self.is_active = False
        except AttributeError:
            pass


vc_chats: Dict[int, XPlayer] = {}


async def get_groupcall(chat_id: int) -> XPlayer:
    if not vc_chats.get(chat_id):
        group_call = vc_chats[chat_id] = XPlayer(chat_id)
        group_call.add_handler(
            network_status_changed_handler, GroupCallAction.NETWORK_STATUS_CHANGED
        )
        group_call.add_handler(playout_ended_handler, GroupCallAction.PLAYOUT_ENDED)
        if userge.has_bot:
            try:
                await userge.get_chat_member(
                    chat_id, (await userge.bot.get_me()).username
                )
            except (UserNotParticipant, PeerIdInvalid):
                pass
            else:
                group_call.chat_has_bot = True
    return vc_chats[chat_id]


async def network_status_changed_handler(gc: XPlayer, is_connected: bool) -> None:
    if is_connected:
        gc.is_active = True
        LOG.info(f"JOINED VC in {gc.chat_id}")
    else:
        gc.is_active = False
        LOG.info(f"LEFT VC in {gc.chat_id}")


async def playout_ended_handler(gc, filename) -> None:
    LOG.info(f"song ended in {gc.chat_id}")
    # If replay is on then just remove the song from playlist
    if len(gc.playlist) == 0:
        return
    to_del = keypath(gc.playlist.pop(0)["id"])
    if len(gc.playlist) > 0:
        await play_now(gc)
        if os.path.exists(to_del):
            os.remove(to_del)
        return
    if not gc.replay_songs:
        if os.path.exists(to_del):
            os.remove(to_del)


def add_groupcall(func):
    @wraps(func)
    async def gc_from_chat(m: Message):
        if m.chat.type in ("group", "supergroup", "channel"):
            gc = await get_groupcall(m.chat.id)
            await func(m, gc)

    return gc_from_chat


def keypath(key: str, thumb: bool = False) -> Union[str, tuple]:
    path_ = f"{Config.DOWN_PATH}{key}"
    return (f"{path_}.raw", f"{path_}.jpg") if thumb else f"{path_}.raw"


async def play_now(gc: XPlayer) -> None:
    r = gc.playlist[0]
    key = r["id"]
    client = userge.bot if r["has_bot"] else userge
    rawfile, thumb = keypath(key, thumb=True)
    if not os.path.exists(rawfile):
        if not (rawdata := await get_rawaudio_thumb(r)):
            await client.send_message(
                gc.chat_id, f"Skipped 1 Invalid Track: `{r['title']}`"
            )
            LOG.info("Skipped Invalid Track")
            gc.playlist.pop(0)
            if len(gc.playlist) > 0:
                await play_now(gc)
            return
        rawfile, thumb = rawdata
    gc.start_playout(key)
    if (msg_ := r["msg"]) and isinstance(msg_, Message):
        atitle = (
            f"[{r['title']}]({(BASE_YT_URL + r['id']) if r['yt_url'] else msg_.link})"
        )
    else:
        atitle = r["title"]
    text = f'🎵 **{atitle}**\n🕐 Duration : `{time_formatter(r["duration"])}`'
    if r["by_user"]:
        text += f'\n__Requested by__ :  👤 {r["by_user"]}'
    if thumb and os.path.exists(thumb):
        await client.send_photo(gc.chat_id, photo=thumb, caption=text)
        os.remove(thumb)
    else:
        await client.send_message(gc.chat_id, text=text, disable_web_page_preview=True)
    if len(gc.playlist) > 1:
        await get_rawaudio_thumb(gc.playlist[1])


async def get_rawaudio_thumb(data: Dict) -> Optional[tuple]:
    key = data["id"]
    msg = data["msg"]
    client = userge.bot if data["has_bot"] else userge
    thumb_loc = keypath(key, thumb=True)[1]
    if data["yt_url"]:
        song_path = await download_yt_song(key)
        if thumb := await check_thumb(
            await pool.run_in_thread(download)(data["thumb"] or await get_ytthumb(key))
        ):
            os.rename(thumb, thumb_loc)
            thumb = thumb_loc
    else:
        song_path = safe_filename(await client.download_media(msg.audio))
        thumb = await extract_thumb(song_path, key)
    if song_path and (outf := await convert_raw(song_path, key)):
        return outf, thumb


async def yt_x_bleck_megik(link: str) -> Optional[str]:
    if not (yt_id := get_yt_video_id(link)):
        if not (
            (output := await get_song_link(link))
            and (pf_ := output.get("linksByPlatform"))
            and (yt_ := pf_.get("youtube"))
        ):
            return
        yt_id = get_yt_video_id(yt_.get("url"))
    return yt_id


@pool.run_in_thread
def convert_raw(audio_path: str, key: str = None) -> Optional[str]:
    filename = key or rand_key()
    raw_audio = keypath(filename)
    try:
        ffmpeg.input(audio_path).output(
            raw_audio, format="s16le", acodec="pcm_s16le", ac=2, ar="48k"
        ).overwrite_output().run()
    except ffmpeg._run.Error as ff_e:
        LOG.info(f"ERROR: FFMPEG coudn't transcode rawfile due to :  {ff_e}")
    else:
        os.remove(audio_path)
        return filename


def check_audio(duration: int, audio_key: str, playlist: List) -> Optional[str]:
    # Duration
    if (invalid := (duration > MAX_DURATION or duration == 0)) :
        return f"Song Duration is {'invalid' if duration == 0 else 'too long'}"
    # check if already in Playlist
    if playlist and (audio_key in [x["id"] for x in playlist]):
        return "Song Already Added in Queue"


@pool.run_in_thread
def extract_thumb(audio: str, key: str) -> Optional[str]:
    thumb_path = keypath(key, thumb=True)[1]
    try:
        (ffmpeg.input(audio).output(thumb_path).run())
    except ffmpeg._run.Error:
        pass
    if os.path.exists(thumb_path):
        return thumb_path


@pool.run_in_thread
def get_ytvid_info(yt_id: str) -> Optional[Dict]:
    try:
        vid_data = youtube_dl.YoutubeDL({"no-playlist": True}).extract_info(
            BASE_YT_URL + yt_id, download=False
        )
    except ExtractorError:
        LOG.info("Can't Extract Info from URL")
    except Exception as err:
        LOG.info(err)
    else:
        return {
            "title": vid_data.get("title"),
            "duration": int(vid_data.get("duration", 0)),
            "thumb": vid_data.get("thumbnail"),
        }


@pool.run_in_thread
def download_yt_song(yt_id: str) -> Optional[str]:
    audio_path = os.path.join(Config.DOWN_PATH, f"{yt_id}.mp3")
    opts = {
        "no-playlist": True,
        "outtmpl": audio_path,
        "prefer_ffmpeg": True,
        "format": "bestaudio/best",
        "geo_bypass": True,
        "nocheckcertificate": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],
        "quiet": True,
        "logtostderr": False,
    }
    try:
        with youtube_dl.YoutubeDL(opts) as ytdl:
            status = ytdl.download([BASE_YT_URL + yt_id])
    except DownloadError as dl_err:
        LOG.info(f"Failed to download video due to ->  {dl_err}")
    except GeoRestrictedError:
        LOG.info("Youtube Video is Geo. Restricted")
    except Exception as y_e:
        LOG.info(y_e)
    else:
        if status == 0:
            return audio_path
        LOG.info(status)


def voice_chat_helpers_buttons():
    buttons = [
        [
            InlineKeyboardButton("🔌  Join", callback_data="vc_plyr_join"),
            InlineKeyboardButton("⏹  Stop", callback_data="vc_plyr_stop"),
        ],
        [
            InlineKeyboardButton("🎵  Player", callback_data="vcbtn_player"),
            InlineKeyboardButton("🎚  Volume", callback_data="vcbtn_vol"),
        ],
        [
            InlineKeyboardButton("🎤  Recorder", callback_data="vcbtn_rec"),
            InlineKeyboardButton("👥  Group Mode", callback_data="vcbtn_groupm"),
        ],
        [
            InlineKeyboardButton("🐞  Debug", callback_data="vcbtn_debug"),
            InlineKeyboardButton("✖️  Close", callback_data="vcbtn_delete"),
        ],
    ]
    return "🎛  **CONTROL PANNEL**", buttons


def volume_pannel():
    buttons = [
        [
            InlineKeyboardButton("➖", callback_data="vc_vol_-"),
            InlineKeyboardButton("➕", callback_data="vc_vol_+"),
        ],
        [
            InlineKeyboardButton("🔇  Mute", callback_data="vc_vol_mute"),
            InlineKeyboardButton("🔈  UnMute", callback_data="vc_vol_unmute"),
        ],
    ]
    return "🎚  **VOLUME**", buttons


def record_pannel():
    buttons = [
        [InlineKeyboardButton("⏺  Start REC.", callback_data="vc_rec_start")],
        [
            InlineKeyboardButton("▶️  Resume REC.", callback_data="vc_rec_resume"),
            InlineKeyboardButton("⏸  Pause REC.", callback_data="vc_rec_pause"),
        ],
        [
            InlineKeyboardButton("⏹  Stop REC.", callback_data="vc_rec_stop"),
            InlineKeyboardButton("🔄  Restart REC.", callback_data="vc_rec_restart"),
        ],
    ]
    return "🎤  **RECORDER**", buttons


def control_pannel():
    buttons = [
        [
            InlineKeyboardButton("▶️  Resume", callback_data="vc_plyr_resume"),
            InlineKeyboardButton("⏸  Pause", callback_data="vc_plyr_pause"),
        ],
        [
            InlineKeyboardButton("🔁  Repeat", callback_data="vc_plyr_repeat"),
            InlineKeyboardButton("🔀  Shuffle", callback_data="vc_plyr_shuffle"),
            InlineKeyboardButton("⏭  Skip", callback_data="vc_plyr_skip"),
        ],
        [
            InlineKeyboardButton("🗒  View Playlist", callback_data="vc_plyr_playlist"),
            InlineKeyboardButton("🚮  Clear Playlist", callback_data="vc_plyr_clearall"),
        ],
    ]
    return "🎵  **PLAYER**", buttons


def get_progress_string(current: int, total: int = 200) -> str:
    percentage = current * 100 / total
    prog_arg = "**Volume** : `{}%`\n" "```{}{}```"
    return prog_arg.format(
        int(percentage),
        "".join(("▰" for _ in range(floor(percentage / 10)))),
        "".join(("▱" for _ in range(10 - floor(percentage / 10)))),
    )


async def kill_radio(chat_id: int) -> None:
    if process := FFMPEG_PROCESSES.get(chat_id):
        process.send_signal(SIGTERM)
    radioraw = keypath(f"radio_{chat_id}")
    if os.path.exists(radioraw):
        os.remove(radioraw)


async def set_group_mode(chat_id: int, clearall: bool = False) -> str:
    global VC_GROUP_MODE_CHATS
    if clearall:
        out = f"playvc **disabled** for `All Chats` (**{len(VC_GROUP_MODE_CHATS)}**)"
        VC_GROUP_MODE_CHATS.clear()
    else:
        if chat_id in VC_GROUP_MODE_CHATS:
            VC_GROUP_MODE_CHATS.remove(chat_id)
            out = f"❌  playvc **disabled** for __Chat ID__: `{chat_id}`"
        else:
            VC_GROUP_MODE_CHATS.add(chat_id)
            out = f"✅  playvc **enabled** for __Chat ID__: `{chat_id}`"
    await SAVED_SETTINGS.update_one(
        {"_id": "VC_GROUP_MODE_CHAT"},
        {"$set": {"chat_ids": list(VC_GROUP_MODE_CHATS)}},
        upsert=True,
    )
    return out


async def append_playlist(gc: XPlayer, m: Message, media_grp: bool, **kwargs) -> None:
    thumb = kwargs["thumb"]
    title = kwargs["title"]
    gc.playlist.append(
        {
            "id": kwargs["audio_key"],
            "title": title,
            "thumb": thumb,
            "duration": kwargs["duration"],
            "has_bot": m.client.is_bot,
            "msg": kwargs["audio_msg"],
            "yt_url": kwargs["yt_id"],
            "by_user": (await userge.get_user_dict(m.from_user, attr_dict=True)).mention
            if m.from_user
            else None,
        }
    )

    if (pl_length := len(gc.playlist)) == 1:
        await play_now(gc)
        await m.delete()
    elif not media_grp:
        text = f"Added to Queue at **#{pl_length - 1}\nSONG :** `{title}`"
        await m.edit((f"[\u200c]({thumb})" + text) if thumb else text)


if userge.has_bot:

    @userge.bot.on_callback_query(filters.regex(pattern=r"^vcbtn_([a-z]+)$"))
    @check_owner
    async def manage_vc_settings(c_q: CallbackQuery):
        setting = c_q.matches[0].group(1)
        chat_id = c_q.message.chat.id
        gc = await get_groupcall(chat_id)
        if setting == "back":
            await c_q.answer()
            text, buttons = voice_chat_helpers_buttons()
        else:
            if setting == "delete":
                return await c_q.message.delete()
            if setting == "debug":
                await c_q.answer("Debugging ...")
                await gc.leave()
                gc = await get_groupcall(chat_id)
                if len(gc.playlist) != 0:
                    f_path = keypath(gc.playlist[0]["id"])
                    try:
                        os.remove(f_path)
                    except OSError:
                        pass
                    await play_now(gc)
                return await gc.join()
            if setting == "groupm":
                if c_q.message.chat.type == "channel":
                    out_ = "Not Permitted for Chat Type: CHANNEL"
                else:
                    out_ = f"👥  Group Mode :  {_parse_arg(bool('enabled' in await set_group_mode(chat_id)))}"
                return await c_q.answer(out_)
            await c_q.answer()
            if setting == "player":
                text, buttons = control_pannel()
            elif setting == "vol":
                text, buttons = volume_pannel()
                text += "\n\n" + get_progress_string(gc.current_vol)
            else:
                text, buttons = record_pannel()

            buttons += [[InlineKeyboardButton("Back", callback_data="vcbtn_back")]]

        await c_q.edit_message_text(
            text=text, reply_markup=InlineKeyboardMarkup(buttons)
        )

    @userge.bot.on_callback_query(filters.regex(pattern=r"^vc_([a-z]+)_([-a-z+]+)$"))
    @check_owner
    async def gc_toggles(c_q: CallbackQuery):
        answer = ""
        alert = False
        to_edit = False
        gc = await get_groupcall(c_q.message.chat.id)
        toggle_type = c_q.matches[0].group(1)
        to_change = c_q.matches[0].group(2)
        cb_text = to_change.title()
        if toggle_type == "rec":
            answer = "Not Implemented yet, 👉😬👈"
            alert = True
            # if to_change == "pause":
            #     gc.pause_recording()
            # elif to_change == "resume":
            #     gc.resume_recording()
            # elif to_change == "restart":
            #     gc.restart_recording()
        elif toggle_type == "plyr":
            if to_change == "playlist":
                buttons = [[InlineKeyboardButton("Back", callback_data="vcbtn_player")]]
                return await c_q.message.edit(
                    gc.get_playlist(),
                    reply_markup=InlineKeyboardMarkup(buttons),
                    disable_web_page_preview=True,
                )
            if to_change == "pause":
                gc.pause_playout()
                answer = f"⏸  {cb_text}d Voice Chat"
            elif to_change == "join":
                await gc.join()
                answer = f"🔌  {cb_text}ed Voice Chat"
            elif to_change == "resume":
                gc.resume_playout()
                answer = f"▶️  {cb_text}d Voice Chat"
            # elif to_change == "restart":
            #     gc.restart_playout()
            elif to_change == "stop":
                answer = f"⏹  {cb_text}ped Voice chat."
                await gc.leave()
            elif to_change == "repeat":
                answer = f"🔁  {cb_text} :  {_parse_arg(gc.replay())}"
            elif to_change == "skip":
                if len(gc.playlist) <= 1:
                    answer = "Nothing Found to Skip, add songs in queue first !"
                    alert = True
                else:
                    to_del = keypath(gc.playlist.pop(0)["id"])
                    await asyncio.gather(c_q.answer("⏭  Song Skipped"), play_now(gc))
                    if os.path.exists(to_del):
                        os.remove(to_del)
                    return
            elif to_change == "clearall":
                gc.playlist.clear()
                answer = "🚮  Playlist Cleared !"
            elif to_change == "shuffle":
                if len(gc.playlist) <= 1:
                    answer = "Nothing Found to Shuffle, add songs in queue first !"
                    alert = True
                else:
                    current = gc.playlist.pop(0)
                    shuffle(gc.playlist)
                    gc.playlist.insert(0, current)
                    answer = "🔀  Playlist Shuffled"
        else:
            to_edit = True
            if match := re.search(r"([0-9.]+)%", c_q.message.text):
                volume = int(float(match.group(1)) * 2)
            else:
                volume = 100
            if to_change == "+":
                volume += 20
            elif to_change == "-":
                volume -= 20
            elif to_change == "mute":
                gc.set_is_mute(True)
                volume = 0
            else:
                gc.set_is_mute(False)
                volume = 100
            volume = max(1, min(int(volume), 200))
            gc.current_vol = volume
            gc.set_my_volume(volume)
            text, buttons = volume_pannel()
            text += "\n\n" + get_progress_string(current=volume)
        await c_q.answer(answer, show_alert=alert)
        if to_edit:
            back_btn = [[InlineKeyboardButton("Back", callback_data="vcbtn_back")]]
            if buttons:
                buttons += back_btn
            else:
                buttons = back_btn
            await c_q.message.edit(
                text,
                reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True,
            )


# <------------------------> COMMANDS <------------------------> #


@userge.on_cmd(
    "joinvc",
    about={
        "header": "Join voice chat",
        "description": "Join voice chat in current group.",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def join_voice_chat(m: Message, gc: XPlayer):
    """Join the voice chat."""
    try:
        if gc.is_active:
            await m.edit("Already in Voice Chat !", del_in=5)
        else:
            await gc.join()
            await m.edit("**Joined** Voice Chat Successfully.", del_in=3)
    except RuntimeError:
        await m.err("No Voice Chat Found, start one first !")


@userge.on_cmd(
    "skipvc",
    about={
        "header": "Skip [n] songs",
        "description": "Skip current playing song",
        "usage": "{tr}skipvc [number of songs to skip]",
        "examples": "{tr}skipvc or {tr}skipvc 5",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def skip_song_voice_chat(m: Message, gc: XPlayer):
    """Skip Current playing song."""
    if len(gc.playlist) == 0:
        await m.edit("No Songs to Skip", del_in=5)
        return
    sk_e = "Provide a valid number of songs to skip"
    if m.input_str:
        if not (num := m.input_str.strip()).isdigit():
            await m.err(sk_e, del_in=5)
            return
        num = int(num)
    else:
        num = 1
    if 0 < num <= len(gc.playlist):
        gc.playlist = gc.playlist[num:]
        await m.edit(f"Skipped **{num}** songs.")
        if len(gc.playlist) > 0:
            await play_now(gc)
    else:
        await m.err(sk_e, del_in=5)


@userge.on_cmd(
    "playvc",
    about={
        "header": "Play song in voice chats",
        "description": "Play Songs in VC by audio file / media group or song name or song URL"
        "\n(supports spotify, youtube, deezer links etc.)",
        "usage": "{tr}playvc [reply to audio msg / Media group | song name | URL]",
        "examples": "{tr}playvc Beliver OR {tr}playvc [reply to audio file]",
    },
    filter_me=False,
    check_client=True,
    allow_private=False,
    allow_bots=False,
    check_downpath=True,
)
@add_groupcall
async def play_voice_chat(m: Message, gc: XPlayer):
    """Play songs..."""
    if (
        m.from_user
        and not (
            m.from_user.id in Config.OWNER_ID
            or (
                (m.from_user.id in Config.SUDO_USERS)
                and ("playvc" in Config.ALLOWED_COMMANDS)
            )
        )
        and m.chat.id not in VC_GROUP_MODE_CHATS
    ):
        return
    await m.edit("`Processing ...`")
    reply = m.reply_to_message
    playlist = gc.playlist
    if reply and reply.media_group_id:
        await m.edit("Finding playable Audio in media group")
        audio_list = []
        for msg in await m.client.get_media_group(m.chat.id, reply.message_id):
            if msg.audio:
                audio_key = msg.audio.file_unique_id
                duration = msg.audio.duration
                if not check_audio(duration, audio_key, playlist):
                    if title := msg.audio.performer:
                        title += f" - {msg.audio.title}"
                    else:
                        title = msg.audio.title or msg.audio.file_name
                    audio_list.append(
                        append_playlist(
                            gc,
                            m,
                            media_grp=True,
                            audio_key=audio_key,
                            title=title,
                            thumb="",
                            duration=duration,
                            audio_msg=msg,
                            yt_id=False,
                        )
                    )
        if len(audio_list) == 0:
            await m.err("No Audio Found")
        else:
            await m.edit(
                f"**{len(audio_list)} Songs** Added to Playlist Successfully !",
            )
            await asyncio.gather(*audio_list)
            await m.delete()
        return
    if reply and reply.audio:
        yt_id = False
        audio_msg = reply
        duration = reply.audio.duration
        audio_key = reply.audio.file_unique_id
        if title := reply.audio.performer:
            title += f" - {reply.audio.title}"
        else:
            title = reply.audio.title or reply.audio.file_name
        thumb = ""
        if err_msg := check_audio(duration, audio_key, playlist):
            return await m.err(err_msg, del_in=7)
        if len(gc.playlist) == 0:
            await m.edit("📥  __Downloading and transcoding__ ...", del_in=5)
    else:
        if (url_from_msg := await find_url_from_msg(m, show_err=False)) and (
            yt_id := await yt_x_bleck_megik(url_from_msg[0])
        ):
            audio_msg = url_from_msg[1]
        else:
            LOG.info("No Valid URL found now searching for given text")
            if m.input_str:
                search_q = m.input_str
                audio_msg = m
            elif reply and (reply.text or reply.caption):
                search_q = reply.text or reply.caption
                audio_msg = reply
            else:
                return await m.err("No Input Found", del_in=5)
            videosSearch = VideosSearch(search_q.strip(), limit=1)
            videosResult = await videosSearch.next()
            if len(res := videosResult["result"]) == 0:
                return await m.err(f'No Result found for Query:  "{search_q}"')
            yt_id = res[0]["id"]
        if not (vid_info := await get_ytvid_info(yt_id)):
            LOG.info("Something Went Wrong :P")
            return
        duration = vid_info["duration"]
        audio_key = yt_id
        if err_msg := check_audio(duration, audio_key, playlist):
            return await m.err(err_msg, del_in=7)
        title = vid_info["title"]
        thumb = vid_info["thumb"]
        await m.delete()
    await append_playlist(
        gc,
        m,
        media_grp=False,
        audio_key=audio_key,
        title=title,
        thumb=thumb,
        duration=duration,
        audio_msg=audio_msg,
        yt_id=bool(yt_id),
    )


@userge.on_cmd(
    "stopvc",
    about={
        "header": "Leave the fun.",
        "description": "Leave voice chat in current group.",
        "usage": "{tr}stopvc just use it.",
        "flags": {"-all": "stop all active voice chats"},
        "examples": "{tr}stopvc",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def stop_voice_chat(m: Message, gc: XPlayer):
    """Leave voice chat."""
    if "-all" in m.flags:
        await m.edit("Leaving All Voice chats")
        kill_list = []
        chat_ids = list(FFMPEG_PROCESSES)
        if chat_ids:
            kill_list += [kill_radio(rid) for rid in chat_ids]
        if vc_chats:
            kill_list += [i.leave() for i in vc_chats.values()]  # if i.is_connected
        if kill_list:
            await asyncio.gather(*kill_list)
    else:
        await m.edit("Sending signal.SIGTERM...")
        await kill_radio(m.chat.id)
        await gc.leave()
    await m.edit("Stopped Successfully.")


@userge.on_cmd(
    "pausevc",
    about={
        "header": "Silence for a moment !",
        "description": "Pause current playing song.",
        "usage": "{tr}pausevc just use it.",
        "examples": "{tr}pausevc",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def pause_voice_chat(m: Message, gc: XPlayer):
    """Pause songs."""
    await m.edit("⏸  __Pausing Media__ ...", del_in=5)
    gc.pause_playout()


@userge.on_cmd(
    "resumevc",
    about={
        "header": "Let the sound begin !",
        "description": "Resume current paused song.",
        "usage": "{tr}pausevc just use it.",
        "examples": "{tr}pausevc",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def resume_voice_chat(m: Message, gc: XPlayer):
    """Resume songs."""
    await m.edit("▶️  __Resuming Media__ ...", del_in=5)
    gc.resume_playout()


@userge.on_cmd(
    "mutevc",
    about={
        "header": "Shhhh stay silent.",
        "description": "Mute voice chat.",
        "usage": "{tr}mutevc just use it.",
        "examples": "{tr}mutevc",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def mute_voice_chat(m: Message, gc: XPlayer):
    """Shhhh..."""
    await m.edit("🔇  __Muting Self__ ...", del_in=5)
    gc.set_is_mute(True)


@userge.on_cmd(
    "unmutevc",
    about={
        "header": "Yey you can talk.",
        "description": "Mute voice chat.",
        "usage": "{tr}unmutevc just use it.",
        "examples": "{tr}unmutevc",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def unmute_voice_chat(m: Message, gc: XPlayer):
    """Unmute voice chat."""
    await m.edit("🔈  __UnMuting Self__ ...", del_in=5)
    gc.set_is_mute(False)


@userge.on_cmd(
    "volume",
    about={
        "header": "Let us reduce sound pollution.",
        "description": "A step for nature to reduce sound pollution as we are human.",
        "usage": "Use {tr}volume and setup volume interactively.",
        "examples": "{tr}volume",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def change_vol(m: Message, gc: XPlayer):
    """A step for nature."""
    if m.input_str and (vol := m.input_str.strip()).isdigit():
        gc.set_my_volume(int(vol))
        await m.edit(f"🔈  Volume changed to  **{vol}%**")
    elif m.client.is_bot:
        text, btns = volume_pannel()
        await m.reply(text, reply_markup=InlineKeyboardMarkup(btns))


@userge.on_cmd(
    "managevc",
    about={
        "header": "Manage voice chats.",
        "description": "Manage voice chats in user friendly way.",
        "usage": "Use {tr}managevc and manage !",
        "examples": "{tr}managevc",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def manage_voice_chat(m: Message, gc: XPlayer):
    """Manage your voice chats."""
    if not (m.client.is_bot or gc.chat_has_bot):
        return await m.err(
            "Bot Needed !, if bot is present in the group then try again with SUDO_TRIGGER."
        )
    out = voice_chat_helpers_buttons()
    if m.client.is_bot:
        await m.edit(out[0], reply_markup=InlineKeyboardMarkup(out[1]))
    else:
        await asyncio.gather(
            m.delete(),
            userge.bot.send_message(
                m.chat.id,
                out[0],
                reply_markup=InlineKeyboardMarkup(out[1]),
            ),
        )


@userge.on_cmd(
    "radio",
    about={
        "header": "Play streams or m3u8 playlist.",
        "description": "Stream audio playlist urls.",
        "usage": "Use {tr}radio [link]",
        "examples": "{tr}radio (yet to add example here)",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def start_radio(m: Message, gc: XPlayer):
    """Play radio stations."""
    text = None
    reply = m.reply_to_message
    if m.input_str:
        text = m.input_str
    elif reply and (reply.text or reply.caption):
        text = reply.text or reply.caption
    if not text:
        await m.err("No Input Found !", del_in=5)
        return
    if not (match := STREAM_LINK.search(text)):
        return await m.edit("No Valid station id found to start the radio !", del_in=7)
    await m.edit("📻 Connecting ...")
    radioraw = keypath(f"radio_{m.chat.id}")
    await kill_radio(m.chat.id)
    station_stream_url = match.group(0)
    process = (
        ffmpeg.input(station_stream_url)
        .output(radioraw, format="s16le", acodec="pcm_s16le", ac=2, ar="48k")
        .overwrite_output()
        .run_async()
    )
    FFMPEG_PROCESSES[m.chat.id] = process
    await m.edit(f"📻 Radio : `{station_stream_url}` is playing...")
    gc.start_playout(f"radio_{m.chat.id}")


@userge.on_cmd(
    "playlist",
    about={
        "header": "Get Song Playlist in current voive chat",
        "usage": "use {tr}playlist",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def playlist_voice_chat(m: Message, gc: XPlayer):
    """Song Playlist"""
    await m.edit_or_send_as_file(gc.get_playlist(), disable_web_page_preview=True)


@userge.on_cmd(
    "vcgroupmode",
    about={
        "header": "Allow current group members to use .playvc to play songs in voice chat",
        "flags": {"-d": "disable for all chats"},
    },
    allow_channels=False,
    allow_private=False,
    allow_bots=False,
)
async def groupmode_voice_chat(m: Message):
    """ enable / disable playvc for group members """
    await m.edit(await set_group_mode(m.chat.id, bool("-d" in m.flags)), del_in=5)


@userge.on_cmd(
    "repeatvc",
    about={
        "header": "Repeat song",
        "description": "turn on repeat for the last song in queue",
    },
    allow_private=False,
    allow_bots=False,
)
@add_groupcall
async def replay_voice_chat(m: Message, gc: XPlayer):
    """repeat voice chat media"""
    await m.edit(f"🔁  Repeat :  {_parse_arg(gc.replay())}", del_in=5)
