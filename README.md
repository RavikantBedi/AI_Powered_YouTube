# AI Video Agency

> **One user request → One autonomous AI production company → One finished, published YouTube video.**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Node.js](https://img.shields.io/badge/Node.js-20+-green.svg)](https://nodejs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-14+-black.svg)](https://nextjs.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://docker.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🎬 What It Does

Give it a single prompt like:

> *"Create a 12-minute Hindi documentary about the history of the Indian space programme and publish it to my channel."*

The system autonomously handles the **entire production pipeline**:

| Stage | Capability |
|-------|------------|
| 🔍 **Research** | Web search, fact-checking, source verification |
| ✍️ **Scriptwriting** | Narrative structure, hooks, pacing, localization |
| 🎨 **Storyboarding** | Scene breakdown, visual planning, asset lists |
| 🖼️ **Image Generation** | AI art (Gemini, DALL-E, Stable Diffusion) |
| 🎞️ **Animation** | Ken Burns, transitions, motion graphics via FFmpeg |
| 🎙️ **Narration** | ElevenLabs premium TTS + free edge-tts fallback |
| 🎬 **Video Editing** | Timeline assembly, sync, effects, color grading |
| 📝 **Subtitles** | Multi-language, timed, styled captions |
| 🖼️ **Thumbnails** | A/B variants, CTR-optimized designs |
| 🔍 **SEO** | Titles, descriptions, tags, chapters, hashtags |
| ✅ **Policy Check** | YouTube Community Guidelines, copyright scan |
| 🚀 **Publishing** | OAuth upload, scheduling, playlist management |
| 📊 **Analytics** | Performance tracking, retention insights |

**No manual prompt-copying, asset downloads, or external editing tools required.**

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATOR                             │
│  (State Machine → Agent Graph → Quality Gates → Artifact Store) │
└───────────────────────────┬─────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   ┌─────────┐         ┌─────────┐         ┌─────────┐
   │Research │         │ Script  │         │ Story-  │
   │ Agents  │────────▶│ Writer  │────────▶│ board   │
   └─────────┘         └─────────┘         └────┬────┘
                                                 │
        ┌───────────────────┐         ┌─────────┴────┐
        ▼                   ▼         ▼              ▼
   ┌─────────┐         ┌─────────┐ ┌─────────┐ ┌─────────┐
   │  Image  │         │   TTS   │ │ Video   │ │  Thumb  │
   │  Gen    │────────▶│  Engine │─▶│ Render  │ │  Nail   │
   └─────────┘         └─────────┘ └────┬────┘ └─────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
               ┌─────────┐          ┌─────────┐          ┌─────────┐
               │Subtitles│          │  SEO    │          │ Policy  │
               └────┬────┘          └────┬────┘          └────┬────┘
                    │                    │                    │
                    └────────────────────┼────────────────────┘
                                         ▼
                                  ┌─────────────┐
                                  │  YouTube    │
                                  │  Publisher  │
                                  └─────────────┘
```

---

## 📁 Repository Structure

```
.
├── backend/                 # FastAPI + Agent Framework + Orchestrator
│   ├── app/
│   │   ├── agents/          # Specialist agents (Research → Publish) + Agent Factory
│   │   ├── providers/       # Pluggable adapters: LLM, Image, TTS, Video, YouTube, Search
│   │   ├── core/            # Orchestrator, Event Bus, Artifact Store, Model Router, Memory
│   │   ├── services/        # FFmpeg Render Engine
│   │   ├── db/              # SQLAlchemy Models + Session
│   │   ├── schemas/         # Pydantic Contracts between Agents
│   │   └── api/             # REST API
│   └── tests/
├── frontend/                # Next.js Dashboard (Projects, Live Agent Graph, New Video)
├── channel_dna/             # Machine-readable Channel Identities (AniDoc included)
├── docs/                    # Architecture + Deployment Guides
├── docker-compose.yml       # One-command full stack
├── .env.example             # Environment template
└── LICENSE
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **Node.js 20+**
- **FFmpeg** on PATH (`ffmpeg -version` to verify)
- **Docker** (optional, for containerized deploy)

### Option 1: Local Development

```bash
# 1. Clone & enter
git clone https://github.com/RavikantBedi/AI_Powered_YouTube.git
cd AI_Powered_YouTube

# 2. Backend setup
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -e ".[dev]"
cp ../.env.example ../.env
# Edit .env with your API keys (see Required Credentials below)

uvicorn app.main:app --reload --port 8000

# 3. Frontend setup (new terminal)
cd ../frontend
npm install
npm run dev
# Opens http://localhost:3000
```

### Option 2: Docker (Recommended for Production)

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 2. Launch everything
docker compose up --build -d

# 3. Access
# Backend API:  http://localhost:8000
# Frontend:     http://localhost:3000
# API Docs:     http://localhost:8000/docs
```

### Create Your First Video

```bash
# Via API (replace with your API key)
export AGENCY_API_KEY="your-key-from-.env"

curl -X POST http://localhost:8000/api/projects \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $AGENCY_API_KEY" \
  -d '{
    "request": "Create a 5-minute English explainer about quantum computing. Publish to my channel.",
    "channel": "anidoc"
  }'

# Start production (returns project ID)
curl -X POST http://localhost:8000/api/projects/<PROJECT_ID>/start \
  -H "X-API-Key: $AGENCY_API_KEY"
```

Monitor progress in the dashboard at `http://localhost:3000` or via:
```bash
curl http://localhost:8000/api/projects/<PROJECT_ID>/status \
  -H "X-API-Key: $AGENCY_API_KEY"
```

---

## 🔑 Required Credentials

| Capability | Environment Variable | Required? | Notes |
|------------|---------------------|-----------|-------|
| **Core Reasoning + Research** | `ANTHROPIC_API_KEY` | ✅ **Yes** | Powers all agent reasoning |
| **Image Generation** | `GEMINI_API_KEY` \| `OPENAI_API_KEY` \| `STABILITY_API_KEY` | ✅ One of | At least one required |
| **Premium Voiceover** | `ELEVENLABS_API_KEY` | ❌ No | Falls back to free `edge-tts` |
| **YouTube Publishing** | `client_secret.json` (OAuth) | ❌ For publishing | See [Deployment Guide](docs/DEPLOYMENT.md) |
| **Notifications** | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` / SMTP | ❌ No | Optional alerts |

> **Minimal working setup:** `ANTHROPIC_API_KEY` + one image provider key. The pipeline runs end-to-end with built-in FFmpeg Ken-Burns animation and edge-tts voiceover. YouTube publishing additionally requires OAuth setup.

**Missing providers never fake output** — they raise `ProviderUnavailableError`, the router attempts fallbacks, and if none exist the project is marked `BLOCKED` with the exact missing dependency.

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | Agent graph, state machine, quality gates, Agent Factory |
| [Deployment](docs/DEPLOYMENT.md) | Environment vars, YouTube OAuth, Docker, production hardening |
| [API Reference](http://localhost:8000/docs) | Interactive OpenAPI/Swagger docs (when running) |

---

## 🛠️ Development

### Running Tests

```bash
# Backend tests
cd backend
pytest -v

# Frontend tests
cd ../frontend
npm test
```

### Code Quality

```bash
# Backend
cd backend
ruff check .      # Lint
ruff format .     # Format
mypy .            # Type check

# Frontend
cd ../frontend
npm run lint
npm run typecheck
```

### Adding a New Agent

1. Create `backend/app/agents/your_agent/agent.py` extending `BaseAgent`
2. Define input/output schemas in `backend/app/schemas/`
3. Register in `backend/app/agents/factory.py`
4. Add to orchestrator graph in `backend/app/core/orchestrator.py`

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit changes: `git commit -m "feat: add amazing feature"`
4. Push to branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

- **Anthropic** for Claude models powering agent reasoning
- **Google** for Gemini image generation
- **ElevenLabs** for premium TTS
- **FFmpeg** for video rendering
- **FastAPI** & **Next.js** communities for excellent frameworks

---

## 📞 Support

- **Issues:** [GitHub Issues](https://github.com/RavikantBedi/AI_Powered_YouTube/issues)
- **Discussions:** [GitHub Discussions](https://github.com/RavikantBedi/AI_Powered_YouTube/discussions)
- **Email:** ravikantbedi@example.com

---

<p align="center">
  Made with ❤️ for creators who want to focus on ideas, not production.
</p>