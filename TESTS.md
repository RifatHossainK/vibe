# Vibe Phase 1 + Admin dashboard — test report

## Phase 2 (calls + groups) — backend: test_calls.py 73/73 PASS + all regressions PASS

Run: fresh DB per suite (`DATA_DIR=/tmp/vibe-coord-*/data`), plus
`python -m py_compile` on every changed `.py` file. Coordinator
independent re-run (fresh DBs, different ports): **test_e2e.py 42/42,
test_admin.py 63/63, test_profile.py 38/38, test_disappearing.py 29/29,
test_calls.py 73/73 — 245 checks total, all PASS.**

### test_calls.py 73/73 PASS (PORT=8006)

- **Groups:** create group → 201, creator auto-added, duplicate user_ids
  deduped, self excluded, 1–49 others, bad user → 400, blank name → 400;
  group WS message reaches ALL members (and not a non-member);
  non-member GET messages → 403; add member; remove member; leave
  (remove self); PATCH members on a 1:1 conversation → 400; group
  disappearing timer + media upload work.
- **Call signalling (live WS, two real clients):** invite → accept →
  offer/answer/ICE relay between the two peers only (a third connected
  user receives nothing); screen-share flag relayed; screen-while-ringing
  → error; accept-by-caller → error; offer with no call → error;
  non-participant invite → error; bad call_type → error; group invite →
  error ("Group calls are not supported in Phase 2").
- **Missed / busy / offline:** reject → `call:reject` + missed-call
  message stored ("📹 Missed video call"); invite to an offline user →
  `call:unavailable` + missed-call message stored ("📞 Missed audio
  call"); second invite while ringing → `call:busy`, no history stored.
- **Call history:** `call:end` with duration 154s → "📞 Audio call ·
  2:34" stored as a kind `"call"` message, visible in history; a second
  `call:end` relays only (no duplicate message); an active call ended
  with duration 0 stores "📞 Audio call · 0:00".
- **Disconnect:** WS close during an active call → peer gets `call:end`
  with `reason: "peer-disconnected"` + ended-call history with the
  elapsed duration; close during ringing → peer gets `call:end`, NO
  history stored.
- **ICE config:** `GET /api/config` → 200 with the Google STUN default;
  no auth → 401; a second server started with `ICE_SERVERS_JSON` set
  returns the override (`stun:custom.example.com:19302`).
- **Admin:** group appears in admin conversations (with `is_group` +
  name); group messages readable in the admin view; group media appears
  in the Media Library. Call-history rows are deliberately EXCLUDED from
  the media list / Media Library / `total_media` count (otherwise every
  missed call would show as a file-less "audio" item); the admin activity
  feed still falls back to "sent media" for call events.
- **Migration check:** a DB built with the pre-Phase-2 schema (no
  `is_group`/`name`/`disappearing_seconds`/`expires_at`/`username`/
  `avatar_path`) starts cleanly; startup adds all columns, no data wiped.
- One test-harness fix by the coordinator: the ICE-override sub-test
  used a fixed data dir, so a re-run crashed on duplicate-email
  registration; it now uses a fresh temp dir each run.

### Coordinator smoke check (live server, single-container mode): PASS

Fresh DB (PORT=8110), backend serving `frontend/out`: `GET /`, `/chat`,
`/admin` → 200; `GET /api/health` → 200. Register A (admin) + B + C;
create group "Family" → 201, 3 members (duplicate id deduped); group
text message → 201; `GET /api/config` → 200 with STUN default, 401
without auth; admin conversations list shows the group
(`is_group: true`). All test servers stopped afterwards; ports free.

### Phase 2 — frontend: build + typecheck PASS

- `npx tsc --noEmit` — exit 0, no type errors.
- `npm run build` — exit 0 (8/8 static pages). Routes: `/` 615 B,
  `/chat` 16.8 kB (was 10.6 kB — calls + groups code), `/admin` 6.21 kB,
  `/register` 2.9 kB. The rewrites warning is pre-existing.
- Built chat chunk contains "New group", "Screen", "Record",
  "Leave group", "Calling", "Share screen", "Incoming", "End call".
- **Node logic test** (mocked RTCPeerConnection/mediaDevices, real
  `calls.ts` compiled): **30/30 PASS** — invite→accept→offer→answer→end
  (call:end sent, duration 0, tracks stopped), incoming accept→answer,
  busy auto-reject (no modal, original call intact), WS drop →
  "Connection lost" with no call:end sent, peer reject/busy ends call,
  screen share on/off with `call:screen` flags, peer screen flag, ICE
  candidate accepted, incoming reject uses no media, ICE fallback
  returns Google STUN, formatDuration 02:34.

## NOT tested (plainly stated) — Phase 2

- **No live call in a real browser.** The signalling state machine is
  tested live over WS (invite/accept/offer/answer/ICE/end, busy,
  offline, disconnect) and the CallManager state machine is logic-tested
  in node — but a real WebRTC peer connection needs two real browsers
  (microphone/camera permission, STUN), which this VM cannot do. The
  first real test must be two browsers (or two phones) on the same
  conversation: one taps 📹, the other accepts, and both see/hear each
  other. If that fails, the fault is in the browser/ICE path, not the
  signalling — the signalling is verified.
- No live group-chat test in a browser (group REST + WS delivery are
  verified live; the group UI is built to that contract and the built
  chunks contain the controls).
- No Docker build test (no Docker daemon in this VM) — the Dockerfile is
  unchanged and written from the verified local build steps.
- No Render deploy test (no account created, per instructions).
- No TURN server: STUN-only by default. Calls between two strict
  symmetric-NAT networks may fail until a TURN server is added via
  `ICE_SERVERS_JSON`.

## Close fix + profile (avatar/username) + disappearing messages — backend: 172/172 PASS

Run: fresh DB per suite (`DATA_DIR=/tmp/vibe-*-test/data`), plus
`python -m py_compile` on every changed `.py` file.

### test_profile.py 38/38 PASS (PORT=8003)

- Register A with username `alice_wonder` → 201, username kept,
  `avatar_url` null; register B with no username → 201, username
  auto-generated from email (`bob`).
- Duplicate username → 400; too-short / bad-chars username → 400.
- `GET /me` returns username + `avatar_url`.
- Search finds A by username, by name, by email, case-insensitively, and
  by partial match; results carry `username` + `avatar_url`.
- PATCH /api/users/me: B changes username to `bob_builder` → 200, new
  name searchable, old name carried by no user; change to a taken or
  invalid username → 400; display_name change → 200.
- Upload a PNG avatar as A → 201, `avatar_url` = `/api/users/1/avatar`.
- Download the avatar via `?token=` as B → 200, **bytes match**; via
  Bearer → 200; without auth → 401.
- `me`, search results, and the conversation list `other_user` all show
  the avatar URL; admin `/api/admin/users` rows carry `username` +
  `avatar_url`.
- Non-image upload → 400; over-5 MB upload → 400.
- Replace avatar → 201, new bytes served; remove avatar → 200,
  `avatar_url` null, avatar route → 404, `me` shows null.

### test_disappearing.py 29/29 PASS (PORT=8005)

- Timer defaults to 0 (off). A message sent while OFF has
  `expires_at` null, `expired` false.
- Timer validation: negative → 422; >86400 → 422; non-participant → 403;
  missing conversation → 404.
- B sets the timer to 10s → 200, `disappearing_seconds` 10. A message
  sent while OFF stays non-expiring after the timer turns on.
- Text + photo sent while ON carry `expires_at` ≈ sent + 10s,
  `expired` false; both visible to B (content + `media_url`) and the
  photo is in the album before expiry.
- After 11s: both are `expired: true` with content/media **stripped**
  (null) in B's history AND in A's history (sender side); the chat-list
  preview's last message is expired with content null; the photo is
  gone from the album; B's direct download → 404.
- Admin still sees everything: conversation messages return the full
  text + `media_url` with `expired: true`; the Media Library still
  lists the photo with `expired: true`; admin direct download → 200,
  bytes match.
- Timer set back to 0 → new messages have no expiry. A WS-sent message
  while the timer is on also gets `expires_at`.

### Migration check (pre-existing DB): PASS

A database built with the OLD schema (no `username`/`avatar_path`/
`disappearing_seconds`/`expires_at` columns) starts the new server
cleanly: the startup migration adds all four columns and backfills a
username for every existing user (`old.user@example.com` → `olduser`,
`second@example.com` → `second`), with roles and data intact, and
search by the backfilled username finds the user.

### Regression: test_e2e.py 42/42 + test_admin.py 63/63 PASS

Both suites re-run unmodified on fresh DBs after all changes — no
Phase 1 or Admin feature broke.

## Message delete (admin moderation) + 1 GB streaming uploads — backend: test_delete.py PASS

- **Admin delete for everyone:** an admin can delete ANY user's message
  (text or media) for everyone — including in a conversation they are not
  a participant of. Participants see the "This message was deleted"
  placeholder (history, chat-list preview, album) and the media becomes
  undownloadable for them; `message_deleted` reaches participants over WS.
  The admin panel still shows the full original content/media, labelled
  with `deleted_by` (user id) and `deleted_by_name` (e.g. "deleted by
  admin"). Regular users can still only delete-for-everyone their OWN
  messages (403 otherwise), and admin `mode=me` on a conversation they
  are not in → 403. `deleted_by` is set on every successful
  delete-for-everyone (sender or admin) and stays null otherwise;
  participant views never include it. Admin Media Library items include
  `conversation_id`, so the frontend can call the DELETE endpoint from a
  Library item.
- **1 GB media uploads, streamed:** the upload cap is **1 GB for ALL
  media types** (photo, video, audio, voice — `MAX_MEDIA_BYTES` in
  `backend/app/config.py`, overridable with the `MAX_MEDIA_BYTES` env
  var). Uploads stream to disk in 1 MB chunks and are never held in
  memory; past the cap the upload aborts, the partial file is deleted,
  and the client gets a clear 400 ("File too large (max 1 GB)").
  `media_size` is the counted byte total. Downloads stream via Starlette
  `FileResponse` (verified — no read-into-memory path).
- **Test coverage:** a 120 MB video generated on disk in chunks, uploaded
  via streaming multipart → 201 with `media_size` == 120 MiB, then
  streamed back and byte-counted (== 120 MiB). The over-limit guard is
  exercised without sending a real 1 GB file: a second server started
  with `MAX_MEDIA_BYTES=5 MB` rejects a 6 MB upload with 400 ("File too
  large (max 5 MB)"), leaves no partial file and creates no message,
  while a 4 MB upload under the cap succeeds.

## Frontend — close fix + profile + disappearing: build + typecheck PASS

- `npx tsc --noEmit` — exit 0, no type errors.
- `npm run build` — exit 0 (8/8 static pages). Routes: `/` 615 B,
  `/chat` 10.6 kB, `/admin` 6.14 kB, `/register` 2.83 kB.
- Logic test (`/tmp/vibe-frontend-logic-test.js`, node): **14/14 PASS** —
  open → close flow at mobile (390px) and desktop (1280px) widths
  (list visible again, empty state shown, switching chats keeps the
  conversation open), the 1s expiry sweep (only due messages stripped,
  never-expiring untouched, list preview stripped), and the search
  stale-response guard (slow first reply ignored, fast second applied).
- Live serving check (backend + `frontend/out`, single-container mode):
  `GET /chat` → 200, `GET /` → title "Vibe"; the served chat chunk
  contains "Close chat" ×2, "Back to chats" ×1, "Disappearing
  messages" ×3, "This message disappeared" ×2, "Change profile
  picture" ×1, "Search people by name, username or email" ×1, and the
  empty-state text. The served admin chunk contains the "⏱ disappeared
  from chat" tag and the Picture column; the register chunk contains the
  Username field.

## NOT tested (plainly stated) — close fix / profile / disappearing

- No live end-to-end test of the new frontend flows against the backend
  in a browser (open → close on desktop/mobile, pick + save a profile
  picture, set a 10s timer and watch a message disappear live). The
  frontend is implemented exactly to the API contract, the endpoints
  are verified live, the built chunks contain the controls, and the
  state logic is verified by the node logic test — but the two have not
  been exercised together in a browser.
- No Docker build test (no Docker daemon in this VM) — the Dockerfile
  is unchanged and written from the verified local build steps.
- No Render deploy test (no account created, per instructions).


## Admin dashboard — backend: test_admin.py 63/63 PASS

Run: `backend/test_admin.py` (fresh DB, `DATA_DIR=/tmp/vibe-admin-test/data`,
`PORT=8001`), plus `python -m py_compile` on every changed `.py` file.

- Register A → 201, role **admin** (first user); register B, C → 201, role
  **user**.
- Admin stats as A → 200: 3 users, 5 messages, 3 media, 5 messages today,
  0 online; users endpoint → 200 with correct `message_count`/`media_count`
  and `is_admin` flags; `last_seen` derived from last sent message / read.
- Activity feed → 200, newest-first, contains register events (A/B/C) and
  message/media events with correct detail strings ("sent a message to Bob",
  "sent a photo", "sent a voice message").
- Conversations → 200: 1 conversation, both participant names,
  `message_count` 5, last message "📷 Photo".
- Conversation messages (admin) → 200, all 5 messages with `sender_name`.
- Media library → 200: 3 items with correct kind/filename/sender/
  conversation participants; `?kind=photo` → only photos, `?kind=audio` →
  only audio/voice, `?kind=video` → only videos.
- Media export → 200, `application/zip`, 3 files inside with entry names
  like `Alice_3_pic-a.png`, PNG bytes verified inside the ZIP.
- Admin A downloads a PNG from a B↔C conversation they are **not** in →
  200, exact bytes; non-participant C downloading A–B media still → 403.
- Non-admin B → **403 on all 7 admin endpoints** (stats, users, activity,
  conversations, conversation messages, media, media export); no token →
  **401**.
- One initial failure was a bug in the test's own expected ZIP-name pairing,
  not in the code — fixed the test, re-ran on a fresh DB, 63/63.

## Admin dashboard — regression: test_e2e.py 42/42 PASS

The existing Phase 1 suite was re-run unmodified against a fresh backend
(`DATA_DIR=/tmp/vibe-e2e-test/data`, `PORT=8000`): **42/42 PASS**, same
coverage as the Phase 1 run (auth, conversations, messages, media upload/
download, WebSocket live message + typing, online presence, 4401 on bad
token). No Phase 1 feature broke.

## Admin dashboard — coordinator smoke check: PASS

Independent smoke run (fresh DB, `PORT=8002`, plain `urllib`): register
admin + user, create conversation, send text, then all 7 admin endpoints
as admin → 200 with correct totals (2 users, 1 message, 1 today, 0 online),
activity contains register + message events, all 7 admin endpoints as
non-admin → 403, no token → 401. Test servers stopped after the runs;
ports 8000/8001/8002 free afterwards.

## Admin dashboard — frontend: build + typecheck PASS

- `npx tsc --noEmit` — exit 0, no type errors.
- `npm run build` — exit 0 ("Compiled successfully", 8/8 static pages).
  Route table includes `├ ○ /admin  5.65 kB  102 kB`; `out/admin.html`
  generated (4,902 bytes). The rewrites warning is pre-existing (same for
  the existing routes).
- Admin page verified by inspection: role gate via `me()` (non-admins see
  "Admin only", no admin fetch made), Dashboard tab (5 stat cards, Users
  table, Activity feed, Conversations with inline "Admin view — read only"
  message view, no send box / no edit-delete controls), Media Library tab
  (All/Photos/Videos/Audio filters, thumbnail grid, viewer modal, per-item
  Download, Download-all ZIP), 30s auto-refresh on both tabs, 🛡 Admin
  button in the chat sidebar only when `role === 'admin'`.

## NOT tested (plainly stated) — admin update

- No live end-to-end test of the `/admin` frontend against the backend in
  a browser (the admin page is implemented exactly to the API contract and
  the endpoints are verified live, but the two have not been exercised
  together in a browser).
- No Docker build test (no Docker daemon in this VM) — the Dockerfile is
  unchanged and written from the verified local build steps.
- No Render deploy test (no account created, per instructions).

---

# Vibe Phase 1 — test report

All tests were run in this VM against the real code (not mocks). Two rounds:
the backend agent's own end-to-end suite, then an independent re-run by the
coordinator plus a frontend build check and a static-serving check.

## Backend — agent's own suite: 42/42 PASS

Run: `backend/test_e2e.py` (fresh DB, PORT=8000), plus `python -m py_compile`
on every `.py` file.

- Register A → 201, role **admin** (first user); register B → 201, role
  **user**; duplicate email → 400; wrong password → 401; `GET /me` OK.
- Search finds B, empty `q` → `[]`; get user 200, missing user 404.
- Create conversation → **201** new, **200** same id on repeat,
  self-conversation → 400.
- A sends text via REST → 201, `seen_by=[A]`; B history contains it; B's
  conversation list shows `unread_count=1` + correct `last_message`;
  B marks read → `{"ok":true}`; A's message `seen_by` becomes `[1,2]`;
  B unread → 0; non-participant (Carol) → 403.
- Upload PNG (70 bytes) as A → 201, `kind=image`, `media_url=/api/media/2`;
  download via `?token=` **bytes match**, via Bearer as B matches, as C → 403;
  media list contains it.
- Upload WAV as `kind=voice` → 201, `kind=voice`; download bytes match;
  `text/plain` upload → 400.
- WebSocket: two clients connect, A sends `{"type":"message",...}` → **B
  receives it live**; A sends `typing=true` → **B receives typing event**;
  `GET /api/users/{id}/online` false with no WS, true while connected;
  bad token → **close 4401**; presence broadcasts on connect/disconnect.
- Static serving: API-only mode (no `frontend/` dir) works; with a frontend
  build, `GET /` → 200 serving the real `index.html`, `/api/health` still
  works, `/api/*` never shadowed by the static mount. `/chat` and `/chat/123`
  → 200 with `index.html` (SPA fallback).

Two real bugs were found and fixed during this testing:
1. `POST /api/conversations` 201 path used raw `JSONResponse`, which cannot
   serialise datetimes → 500. Fixed with `jsonable_encoder`.
2. WS broadcasts silently vanished (`message_to_dict` returned `datetime`
   objects, `send_json` raised, the send helper swallowed it). Fixed by
   returning ISO strings from the shared serialisers.

## Backend — coordinator independent re-run: 23/23 PASS

Run: fresh DB (`DATA_DIR=/tmp/vibe-test-data`, `PORT=8001`), independent test
script (httpx + websockets), after the agent's suite.

- Register A 201 + admin; register B 201 + user; wrong password 401; me A.
- Search finds Bob; create conversation 201; repeat 200 same id.
- REST text message 201; `seen_by` starts `[A]`; B history has the message;
  B list `unread_count=1`; B mark read ok; `seen_by` now `[A,B]`.
- Upload PNG 201 `kind=image`; download bytes match via `?token=` and via
  Bearer; media list has the image.
- Upload WAV 201 `kind=voice`; download bytes match.
- `text/plain` upload rejected 400.
- `online=true` while WS connected.
- WS live message arrives at B; WS typing event arrives at B.

Notes on the re-run: two initial failures were test-script bugs, not backend
bugs — (a) httpx picked up the VM proxy env vars (fixed with
`trust_env=False`), (b) the `kind` form field was sent inside `files=` instead
of `data=` (fixed), (c) the first WS assertions read queued presence events
before the message (fixed by skipping non-matching event types). After those
fixes the suite is 23/23. The test database was deleted after the run; the
shipped tree contains no test data.

## Frontend — build + typecheck: PASS

- `npm install` — 387 packages, exit 0.
- `npm run build` — passes. Routes: `/` 615 B, `/chat` 7.89 kB,
  `/login` 2.08 kB, `/register` 2.24 kB (all static). `out/` contains
  `index.html`, `login.html`, `register.html`, `chat.html`.
  One expected Next.js warning: rewrites do not apply with
  `output: 'export'` — they are dev-only by design; production serves
  `/api` same-origin.
- `npx tsc --noEmit` — exit 0, no errors.
- Static review: no TODOs/stubs, no `any` in API shapes, all media URLs go
  through the `?token=` helper, WS auto-reconnect present, no hardcoded
  secrets/passwords/keys, every button does something real except the call
  buttons which show the honest Phase-2 note.

## Static serving (single-container mode): PASS

With the backend running and `frontend/out/` present:
`GET /` → 200 (title "Vibe"), `GET /chat` → 200, `GET /login` → 200,
`GET /api/health` → 200.

## NOT tested (plainly stated)

- No live end-to-end test of the frontend against the backend in a browser
  (login → chat → send → receive across two browser sessions). The frontend
  is implemented exactly to the API contract and the backend is verified
  live, but the two have not been exercised together in a browser.
- No Docker build test (no Docker daemon in this VM) — the Dockerfile is
  written from the verified local build steps but has not been built.
- No Render deploy test (no account created, per instructions).
