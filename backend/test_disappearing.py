"""End-to-end verification for disappearing messages.

Run against a FRESH database (fresh DATA_DIR). BASE is read from the PORT
env var (default 8005), same style as test_e2e.py / test_admin.py.

Covers: timer set/validate, expires_at on text + media (REST + WS),
expiry hides content from participants (history, list preview, media
album, direct download) while the admin still sees everything with the
expired flag, and timer OFF means messages never disappear.
"""
import asyncio
import base64
import os
import sys
import time

import httpx

PORT = os.environ.get("PORT", "8005")
BASE = f"http://127.0.0.1:{PORT}"
results = []

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        # ---- setup: A (admin), B, C ----
        r = await c.post("/api/auth/register", json={
            "display_name": "Alice", "email": "alice@example.com", "password": "secret123"})
        a = r.json() if r.status_code == 201 else {}
        token_a, id_a = a.get("access_token", ""), a.get("id")
        h_a = {"Authorization": f"Bearer {token_a}"}
        r = await c.post("/api/auth/register", json={
            "display_name": "Bob", "email": "bob@example.com", "password": "secret123"})
        b = r.json() if r.status_code == 201 else {}
        token_b, id_b = b.get("access_token", ""), b.get("id")
        h_b = {"Authorization": f"Bearer {token_b}"}
        r = await c.post("/api/auth/register", json={
            "display_name": "Carol", "email": "carol@example.com", "password": "secret123"})
        cc = r.json() if r.status_code == 201 else {}
        token_c, id_c = cc.get("access_token", ""), cc.get("id")
        h_c = {"Authorization": f"Bearer {token_c}"}

        r = await c.post("/api/conversations", json={"user_id": id_b}, headers=h_a)
        conv_id = r.json().get("id") if r.status_code in (200, 201) else None
        check("A creates conv with B", conv_id is not None, str(r.status_code))

        # ---- timer defaults to off ----
        r = await c.get("/api/conversations", headers=h_a)
        conv = next((x for x in r.json() if x["id"] == conv_id), None)
        check("timer off by default", conv is not None and conv.get("disappearing_seconds") == 0,
              str(conv))

        # ---- message while OFF never expires ----
        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "stays forever"}, headers=h_a)
        m_off = r.json() if r.status_code == 201 else {}
        check("OFF message expires_at null",
              r.status_code == 201 and m_off.get("expires_at") is None and m_off.get("expired") is False,
              str(r.status_code) + " " + r.text[:150])

        # ---- timer validation ----
        r = await c.patch(f"/api/conversations/{conv_id}/disappearing",
                          json={"seconds": -1}, headers=h_a)
        check("negative seconds rejected", r.status_code == 422, str(r.status_code))
        r = await c.patch(f"/api/conversations/{conv_id}/disappearing",
                          json={"seconds": 86401}, headers=h_a)
        check("over-24h seconds rejected", r.status_code == 422, str(r.status_code))
        r = await c.patch(f"/api/conversations/{conv_id}/disappearing",
                          json={"seconds": 10}, headers=h_c)
        check("non-participant cannot set timer", r.status_code == 403, str(r.status_code))
        r = await c.patch("/api/conversations/999999/disappearing",
                          json={"seconds": 10}, headers=h_a)
        check("missing conversation 404", r.status_code == 404, str(r.status_code))

        # ---- set timer 10s (as B — either participant may set it) ----
        r = await c.patch(f"/api/conversations/{conv_id}/disappearing",
                          json={"seconds": 10}, headers=h_b)
        check("B sets timer 10s", r.status_code == 200 and r.json().get("disappearing_seconds") == 10,
              str(r.status_code) + " " + r.text[:150])
        # The OFF message stays non-expiring after the timer turns on.
        r = await c.get(f"/api/conversations/{conv_id}/messages", headers=h_a)
        off_msg = next((m for m in r.json() if m["id"] == m_off["id"]), None)
        check("old OFF message still never expires",
              off_msg is not None and off_msg.get("expires_at") is None, str(off_msg))

        # ---- send text + photo while timer ON ----
        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "this will vanish"}, headers=h_a)
        m_text = r.json() if r.status_code == 201 else {}
        check("ON text has expires_at ~10s",
              r.status_code == 201 and m_text.get("expires_at") is not None and m_text.get("expired") is False,
              str(r.status_code) + " " + r.text[:200])
        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("pic.png", PNG, "image/png")}, headers=h_a)
        m_pic = r.json() if r.status_code == 201 else {}
        check("ON photo has expires_at ~10s",
              r.status_code == 201 and m_pic.get("expires_at") is not None and m_pic.get("expired") is False,
              str(r.status_code) + " " + r.text[:200])
        pic_id = m_pic.get("id")

        # ---- visible to both users before expiry ----
        r = await c.get(f"/api/conversations/{conv_id}/messages", headers=h_b)
        hist = r.json() if r.status_code == 200 else []
        t = next((m for m in hist if m["id"] == m_text["id"]), None)
        p = next((m for m in hist if m["id"] == pic_id), None)
        check("B sees text content before expiry",
              t is not None and t.get("content") == "this will vanish", str(t))
        check("B sees photo media_url before expiry",
              p is not None and p.get("media_url") == f"/api/media/{pic_id}", str(p))
        r = await c.get(f"/api/conversations/{conv_id}/media", headers=h_b)
        check("photo in album before expiry",
              any(m["id"] == pic_id for m in r.json()), str(r.status_code))

        # ---- wait for expiry (10s timer + 1s margin) ----
        print("waiting 11s for expiry…")
        time.sleep(11)

        # ---- gone from BOTH users' history (content stripped) ----
        r = await c.get(f"/api/conversations/{conv_id}/messages", headers=h_b)
        hist = r.json() if r.status_code == 200 else []
        t = next((m for m in hist if m["id"] == m_text["id"]), None)
        p = next((m for m in hist if m["id"] == pic_id), None)
        check("B history: text expired, content stripped",
              t is not None and t.get("expired") is True and t.get("content") is None, str(t))
        check("B history: photo expired, media stripped",
              p is not None and p.get("expired") is True and p.get("media_url") is None, str(p))
        r = await c.get(f"/api/conversations/{conv_id}/messages", headers=h_a)
        t_a = next((m for m in r.json() if m["id"] == m_text["id"]), None)
        check("A history: text expired too (sender side)",
              t_a is not None and t_a.get("expired") is True and t_a.get("content") is None, str(t_a))

        # ---- chat list preview does not leak the expired content ----
        r = await c.get("/api/conversations", headers=h_b)
        conv = next((x for x in r.json() if x["id"] == conv_id), None)
        lm = (conv or {}).get("last_message") or {}
        check("list preview: last message expired, content null",
              lm.get("expired") is True and lm.get("content") is None, str(lm))
        check("list preview: OFF message still intact in history",
              any(m.get("content") == "stays forever" for m in hist),
              "OFF message count in history: " + str(sum(1 for m in hist if m.get("content") == "stays forever")))

        # ---- expired media gone from the album + direct download blocked ----
        r = await c.get(f"/api/conversations/{conv_id}/media", headers=h_b)
        check("photo gone from album after expiry",
              not any(m["id"] == pic_id for m in r.json()), str(r.json()))
        r = await c.get(f"/api/media/{pic_id}", params={"token": token_b})
        check("B direct download of expired photo 404", r.status_code == 404, str(r.status_code))

        # ---- admin still sees everything ----
        r = await c.get(f"/api/admin/conversations/{conv_id}/messages", headers=h_a)
        adm = r.json() if r.status_code == 200 else []
        t_adm = next((m for m in adm if m["id"] == m_text["id"]), None)
        p_adm = next((m for m in adm if m["id"] == pic_id), None)
        check("admin sees expired text WITH content + expired flag",
              t_adm is not None and t_adm.get("content") == "this will vanish" and t_adm.get("expired") is True,
              str(t_adm))
        check("admin sees expired photo WITH media_url + expired flag",
              p_adm is not None and p_adm.get("media_url") == f"/api/media/{pic_id}" and p_adm.get("expired") is True,
              str(p_adm))
        r = await c.get("/api/admin/media", headers=h_a)
        lib = r.json() if r.status_code == 200 else []
        p_lib = next((m for m in lib if m["id"] == pic_id), None)
        check("admin Media Library still lists expired photo",
              p_lib is not None and p_lib.get("expired") is True, str(p_lib))
        r = await c.get(f"/api/media/{pic_id}", params={"token": token_a})
        check("admin direct download still works", r.status_code == 200 and r.content == PNG,
              f"status={r.status_code} len={len(r.content)}")

        # ---- timer OFF again: new messages never expire ----
        r = await c.patch(f"/api/conversations/{conv_id}/disappearing",
                          json={"seconds": 0}, headers=h_a)
        check("timer set back to 0 (off)", r.status_code == 200 and r.json().get("disappearing_seconds") == 0,
              str(r.status_code))
        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "back to normal"}, headers=h_a)
        check("message after OFF has no expiry",
              r.status_code == 201 and r.json().get("expires_at") is None, str(r.status_code))

        # ---- WS-sent message also gets an expiry when the timer is on ----
        r = await c.patch(f"/api/conversations/{conv_id}/disappearing",
                          json={"seconds": 60}, headers=h_a)
        check("timer 60s for WS check", r.status_code == 200, str(r.status_code))
        import websockets
        uri = BASE.replace("http://", "ws://") + f"/api/ws?token={token_a}"
        async with websockets.connect(uri, max_size=10 * 1024 * 1024) as ws:
            await ws.send('{"type": "message", "conversation_id": %d, "content": "ws vanishing"}' % conv_id)
            got = None
            for _ in range(10):
                import json as _json
                ev = _json.loads(await ws.recv())
                if ev.get("type") == "message" and ev.get("message", {}).get("content") == "ws vanishing":
                    got = ev["message"]
                    break
            check("WS message gets expires_at when timer on",
                  got is not None and got.get("expires_at") is not None and got.get("expired") is False,
                  str(got))

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


asyncio.run(main())
