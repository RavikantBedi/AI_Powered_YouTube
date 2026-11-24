# Deployment & Setup

## 1. FFmpeg (required)

FFmpeg **is** the render engine — there is no fallback. `ffmpeg` and `ffprobe` must both
be on PATH.

**Windows** (pick one):

```powershell
# Chocolatey
choco install ffmpeg-full

# Scoop
scoop install ffmpeg

# Manual: download a release build from https://www.gyan.dev/ffmpeg/builds/
#   -> extract to C:\ffmpeg
#   -> add C:\ffmpeg\bin to PATH, then open a new terminal
```

**Linux / macOS**: `apt install ffmpeg` / `brew install ffmpeg`.

Verify: `ffmpeg -version && ffprobe -version`. Then `GET /health` reports `"ffmpeg": true`.

The Docker image installs FFmpeg and Noto Devanagari fonts already — the fonts matter,
because burned-in Hindi subtitles render as boxes without them.

## 2. Python environment

Requires Python 3.10+. Note that `anthropic` 1.x needs 3.11+; on 3.10 the pin resolves to
0.125.x, which supports everything this project uses.

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
```

## 3. Environment variables

Copy `.env.example` to `.env` at the repository root and fill it in.

### Required

| Variable | Purpose |
|---|---|
| `AGENCY_API_KEY` | Guards every API route. The server **refuses to serve** while it is unset or `change-me`. |
| `ANTHROPIC_API_KEY` | Every reasoning agent. Without it nothing runs. |
| one image key | `GEMINI_API_KEY`, `OPENAI_API_KEY`, or `STABILITY_API_KEY`. |

### Optional, with graceful degradation

| Variable | Without it |
|---|---|
| `ELEVENLABS_API_KEY` | Falls back to free `edge-tts` (no credentials needed). |
| `KLING_API_KEY` | Falls back to the built-in FFmpeg Ken-Burns motion engine. |
| `TELEGRAM_*` / `SMTP_*` / `NOTIFY_WEBHOOK_URL` | No push notification; the dashboard still shows completion. |
| `TAVILY_API_KEY` | Research uses Anthropic's server-side web search only. |
| `MUSIC_LIBRARY_DIR` contents | Renders narration-only rather than risking unknown-rights music. |
| YouTube OAuth | Renders and holds at `blocked_on: youtube_oauth`; resume after connecting. |

## 4. YouTube OAuth

1. In [Google Cloud Console](https://console.cloud.google.com/): create a project, enable
   **YouTube Data API v3**.
2. **APIs & Services → OAuth consent screen**: External, add yourself as a test user, and
   add the scopes `youtube.upload` and `youtube.force-ssl`.
3. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON.
4. Save it to the path in `YOUTUBE_CLIENT_SECRET_FILE` (default `backend/secrets/client_secret.json`).
5. Open the dashboard's **YouTube** page → *Get authorization URL* → grant access → paste
   the code back. The refresh token is written to `YOUTUBE_TOKEN_FILE` with owner-only
   permissions.

Or via API:

```bash
curl -X POST localhost:8000/api/youtube/connect -H "X-API-Key: $AGENCY_API_KEY" \
  -H "Content-Type: application/json" -d '{"redirect_uri":"urn:ietf:wg:oauth:2.0:oob"}'
# open the returned URL, then:
curl -X POST localhost:8000/api/youtube/callback -H "X-API-Key: $AGENCY_API_KEY" \
  -H "Content-Type: application/json" -d '{"code":"4/0A..."}'
```

**Quota note:** a video insert costs ~1600 units against a default 10,000/day quota — about
6 uploads per day until you request an increase.

**Unverified-app limits:** until your OAuth app is verified, uploads are forced to
`private` regardless of the requested privacy status, and custom thumbnails require a
verified channel. The publisher handles a thumbnail rejection gracefully (it logs and
continues) rather than failing the upload.

## 5. Music library

Rights-cleared audio only. Drop files into mood subdirectories and register each filename
in `assets/music/LICENSES.json`:

```
assets/music/grave/   assets/music/tense/
assets/music/hopeful/ assets/music/neutral/
```

A track with no license entry is refused by the Music Agent, and the copyright gate blocks
publishing if unknown-rights audio somehow reaches the render.

## 6. Running

**Development**

```bash
# terminal 1
cd backend && uvicorn app.main:app --reload --port 8000
# terminal 2
cd frontend && npm run dev
```

Dashboard at http://localhost:3000. Paste your `AGENCY_API_KEY` into the sidebar field once;
it is stored in localStorage and sent as `X-API-Key`.

**Docker**

```bash
docker compose up --build
```

Brings up Postgres, the backend (with FFmpeg and Devanagari fonts), and the dashboard.
Artifacts persist in the `artifacts` volume; secrets mount from `./secrets`.

## 7. First production

```bash
KEY=$AGENCY_API_KEY
ID=$(curl -s -X POST localhost:8000/api/projects -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"request":"Create a 12-minute Hindi documentary about the 1971 escape of three Indian Air Force pilots from a Pakistani prison, and publish it to my channel.","auto_start":true}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['project_id'])")

watch -n5 "curl -s localhost:8000/api/projects/$ID/status -H 'X-API-Key: $KEY' | python -m json.tool"
```

Expect roughly 25–60 minutes wall clock for a 12-minute video, dominated by image
generation, and **$3–8** depending on scene count and image provider.

## 8. Operating notes

**Resume, don't restart.** A blocked or failed project keeps every completed stage. Fix the
dependency and `POST /api/projects/{id}/retry` — it resumes from the last checkpoint and
re-reads provider credentials.

**Reading a BLOCKED project.** `blocked_on` names the exact dependency:
`image_provider`, `tts_provider`, `ffmpeg`, `youtube_oauth`, `policy_review`,
`quality_gate:SCRIPT`, or a missing environment variable. `error` carries the detail.

**Artifacts.** Everything lands under `ARTIFACT_ROOT/{project_id}/` — the final render at
`final/video.mp4`, script versions, per-scene images and clips, subtitles, thumbnails,
QA reports, and the provenance ledger. Nothing intermediate is discarded.

**Costs.** `GET /api/projects/{id}` returns actual spend by kind. Lower it by reducing
scene count (raise `AVG_SCENE_SECONDS` in `planner.py`) or by routing more tasks to the
`standard` tier in `router.py`.

## 9. Production hardening

Before running this anywhere real:

- **Migrations.** Schema is created with `create_all` for convenience. Add Alembic before
  the first schema change in production.
- **Task durability.** Runs live in asyncio tasks in the API process; a restart mid-run
  requires `retry` to resume. For multi-worker deployments, move `ProductionRun` execution
  behind Celery or Temporal — the stage functions are already idempotent and checkpointed,
  which is the hard part.
- **Auth.** One shared API key. Replace `app/api/deps.py` with real per-user auth and RBAC
  for multi-tenant use.
- **Secrets.** OAuth tokens are written to disk with owner-only permissions. Use a secret
  manager in production.
- **Rate limits.** Concurrency caps are per-stage constants (`MAX_CONCURRENT` in the image,
  motion, and voice agents). Tune them to your provider tier.
