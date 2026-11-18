# Vibe — public chat website (Phase 1 + Phase 2)

Messenger/WhatsApp-style chat at **vibe.rifatai.shop**. Phase 1 is the working
core: accounts, real-time 1-to-1 messaging, photo/video/audio/voice messages,
and a per-conversation media album. **Phase 2 adds real 1-to-1 audio/video
calls (WebRTC), screen sharing, silent call recording, call history, and
group chats.**

## What works in Phase 1

- **Auth** — register (display name + email + password), login, logout.
  bcrypt passwords + JWT (7-day). The first registered user is the admin.
- **Real-time messaging** — WebSocket. Text arrives instantly, history is
  saved, read receipts (✓ sent / ✓✓ seen), typing indicator, online/offline
  presence dots.
- **Contacts** — search users by name, username, or email (from the very
  first character; debounced ~250ms, stale results never overwrite newer
  ones), start a new chat. Left sidebar shows chats with last message,
  time, and unread count.
- **Profile pictures + usernames** — every user has a unique username
  (3–20 chars, letters/numbers/underscore; auto-generated from the email
  if not chosen at signup; changeable in My profile). Click your own
  picture/name at the top of the chat list to open My profile: change
  your picture (preview before saving, max 5 MB, PNG/JPG/GIF/WebP),
  remove it, or change your display name/username. Avatars show in the
  chat list, conversation header, on the other user's messages, and in
  the admin Users table; initials show when no picture is set.
- **Close / back** — on mobile the conversation header has a ← Back
  button; on desktop it has a ✕ Close button. Both return to the chat
  list (mobile) / the empty state (desktop). Clicking another chat
  switches conversations normally.
- **Disappearing messages** — in any conversation, either participant can
  set a timer (⏱ button in the header: Off / 10s / 30s / 1 min / 5 min /
  custom seconds, max 24h). Messages sent while the timer is on vanish
  from BOTH users' chat views after that many seconds (a small "This
  message disappeared" line stays), and the chat-list preview and media
  album hide them too. Messages are NEVER deleted from the database: the
  admin still sees them in the read-only conversation view and the Media
  Library, labelled "⏱ disappeared", and can still download the media.
  Messages sent while the timer is off never disappear.
- **Media messages** — send photos (preview in chat), videos (playable),
  audio files, and voice messages (record in the browser, playable).
  Caps: photo/video 50 MB, audio/voice 25 MB. Files are validated
  server-side (type + size) and stored on disk.
- **Media panel** — per conversation, a "Media" panel lists every shared
  photo, video, and audio file (album grid + audio list with play buttons).
- **Admin dashboard** — the first registered user (admin) sees everything:
  totals (users, messages, media, online now, messages today), a users
  table (online dot, last seen, message/media counts), a live activity
  feed (who registered, who messaged whom, who sent what), all
  conversations with a read-only message view, and a central Media Library
  with every photo/video/audio from every conversation (filters, viewer,
  per-item download, Download-all ZIP). Admin is **read-only** — the admin
  can never send, edit, or delete other users' messages. Open it from the
  🛡 Admin button in the chat sidebar (admin users only) or at `/admin`.
  Group chats appear in the admin conversation list (with name + member
  count) and are readable like any other conversation; call-history lines
  ("📞 Missed audio call", "📹 Video call · 2:34") show in the read-only
  view. Call-history lines are NOT counted as media and never appear in
  the Media Library.
- **UI** — Messenger-style: left chat list, right conversation, dark theme,
  responsive (mobile shows the list first, then the conversation).

## What works in Phase 2

- **1-to-1 audio + video calls** — WebRTC peer-to-peer, in the browser.
  📞 / 📹 in the conversation header start a call; the other side gets an
  incoming-call modal (name + avatar, Accept / Reject) no matter which
  conversation they have open. In-call view: remote video tile, local
  picture-in-picture, live call timer, mute/unmute, camera on/off (video),
  end call. If the other side is offline you get "unavailable" and a
  missed-call line is stored; if they are already in a call you get
  "busy"; if they reject, a missed-call line is stored. Signalling runs
  over the existing WebSocket (`call:invite/accept/reject/end/offer/
  answer/ice/screen`); media is peer-to-peer (no media server). ICE
  servers come from `GET /api/config` — Google STUN by default
  (`stun:stun.l.google.com:19302`, `stun1.l.google.com:19302`); override
  with the `ICE_SERVERS_JSON` env var to add a TURN server later.
  **Honest limit:** STUN-only, so calls may fail on strict symmetric-NAT
  networks — a TURN server is needed for those (not included; see below).
- **Screen sharing** — during a video call, either side can share their
  screen (whole screen or a window) instead of the camera; the other side
  sees it in the large tile with a "🖥 Screen" label. Stopping sharing
  (or stopping from the browser's own share UI) returns to the camera.
- **Call recording (silent)** — during a call, the Record button saves
  the call: audio calls → audio file, video calls → video file, uploaded
  as a media message in the conversation (so it appears in the Media
  Library and the per-conversation album like any other media). The
  recording captures the remote stream (what you hear/see). **Per the
  site owner's explicit rule, recording is silent: no recording indicator
  is shown to the other party, and nothing about recording is sent over
  the call. Both the admin and regular users can record this way.**
- **Call history** — missed and ended calls appear in the chat as a
  centered line: "📞 Missed audio call", "📹 Missed video call",
  "📞 Audio call · 2:34", "📹 Video call · 0:45". Call history never
  disappears (disappearing-message timers do not apply to it).
- **Group chats (messaging)** — "＋ New → New group": pick members,
  set a name, and chat. Groups send real-time text/media messages to
  all members, show each sender's name + avatar, have a member list,
  add-members, and Leave group. Groups appear in the chat list (👥 +
  name + member count) and in the admin panel. The disappearing-message
  timer works in groups. **Group voice/video calls are NOT in Phase 2**
  — call buttons are disabled in group chats ("Calls are 1-to-1 in
  Phase 2").

## Project structure

```
rifat-chat/
  backend/          FastAPI + WebSocket + SQLite (SQLAlchemy) + JWT + bcrypt
    app/            main, config, database, models, security, schemas, ws, helpers
    app/routers/    auth, users, conversations, media, admin, config
    run.py          uvicorn entry (PORT env, default 8000)
    requirements.txt
    test_e2e.py     Phase 1 end-to-end suite (42 checks)
    test_admin.py   Admin dashboard suite (63 checks)
    test_profile.py Profile (username + avatar) suite (38 checks)
    test_disappearing.py  Disappearing messages suite (29 checks)
    test_calls.py   Phase 2 calls + groups suite (73 checks)
    data/           created on first run: vibe.db + uploads/ (not in the repo)
  frontend/         Next.js 14 (App Router) + Tailwind v3 + TypeScript
    app/            / (redirect), /login, /register, /chat, /admin
    components/     ChatList, ChatWindow, MessageBubble, MediaPanel,
                    VoiceRecorder, NewChatModal, Avatar, ProfileModal,
                    CallView, IncomingCallModal, NewGroupModal, GroupInfoPanel
    lib/            api.ts (REST client), auth.ts, ws.ts, format.ts,
                    calls.ts (WebRTC CallManager)
  Dockerfile        single-container image (frontend static export + backend)
  render.yaml       Render Blueprint (free tier)
  TESTS.md          what was tested and the results
```

## Run locally (development)

You need Python 3.12 and Node 20+.

```bash
# 1. Backend (terminal 1) — http://localhost:8000
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py

# 2. Frontend (terminal 2) — http://localhost:3000
cd frontend
npm install
npm run dev
```

Open http://localhost:3000, register the first account (it becomes the admin),
then register a second account (in another browser/incognito) and chat.

The frontend talks to the backend through relative `/api/*` (Next.js rewrites
proxy to :8000 in dev). WebSocket connects to `ws://localhost:8000/api/ws`
via `NEXT_PUBLIC_WS_URL` in `frontend/.env.local` (dev only — the Docker
build removes that file so production uses same-origin WS).

## Run locally (production-like, single container)

```bash
docker build -t vibe .
docker run -p 8000:8000 -e SECRET_KEY="change-this-to-a-long-random-string" vibe
```

Open http://localhost:8000. The backend serves the frontend, the API, and
the WebSocket all on one port.

## Deploy to Render (free tier)

1. Push this project to a GitHub repo (e.g. `vibe`).
2. In Render: **New → Blueprint** → connect the repo → Render reads
   `render.yaml` and creates the `vibe` web service (Docker, free plan).
   `SECRET_KEY` is auto-generated; `DATA_DIR` is set to `/app/backend/data`.
3. Wait for the first deploy to finish. Your app will be live at a Render
   URL like `https://vibe-xxxx.onrender.com`. Open it and register the
   first account (it becomes the admin).

**Free-tier limits (honest):** Render free web services spin down after
inactivity (first visit after idle takes ~30-60s to wake up) and the
filesystem is **ephemeral** — the SQLite database and uploaded files are
lost when the service redeploys or restarts. For a production site where
chats and media must survive restarts, attach a Render persistent disk
(paid) or move the data directory to persistent storage. That is a
Phase-2/hosting decision, not a code change.

## Point vibe.rifatai.shop at it (Cloudflare DNS)

1. In Cloudflare, open the **rifatai.shop** zone → **DNS** → **Add record**.
2. Type: **CNAME** · Name: **vibe** · Target: your Render hostname
   (e.g. `vibe-xxxx.onrender.com`) · Proxy status: **DNS only**
   (grey cloud — Render terminates TLS itself; proxying through Cloudflare
   also works but "DNS only" is the simplest reliable setup).
3. Wait a few minutes for DNS to propagate, then open
   `https://vibe.rifatai.shop`. Render provisions the certificate.

## Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | `dev-secret-change-me` | **Change this in production.** Render generates one via `render.yaml`. |
| `DATA_DIR` | `backend/data` | SQLite file + uploads live here. |
| `PORT` | `8000` | Render sets this automatically. |
| `ICE_SERVERS_JSON` | Google STUN only | JSON array of RTCIceServer objects, e.g. `[{"urls":["stun:stun.l.google.com:19302"]},{"urls":["turn:turn.example.com:3478"],"username":"u","credential":"p"}]`. Add a TURN server here if calls fail on strict networks. Invalid JSON falls back to the STUN default. |

## How to use

1. Register an account (first account = admin).
2. In the left sidebar, click **New chat**, search a user by name/email,
   and start chatting. Or click **New group**, pick members, set a name,
   and chat with the whole group.
3. Send text, attach a photo/video/audio file with 📎, or record a voice
   message with 🎙️ (the browser will ask for microphone permission).
4. Click **Media** (⋯ in the chat top bar) to see every photo, video, and
   audio file shared in that conversation.
5. **Call someone (1-to-1):** click 📞 (audio) or 📹 (video) in the
   conversation header. The other side gets an incoming-call modal —
   Accept to talk, Reject to send a missed call. During a video call you
   can share your screen (🖥 button) and record the call (● button —
   silent: the other side is not told). Group chats have no call buttons
   in Phase 2.
6. **Admin?** If you are the first registered user, the 🛡 Admin button in
   the sidebar (or `/admin`) opens the dashboard: stats, users, activity,
   all conversations (read-only, groups included), and the Media Library
   with every photo/video/audio on the site. Non-admins see "Admin only" there.

## What is NOT in Phase 2

- Group voice/video calls (1-to-1 calls only; group call buttons are disabled).
- A TURN server (STUN-only by default — see `ICE_SERVERS_JSON` above).
  Calls may fail between two strict symmetric-NAT networks without one.
- Push notifications, message search, message editing/deletion.
- Persistent storage on the free tier (see the Render note above).
- A live browser end-to-end test of real WebRTC calls (the signalling
  state machine is tested live over WS, but a real call needs two real
  browsers — see TESTS.md).
