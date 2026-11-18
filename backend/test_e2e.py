"""End-to-end verification for the Vibe backend (REST + WS)."""
import asyncio
import base64
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        # health
        r = await c.get("/api/health")
        check("health", r.status_code == 200, str(r.status_code))

        # register A (first user -> admin)
        r = await c.post("/api/auth/register", json={
            "display_name": "Alice", "email": "alice@example.com", "password": "secret123"})
        check("register A 201", r.status_code == 201, str(r.status_code) + " " + r.text[:200])
        a = r.json() if r.status_code == 201 else {}
        check("A is admin (first user)", a.get("role") == "admin", str(a.get("role")))
        token_a = a.get("access_token", "")
        id_a = a.get("id")

        # duplicate email -> 400
        r = await c.post("/api/auth/register", json={
            "display_name": "Alice2", "email": "alice@example.com", "password": "secret123"})
        check("duplicate email 400", r.status_code == 400, str(r.status_code))

        # register B (user)
        r = await c.post("/api/auth/register", json={
            "display_name": "Bob", "email": "bob@example.com", "password": "secret123"})
        check("register B 201", r.status_code == 201, str(r.status_code))
        b = r.json() if r.status_code == 201 else {}
        check("B is user", b.get("role") == "user", str(b.get("role")))
        token_b = b.get("access_token", "")
        id_b = b.get("id")

        # login both
        r = await c.post("/api/auth/login", json={"email": "alice@example.com", "password": "secret123"})
        check("login A 200", r.status_code == 200 and "access_token" in r.json(), str(r.status_code))
        r = await c.post("/api/auth/login", json={"email": "bob@example.com", "password": "secret123"})
        check("login B 200", r.status_code == 200, str(r.status_code))
        r = await c.post("/api/auth/login", json={"email": "bob@example.com", "password": "wrongpass"})
        check("login wrong password 401", r.status_code == 401, str(r.status_code))

        # me
        r = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        check("me A", r.status_code == 200 and r.json().get("email") == "alice@example.com", r.text[:120])

        # search finds B
        r = await c.get("/api/users/search", params={"q": "bob"},
                        headers={"Authorization": f"Bearer {token_a}"})
        found = [u for u in r.json()] if r.status_code == 200 else []
        check("search finds B", any(u["id"] == id_b for u in found), str(found))
        r = await c.get("/api/users/search", params={"q": ""},
                        headers={"Authorization": f"Bearer {token_a}"})
        check("search empty q -> []", r.status_code == 200 and r.json() == [], str(r.json()))

        # get user
        r = await c.get(f"/api/users/{id_b}", headers={"Authorization": f"Bearer {token_a}"})
        check("get user B", r.status_code == 200 and r.json().get("display_name") == "Bob", str(r.status_code))
        r = await c.get("/api/users/99999", headers={"Authorization": f"Bearer {token_a}"})
        check("get missing user 404", r.status_code == 404, str(r.status_code))

        # A creates conversation with B
        r = await c.post("/api/conversations", json={"user_id": id_b},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("create conv 201", r.status_code == 201, str(r.status_code) + " " + r.text[:200])
        conv = r.json() if r.status_code in (200, 201) else {}
        conv_id = conv.get("id")
        check("conv other_user is B", conv.get("other_user", {}).get("id") == id_b, str(conv))
        # duplicate create -> 200 same id
        r2 = await c.post("/api/conversations", json={"user_id": id_b},
                          headers={"Authorization": f"Bearer {token_a}"})
        check("create conv again 200 same id", r2.status_code == 200 and r2.json().get("id") == conv_id,
              str(r2.status_code))
        # self conv -> 400
        r = await c.post("/api/conversations", json={"user_id": id_a},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("conv with self 400", r.status_code == 400, str(r.status_code))

        # A sends text via REST
        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "Hello Bob!"},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("A sends text 201", r.status_code == 201, str(r.status_code) + " " + r.text[:200])
        msg1 = r.json() if r.status_code == 201 else {}
        msg1_id = msg1.get("id")
        check("text seen_by=[A]", msg1.get("seen_by") == [id_a], str(msg1.get("seen_by")))

        # B fetches history
        r = await c.get(f"/api/conversations/{conv_id}/messages",
                        headers={"Authorization": f"Bearer {token_b}"})
        hist = r.json() if r.status_code == 200 else []
        check("B history has message", any(m["id"] == msg1_id and m["content"] == "Hello Bob!" for m in hist),
              str(hist))

        # B conversation list shows unread 1
        r = await c.get("/api/conversations", headers={"Authorization": f"Bearer {token_b}"})
        lst = r.json() if r.status_code == 200 else []
        entry = next((x for x in lst if x["id"] == conv_id), None)
        check("B conv list unread=1", entry is not None and entry.get("unread_count") == 1, str(entry))
        check("B conv list last_message", entry is not None and entry.get("last_message", {}).get("content") == "Hello Bob!",
              str(entry))

        # B marks read
        r = await c.post(f"/api/conversations/{conv_id}/read", headers={"Authorization": f"Bearer {token_b}"})
        check("B mark read ok", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
        # A fetches history: seen_by includes B
        r = await c.get(f"/api/conversations/{conv_id}/messages",
                        headers={"Authorization": f"Bearer {token_a}"})
        hist = r.json() if r.status_code == 200 else []
        m = next((x for x in hist if x["id"] == msg1_id), None)
        check("seen_by includes B after read", m is not None and id_b in m.get("seen_by", []), str(m))
        # unread now 0 for B
        r = await c.get("/api/conversations", headers={"Authorization": f"Bearer {token_b}"})
        entry = next((x for x in r.json() if x["id"] == conv_id), None)
        check("B unread=0 after read", entry is not None and entry.get("unread_count") == 0, str(entry))

        # 403 for non-participant: register C, try reading conv
        r = await c.post("/api/auth/register", json={
            "display_name": "Carol", "email": "carol@example.com", "password": "secret123"})
        token_c = r.json().get("access_token", "")
        r = await c.get(f"/api/conversations/{conv_id}/messages",
                        headers={"Authorization": f"Bearer {token_c}"})
        check("non-participant messages 403", r.status_code == 403, str(r.status_code))

        # Upload small PNG as A
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("tiny.png", png, "image/png")},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("upload PNG 201", r.status_code == 201, str(r.status_code) + " " + r.text[:250])
        up = r.json() if r.status_code == 201 else {}
        media_id = up.get("id")
        check("upload kind=image media_url", up.get("kind") == "image" and up.get("media_url") == f"/api/media/{media_id}",
              str(up))
        # download back via ?token=
        r = await c.get(f"/api/media/{media_id}", params={"token": token_a})
        check("download PNG bytes match", r.status_code == 200 and r.content == png,
              f"status={r.status_code} len={len(r.content)} ct={r.headers.get('content-type')}")
        # download via Bearer header as B
        r = await c.get(f"/api/media/{media_id}", headers={"Authorization": f"Bearer {token_b}"})
        check("download PNG as B (Bearer)", r.status_code == 200 and r.content == png, str(r.status_code))
        # download as non-participant C -> 403
        r = await c.get(f"/api/media/{media_id}", params={"token": token_c})
        check("download PNG as C 403", r.status_code == 403, str(r.status_code))
        # media list
        r = await c.get(f"/api/conversations/{conv_id}/media",
                        headers={"Authorization": f"Bearer {token_b}"})
        check("media list has PNG", r.status_code == 200 and any(x["id"] == media_id for x in r.json()), r.text[:200])

        # Upload small audio (WAV header bytes) as A, kind=voice
        wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("note.wav", wav, "audio/wav")},
                         data={"kind": "voice"},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("upload WAV voice 201", r.status_code == 201, str(r.status_code) + " " + r.text[:250])
        up2 = r.json() if r.status_code == 201 else {}
        check("voice kind=voice", up2.get("kind") == "voice", str(up2))
        r = await c.get(f"/api/media/{up2.get('id')}", params={"token": token_b})
        check("download WAV bytes match", r.status_code == 200 and r.content == wav,
              f"status={r.status_code} len={len(r.content)}")

        # Upload rejected: text file as image
        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("evil.txt", b"hello", "text/plain")},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("upload text/plain rejected 400", r.status_code == 400, str(r.status_code))

        # online endpoint (no WS connected yet -> false)
        r = await c.get(f"/api/users/{id_b}/online", headers={"Authorization": f"Bearer {token_a}"})
        check("online endpoint false w/o WS", r.status_code == 200 and r.json().get("online") is False, r.text[:120])

    # ---- WebSocket test ----
    ws_a = await websockets.connect(f"ws://127.0.0.1:8000/api/ws?token={token_a}")
    ws_b = await websockets.connect(f"ws://127.0.0.1:8000/api/ws?token={token_b}")
    try:
        # presence: B should see A online (B connected after A; drain any presence msgs)
        # A sends a WS message, B receives it live
        await ws_a.send(json.dumps({"type": "message", "conversation_id": conv_id, "content": "live hello"}))
        got = None
        for _ in range(5):
            raw = await asyncio.wait_for(ws_b.recv(), timeout=5)
            d = json.loads(raw)
            if d.get("type") == "message":
                got = d
                break
        check("WS B receives A message live",
              got is not None and got["message"]["content"] == "live hello"
              and got["message"]["conversation_id"] == conv_id, str(got))
        # typing
        await ws_a.send(json.dumps({"type": "typing", "conversation_id": conv_id, "typing": True}))
        got_t = None
        for _ in range(5):
            raw = await asyncio.wait_for(ws_b.recv(), timeout=5)
            d = json.loads(raw)
            if d.get("type") == "typing":
                got_t = d
                break
        check("WS B receives typing",
              got_t is not None and got_t.get("user_id") == id_a and got_t.get("typing") is True,
              str(got_t))
        # online endpoint while connected
        async with httpx.AsyncClient(base_url=BASE, timeout=10, trust_env=False) as c2:
            r = await c2.get(f"/api/users/{id_b}/online", headers={"Authorization": f"Bearer {token_a}"})
            check("online endpoint true with WS", r.status_code == 200 and r.json().get("online") is True, r.text[:120])
        # invalid token rejected with 4401
        try:
            bad = await websockets.connect("ws://127.0.0.1:8000/api/ws?token=badtoken")
            await asyncio.wait_for(bad.recv(), timeout=5)
            check("WS bad token rejected 4401", False, "connection stayed open")
            await bad.close()
        except websockets.exceptions.ConnectionClosed as e:
            check("WS bad token rejected 4401", e.code == 4401, f"code={e.code}")
    finally:
        await ws_a.close()
        await ws_b.close()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


asyncio.run(main())
