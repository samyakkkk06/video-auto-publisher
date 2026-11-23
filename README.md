# Video Auto Publisher

Posts 5 short clips a day to **Instagram** and **YouTube**, automatically, with AI written
captions, descriptions, tags and hashtags. Runs on cloud runners, so it keeps working
when your laptop is off.

**Total cost: $0.00** every service used below has a genuinely free tier.

---

## Do this now

```
1.  Put your clips in      clips/
2.  Put your Word file in  descriptions/
3.  Put a free AI key in    .env        (GEMINI_API_KEY or GROQ_API_KEY)
4.  Run                    powershell -ExecutionPolicy Bypass -File setup.ps1
5.  Check                  powershell -ExecutionPolicy Bypass -File run_local.ps1 -Verify
```

Then finish the one time platform connections in **Setup** below (YouTube, Instagram, GitHub).
Steps 1 to 5 let you test captions and the schedule immediately, before any account is connected.

---

## What it does every day

| Time (your timezone) | What happens |
|---|---|
| 00:00 | New queue of 5 clips is built, AI writes all the copy, YouTube uploads are scheduled for the day |
| 07:00 | Clip 1 publishes to Instagram + YouTube |
| 11:00 | Clip 2 publishes to Instagram + YouTube |
| 16:00 | Clip 3 publishes to Instagram + YouTube |
| 20:00 | Clip 4 publishes to Instagram + YouTube |
| 00:00 | Clip 5 publishes to Instagram + YouTube |

You drop clips and a Word file in. Everything else happens by itself.

---

## How it works

```
clips/*.mp4  +  descriptions/*.docx
          |
          v
  [GitHub Release]  clips are stored here, not in git history
          |
          v  (cloud runner downloads what it needs)
  [AI writing]  Gemini / Groq / OpenRouter / Ollama  (free)
  Instagram caption + hashtags, YouTube title + description + tags
          |
          v
  [ffmpeg]  every clip -> 1080x1920 H.264 vertical (Reels + Shorts)
          |
          +--> [YouTube Data API]  upload as private + publishAt = slot time
          |
          +--> [GitHub Release]   public https URL (Instagram has to download it)
                    |
                    v
              [Instagram Graph API]  container -> wait FINISHED -> media_publish
```

---

## Setup

### Step 0 - Install (2 minutes, on your laptop)

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Creates the virtual environment, installs the Python packages, checks ffmpeg and creates `.env`.

If ffmpeg is missing, install it from <https://ffmpeg.org/download.html> and add it to PATH.

---

### Step 1 - Add your content (2 minutes)

- Put your 30 to 45 second clips in `clips/` (name them `clip_001.mp4`, `clip_002.mp4`, ...)
- Put your Word file in `descriptions/` (one paragraph block per clip, blank line between)
  - `.docx`, `.doc` (even when Word exported it as HTML), `.txt`, `.md`, `.rtf` and `.json` all work
- Then publish them to the cloud:

```powershell
powershell -ExecutionPolicy Bypass -File upload_clips.ps1
```

That uploads the clips to a GitHub Release, writes `clips_manifest.json` and pushes it.

**Why the clips live in a Release and not in git:** a video file in git history is stored forever
and every clone downloads it. 20 clips is already 300 MB. A GitHub Release holds the same files
for free, the repository stays at 0.1 MB, and the cloud runner downloads the clips it needs
before each publish run.

The publisher rotates through the clips, 5 per day.

---

### Step 2 - Free AI key (3 minutes)

The captions, descriptions and tags are written by a real AI model. Pick one free key:

| Provider | Get a key | Free allowance | Card needed |
|---|---|---|---|
| **Google Gemini** ⭐ | <https://aistudio.google.com/apikey> | 1500 requests/day | No |
| **Groq** | <https://console.groq.com/keys> | 30 requests/min | No |
| **OpenRouter** | <https://openrouter.ai/keys> | 50 requests/day on free models | No |
| **Ollama** | installed locally | unlimited, offline | No |

Put it in `.env`:

```
GEMINI_API_KEY=your_key_here
```

The model name is detected automatically, so a new Gemini release will not break anything.

You can add more than one. The system tries them in order and falls back automatically, so if one
hits its limit the others keep the captions flowing.

**Any OpenAI-compatible gateway works too.** If you have a key for NaraRouter, OpenAI, Together AI
or a self hosted model, fill in this block in `.env`:

```
CUSTOM_AI_BASE_URL=https://router.bynara.id/v1
CUSTOM_AI_API_KEY=sk-nry-xxxx
CUSTOM_AI_MODEL=            # leave empty to auto pick from /models
CUSTOM_AI_AUTH=Authorization
CUSTOM_AI_AUTH_PREFIX=Bearer
```

**About OpenRouter limits:** the free tier allows 50 free-model requests per day, shared across
all `:free` models, and it resets at 00:00 UTC. This system only needs 5 requests a day (one per
post), so it fits easily. If the key was already used up elsewhere, either wait for the reset or
add 10 credits once at <https://openrouter.ai/settings/credits>, which raises the limit to 1000
free requests per day while the free models still cost $0.

**About Ollama:** fully free and offline, but it needs a machine that stays on. On a laptop that
sleeps it will not work. Use it as a local fallback only.

No key at all? It still runs, using a built-in template writer. You just get less interesting copy.

Check your key any time:

```powershell
.venv\Scripts\python -m scripts.daily_run --test-ai
```

---

### Step 3 - GitHub repository (5 minutes)

This is what makes it run when your laptop is off.

```powershell
powershell -ExecutionPolicy Bypass -File setup_github.ps1
```

That script installs the GitHub CLI if needed, signs you in, creates the **public** repository,
pushes the code and uploads every secret from your `.env`. It also enables the workflow.

The repository **must be public** so Instagram can download your media from a public URL.

Prefer to do it by hand? Create the repo at <https://github.com/new> (public), then:

```powershell
git remote add origin https://github.com/YOUR_NAME/video-auto-publisher.git
git push -u origin main
```

---

### Step 4 - YouTube (10 minutes)

1. Open <https://console.cloud.google.com/apis/credentials> and create a project.
2. Enable the **YouTube Data API v3**.
3. Create an **OAuth client ID**, type **Desktop app**.
4. Copy the client id and secret into `.env`:
   ```
   YOUTUBE_CLIENT_ID=xxxx.apps.googleusercontent.com
   YOUTUBE_CLIENT_SECRET=xxxx
   ```
5. Get the refresh token:
   ```powershell
   .venv\Scripts\python -m scripts.youtube_auth
   ```
   A browser window opens, click Allow. The token is saved into `.env` automatically.

YouTube API quota: 10,000 units/day by default. An upload costs 1,600, so 5 uploads/day
(8,000 units) fits inside the free quota.

---

### Step 5 - Instagram (15 minutes)

Instagram only allows software posting for **Business or Creator** accounts.

1. Convert the account: Instagram app -> Settings -> Account -> Switch to Professional -> **Creator** (free).
2. Create a Meta developer app: <https://developers.facebook.com/apps> -> Create App -> **Other** -> Business.
3. Add the product **Instagram Graph API**.
4. Add permissions:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_read_engagement`
   - `pages_manage_posts`
5. Add a **Facebook Page**, link the Instagram account to it.
6. Generate a **User Access Token** with those permissions (use the Graph API Explorer).
7. Put them in `.env`:
   ```
   INSTAGRAM_USER_ID=17841400000000000
   INSTAGRAM_ACCESS_TOKEN=EAA...
   ```

Your Instagram numeric id: Instagram app -> Profile -> three dots menu -> Copy Profile Link,
the number at the end of the profile URL is the id.

Free limits: 25 API published posts per rolling 24 hours, so 5/day is comfortable.

---

### Step 6 - Public media hosting (5 minutes)

Instagram downloads the video from a public URL, so the file has to be reachable without a login.
GitHub Releases does this for free.

The system uploads each clip to a release named `media-YYYY-MM-DD` and uses the download link.

**In GitHub Actions** the built in `GITHUB_TOKEN` already has release upload permission, so
nothing to do, the workflow passes it automatically.

**For local runs** create a token:

1. <https://github.com/settings/tokens> -> **Generate new token (classic)**.
2. Check `repo` (and `public_repo` if the repo is public).
3. Generate, copy the token into `.env`:
   ```
   GITHUB_TOKEN=ghp_xxxx
   GITHUB_OWNER=YOUR_NAME
   GITHUB_REPO=video-auto-publisher
   ```

The repository has to be **public** for the download link to work without a login.

---

### Step 7 - GitHub Actions secrets (5 minutes)

If you ran `setup_github.ps1` this is already done, it uploads every value from `.env`.

To do it by hand: in the repo go to **Settings -> Secrets and variables -> Actions -> New repository secret**
and add every value from your `.env`:

```
GEMINI_API_KEY
GROQ_API_KEY                    (if used)
OPENROUTER_API_KEY              (if used)
YOUTUBE_CLIENT_ID
YOUTUBE_CLIENT_SECRET
YOUTUBE_REFRESH_TOKEN
INSTAGRAM_USER_ID
INSTAGRAM_ACCESS_TOKEN
GITHUB_TOKEN                    (only needed for local runs)
GITHUB_OWNER
GITHUB_REPO
TELEGRAM_BOT_TOKEN              (optional)
TELEGRAM_CHAT_ID                (optional)
```

Then: **Actions tab -> Auto Publish -> Enable workflow**.

The workflow wakes up every 10 minutes, checks which slot is due, and publishes.
GitHub's free plan gives 2,000 runner minutes/month for public repos, this uses roughly 300.

---

### Step 8 - Check everything (1 minute)

```powershell
powershell -ExecutionPolicy Bypass -File run_local.ps1 -Verify
```

Expected output:

```
=== SYSTEM CHECK ===

AI providers       : gemini
Clips found        : 12
Description blocks : 12
YouTube API        : OK (channel reachable, 3 recent uploads)
Instagram API      : OK (@yourhandle, id 17841400...)
Media hosting      : github_release (repo yourname/video-auto-publisher)

=== SCHEDULE ===

  00:00 America/New_York  (later)
  07:00 America/New_York  (later)
  ...
```

---

## Changing the schedule

Edit `config.json`:

```json
"timezone": "America/New_York",
"schedule_times": ["07:00", "11:00", "16:00", "20:00", "00:00"]
```

Current defaults are tuned for Tier 1 countries (US, UK, Canada, Australia, Germany, France).

| Slot | US | UK | Europe | Asia |
|---|---|---|---|---|
| 07:00 EST | 7 AM | 12 PM | 1 PM | 8 PM |
| 11:00 EST | 11 AM | 4 PM | 5 PM | 12 AM |
| 16:00 EST | 4 PM | 9 PM | 10 PM | 5 AM |
| 20:00 EST | 8 PM | 1 AM | 2 AM | 9 AM |
| 00:00 EST | 12 AM | 5 AM | 6 AM | 1 PM |

The same slot can be tuned per platform if you want, for example YouTube at 16:00 and
Instagram at 17:30. Change the schedule, then clear `state/published.json` so the new
times take effect immediately.

---

## Running it on your own machine

```powershell
powershell -ExecutionPolicy Bypass -File run_local.ps1            # normal run
powershell -ExecutionPolicy Bypass -File run_local.ps1 -DryRun    # plan only, no posting
powershell -ExecutionPolicy Bypass -File run_local.ps1 -Verify    # check connections
powershell -ExecutionPolicy Bypass -File run_local.ps1 -Force     # ignore caches, republish
```

To keep it running locally as a fallback, run it from **Task Scheduler** every 10 minutes
or use a simple loop:

```powershell
while ($true) { powershell -ExecutionPolicy Bypass -File run_local.ps1; Start-Sleep -Seconds 600 }
```

## Your daily routine

```
1. drop new clips into clips/
2. update descriptions/ if you added new ones
3. powershell -ExecutionPolicy Bypass -File upload_clips.ps1
```

Step 3 uploads only the new clips, updates the manifest and pushes it. Everything else,
including the actual publishing, happens on the cloud without you.

---

## What is automated vs manual

| Task | Who does it |
|---|---|
| Pick the 5 clips of the day | Automatic, rotates through `clips/` |
| Write Instagram caption + hashtags | Automatic, AI |
| Write YouTube title + description + tags | Automatic, AI |
| Convert video to vertical Reels format | Automatic, ffmpeg |
| Upload to YouTube at the right time | Automatic, official API |
| Publish the Reel to Instagram | Automatic, official API |
| Add a thumbnail | Automatic, ffmpeg frame grab |
| Log everything | Automatic, `output/logs/` |
| Tell you what happened | Optional Telegram alert |
| Replace a clip with a better one | Manual, drop the file in `clips/` |

---

## Troubleshooting

**"Video limit exceeded" on Instagram**
The API caps at 25 published posts per 24h rolling window. The system waits and publishes
the next slot automatically. Check `state/published.json` for what went out.

**YouTube uploads as private**
New Google Cloud projects need a free audit before uploads can go public.
<https://support.google.com/youtube/answer/12777536> -> request the audit, it usually takes a few days.
Until then, upload with `--force` after the audit passes, or switch the account to an
established channel.

**Instagram says the media failed to download**
The public URL was not reachable. Test the `public_url` value from `state/published.json`
in a private browser window. If it fails, re-run with `-Force` after checking `GITHUB_TOKEN`.

**Captions sound generic**
Improve them in `config.json` under `content.brand_voice` and `content.niche`.
Add a real example caption of your own to `content.extra_instructions`.

**A clip was not picked up**
Only `mp4, mov, m4v, webm, avi, mkv, mpg, mpeg` are picked up, and files starting with `_`
or `readme` are skipped on purpose.

**The cloud runner says 0 clips**
Run `powershell -ExecutionPolicy Bypass -File upload_clips.ps1` to push your clips to the
Release, then check the Actions log for the "Download clips from the release" step.

**The repo is too big / GitHub warns about size**
The clips are not in git. If you see a large repository, you have old history from before the
switch. Fix with a clean history:

```powershell
git checkout --orphan clean
git add -A
git commit -m "clean history"
git branch -D main
git branch -m clean main
git reflog expire --expire=now --all
git gc --prune=now
```

---

## Files

```
Automation/
├── clips/                    your video clips (not in git, stored in a Release)
├── clips_manifest.json       the small index git does track
├── descriptions/             your Word file with descriptions
├── output/
│   ├── media/                vertical video created by ffmpeg
│   ├── metadata/             generated captions, descriptions, tags
│   └── logs/                 daily logs
├── state/published.json      what has been posted, prevents double posting
├── config.json               schedule, platforms, timezone, voice
├── .env                      your keys (never commit this)
├── scripts/
│   ├── ai_client.py          free AI providers with fallback
│   ├── config.py             config and .env loading
│   ├── content_generator.py  Word file -> captions, descriptions, tags
│   ├── fetch_clips.py        download clips from the Release
│   ├── upload_clips.py       upload clips to the Release
│   ├── media_hosting.py      public URL for Instagram
│   ├── media_prep.py         ffmpeg vertical conversion
│   ├── publisher_instagram.py
│   ├── publisher_youtube.py
│   ├── daily_run.py          the orchestrator
│   ├── state.py              state, logging, notifications
│   └── youtube_auth.py       one time OAuth helper
├── .github/workflows/publish.yml   the cloud scheduler
├── setup.ps1                 one time install
├── setup_github.ps1          one time repo + secrets setup
├── upload_clips.ps1          publish new clips to the Release
└── run_local.ps1             run it
```

---

## Cost

| Service | Free allowance | Your usage |
|---|---|---|
| GitHub Actions | 2,000 min/month | ~300 min/month |
| YouTube Data API | 10,000 units/day | 8,000 units/day |
| Instagram Graph API | 25 posts/24h | 5 posts/day |
| GitHub Releases | unlimited | 150 clips/month |
| Gemini / Groq / OpenRouter | free tier | 5 writes/day |
| **Total** | | **$0.00** |
