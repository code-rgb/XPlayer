# X-PLAYER

**Voice Chat Music and Radio Player for X**

## 📌 NOTE :

1. Never ever join an active voice chat with the same account. (**System may hang**)
2. To Listen to music in Voice chat best way is to use **USERGE-X** in your **alt account** and add all XPlayer commands in sudo (avoid using Multiple owner IDs) and for **interactive buttons** (`.managevc`) add your bot in group.

### 🌟 Features :

- Multiple Chat + Channel support.
- Uninterrupted playback (downloads and convert next song in advance)
- Text based integrated youtube search.
- Quick Toggles for volume up-down, mute-unmute etc.
- A traditional user friendly player packed with Resume, Pause, Repeat, Shuffle, Skip !
- Beta play from radio station streamable links. (see help `.radio`)
- Auto Song Download and Clean (No need to worry about storage)
- Supports music playback from :

```
1. Text (integrated youtube search)
2. Song URL (Spotify, YouTube, Deezer, Amazon music etc.)
3. Telegram audio / media group and *channels
```

---

### ⬇️ Installation :

#### Requirements

- [USERGE-X](https://github.com/code-rgb/USERGE-X) `v0.5.1` or above.

#### ⚙️ Config Vars

- `VC_SONG_MAX_DURATION` - Set the max. allowed song duration in sec. (defaults to `600`)
- `VC_GROUP_MODE` - [use `.vcgroupmode` to enable / disable], If `True` anyone in the group can use `.playvc` to play songs. (defaults to `False`)

Add this repo as **custom plugin repo** i.e
`CUSTOM_PLUGINS_REPO="https://github.com/code-rgb/XPlayer"`
or add [xplayer.py](https://github.com/code-rgb/XPlayer/blob/master/plugins/xplayer.py) in your custom plugin repo or forked X repo.

> e.g. `.setvar CUSTOM_PLUGINS_REPO https://github.com/code-rgb/XPlayer`

---

#### ❓ FAQ :

Q. Lag During Music Playback

> Completely depends on your machine, (may lag on Heroku free dynos) as pytgcalls have high CPU usage.

#### 🐞 Bugs :

Joined Voice Chat but doesn't play anything.

> It's a common pytgcalls issue.
> possible workaround -> use [`🐞 Debug`] button

#### ⚡️ Upcoming :

**Note** : no ETA (Delayed until pytgcalls gets stable)

- Recorder
- UI tweaks
- Inline Support for `.managevc` so no need to add bot in group

---

### Credits:

- Thanks @MarshalX for his [pytgcalls](https://github.com/MarshalX/tgcalls) library
- Plugin: @DeletedUser420
