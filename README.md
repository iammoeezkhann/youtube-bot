# Multi-Platform Video Uploader

Upload videos to **YouTube**, **TikTok**, **Instagram**, and **Facebook** on a schedule.

**Defaults:**
- Upload **one** video per run (oldest inbox first)
- **Public** visibility on YouTube
- **6 uploads/day** at global UTC times
- Move uploaded inbox files to `uploaded/`
- Optional **Telegram** inbox from your phone
- Optional **channel copy** — sync a profile and re-upload videos randomly

## Platforms

| Platform | How it uploads |
|----------|----------------|
| YouTube | Google OAuth + YouTube Data API |
| Instagram Reels | Meta Graph API (resumable upload) |
| Facebook | Meta Graph API (page video upload) |
| TikTok | TikTok Content Posting API |

Enable each platform in `config.yaml` under `platforms:`.

## 1. Google Cloud setup (YouTube)

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or pick an existing one).
3. Enable **YouTube Data API v3**.
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
5. Application type: **Desktop app**.
6. Download the JSON file and save it as:

```
credentials/client_secret.json
```

7. If Google shows "Testing" mode, add your Google account under **OAuth consent screen → Test users**.

**Quota note:** Each upload uses ~1,600 API units. Default daily quota is often 10,000 (~6 uploads/day). For hourly uploads (24/day), request a quota increase in Cloud Console.

## 2. Install

```powershell
cd C:\Users\moeez\Documents\Python\youtube-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 3. Configure

Edit `config.yaml`:

```yaml
watch_folder: "C:/Videos/to-upload"   # your folder
schedule:
  type: daily_times
  timezone: UTC
  times:
    - "07:00"
    - "12:00"
    - "15:00"
    - "18:00"
    - "21:00"
    - "00:00"
upload:
  mode: one
  privacy: public
  pick_strategy: fifo   # fifo = inbox first | random = random from channel pool

platforms:
  youtube:
    enabled: true
  instagram:
    enabled: false
  facebook:
    enabled: false
  tiktok:
    enabled: false
  meta:
    access_token: ""
    instagram_account_id: ""
    facebook_page_id: ""
```

**Global 6×/day schedule (UTC) — default in config.yaml:**

| UTC | US East | UK | Central EU | Sydney |
|-----|---------|-----|------------|--------|
| 07:00 | 3 AM | 8 AM | 9 AM | 5 PM |
| 12:00 | 8 AM | 1 PM | 2 PM | 10 PM |
| 15:00 | 11 AM | 4 PM | 5 PM | 1 AM |
| 18:00 | 2 PM | 7 PM | 8 PM | 4 AM |
| 21:00 | 5 PM | 10 PM | 11 PM | 7 AM |
| 00:00 | 8 PM | 12 AM | 1 AM | 10 AM |

**Other schedule examples:**

```yaml
# Every day at 9:00 AM UTC
schedule:
  type: cron
  timezone: UTC
  cron: "0 9 * * *"

# Every 30 minutes
schedule:
  type: interval
  hours: 0
  minutes: 30
```

Optional: put a `.jpg` with the same name as the video (e.g. `my-video.mp4` + `my-video.jpg`) to set a custom thumbnail.

## 4. Authenticate (one time)

```powershell
python main.py auth
```

A browser window opens. Sign in with the Google account that owns your YouTube channel.

### Meta (Instagram + Facebook)

1. Create a [Meta Developer](https://developers.facebook.com/) app.
2. Add **Instagram Graph API** and connect your Instagram Business account to a Facebook Page.
3. Generate a long-lived **Page access token** with publish permissions.
4. Put token + account/page IDs in `config.yaml` under `platforms.meta`, or set `META_ACCESS_TOKEN` in `.env`.

### TikTok

1. Go to [TikTok for Developers](https://developers.tiktok.com/) → **Manage apps** → **Create app**.
2. Add product **Content Posting API**.
3. Under **Login Kit**, add redirect URI: `http://127.0.0.1:8765/callback`
4. Request scope **`video.upload`** (inbox drafts — easiest to start) or **`video.publish`** (direct post — needs app audit).
5. Copy **Client key** and **Client secret** into `credentials/tiktok_app.json`:

```json
{
  "client_key": "YOUR_KEY",
  "client_secret": "YOUR_SECRET",
  "redirect_uri": "http://127.0.0.1:8765/callback"
}
```

6. In `config.yaml`, set `platforms.tiktok.enabled: true` and `post_mode: inbox`.
7. Connect your TikTok account:

```powershell
python main.py tiktok-auth
```

8. Test upload:

```powershell
python main.py once
```

**Inbox mode** (`post_mode: inbox`): video lands in your TikTok app inbox — tap the notification to add caption and publish.

**Direct mode** (`post_mode: direct`): posts straight to your profile (requires `video.publish` scope + TikTok app audit).

## 5. Run

```powershell
# Upload once right now (good for testing)
python main.py once

# Start the scheduler (6 uploads/day at UTC times in config.yaml)
python main.py run
```

Keep the terminal open while `run` is active on your PC, **or** deploy to a cheap always-on server (see below).

## 6. Upload from your phone (Telegram — recommended)

You do **not** need to be at your PC. Send videos to a Telegram bot; they land in the watch folder and upload on schedule.

### Setup

1. Open Telegram, message **@BotFather** → `/newbot` → copy the **bot token**.
2. Message **@userinfobot** → copy your **numeric user ID**.
3. Edit `config.yaml`:

```yaml
telegram:
  enabled: true
  bot_token: "YOUR_BOT_TOKEN"
  allowed_user_ids:
    - 123456789
```

4. Install the new dependency:

```powershell
pip install -r requirements.txt
```

5. Run everything together:

```powershell
python main.py run
```

### From your phone

- Open your bot in Telegram
- Send `/start`
- Send a **video** with a **caption** (used as title)
- Or paste a **YouTube / Shorts link** — bot downloads it
- Or sync a channel: `/channel https://tiktok.com/@username`
- Send `/status` → queue, channel pool, enabled platforms

### Channel copy mode

1. `/channel <url>` — downloads videos into `sources_folder`
2. Set `upload.pick_strategy: random` in `config.yaml`
3. Each scheduled run picks a **random** video and uploads to all enabled platforms

### Commands

| Telegram | What it does |
|----------|----------------|
| `/start` | Help |
| `/status` | Queue + schedule + platforms |
| `/channel <url>` | Sync channel/profile videos |
| `/cancel` | Cancel pending YouTube link |

| CLI | What it does |
|-----|----------------|
| `python main.py run` | Scheduler + Telegram (if enabled) |
| `python main.py telegram` | Telegram inbox only |
| `python main.py once` | Upload one video now |
| `python main.py auth` | YouTube OAuth |
| `python main.py tiktok-auth` | TikTok OAuth (browser login) |

---

## 7. Cloud folder option (alternative)

If you prefer a synced folder instead of Telegram:

1. Use **Google Drive**, **OneDrive**, or **Dropbox** on your phone.
2. Save videos into a folder that syncs to your PC, e.g. `C:/Users/you/OneDrive/YouTube-inbox`.
3. Set `watch_folder` in `config.yaml` to that path.

**Catch:** something must still **run the bot 24/7** at upload times. A cloud-synced folder on your PC only works when the PC is on. For true phone-only workflow, use **Telegram + a VPS** (below).

---

## 8. Run 24/7 without your PC (VPS)

Your PC does not need to stay on. Deploy to a small cloud server (~$5–6/month):

| Provider | Notes |
|----------|--------|
| [Hetzner](https://www.hetzner.com/cloud) | Cheap EU servers (good from Finland) |
| [DigitalOcean](https://www.digitalocean.com/) | Simple droplets |
| [Oracle Cloud](https://www.oracle.com/cloud/free/) | Free tier available |

**Steps:**

1. Create a Linux VPS (Ubuntu).
2. Copy this project + `credentials/` (OAuth files) to the server.
3. Run `python main.py auth` once on the server (browser OAuth — use SSH port forward or copy `token.json` from your PC after auth).
4. Enable Telegram in `config.yaml`.
5. Run with **systemd** or **screen** so it stays alive:

```bash
python main.py run
```

Now you can send videos from your phone anytime; the server uploads on the UTC schedule even when your PC is off.

---

## Windows Task Scheduler (optional)

If you prefer not to keep a terminal open:

1. Create a task that runs every hour.
2. Action: `C:\Users\moeez\Documents\Python\youtube-bot\.venv\Scripts\python.exe`
3. Arguments: `main.py once`
4. Start in: `C:\Users\moeez\Documents\Python\youtube-bot`

## Project layout

```
youtube-bot/
  config.yaml
  main.py
  bot/
    platforms/     # YouTube, Meta, TikTok uploaders
  credentials/     # OAuth files (gitignored)
  data/            # upload history + lock file
  logs/
```

## Troubleshooting

- **Missing client_secret.json** → complete Google Cloud OAuth setup above.
- **Upload lock error** → another run is in progress; wait or delete `data/upload.lock` if stuck.
- **Quota exceeded** → reduce frequency or request higher quota from Google.
- **Empty folder** → bot logs "No pending videos" and waits for the next run.
