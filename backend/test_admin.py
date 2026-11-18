"""End-to-end verification for the Vibe admin dashboard API.

Run against a FRESH database (fresh DATA_DIR). BASE is read from the PORT
env var (default 8001), same style as test_e2e.py.
"""
import asyncio
import base64
import io
import os
import sys
import zipfile

import httpx

PORT = os.environ.get("PORT", "8001")
BASE = f"http://127.0.0.1:{PORT}"
results = []

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
WAV = (b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00"
       b"\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        # ---- setup: register A (admin), B, C ----
        r = await c.post("/api/auth/register", json={
            "display_name": "Alice", "email": "alice@example.com", "password": "secret123"})
        check("register A 201", r.status_code == 201, str(r.status_code) + " " + r.text[:200])
        a = r.json() if r.status_code == 201 else {}
        check("A is admin (first user)", a.get("role") == "admin", str(a.get("role")))
        token_a, id_a = a.get("access_token", ""), a.get("id")

        r = await c.post("/api/auth/register", json={
            "display_name": "Bob", "email": "bob@example.com", "password": "secret123"})
        b = r.json() if r.status_code == 201 else {}
        check("register B 201 user", r.status_code == 201 and b.get("role") == "user",
              str(r.status_code))
        token_b, id_b = b.get("access_token", ""), b.get("id")

        r = await c.post("/api/auth/register", json={
            "display_name": "Carol", "email": "carol@example.com", "password": "secret123"})
        cc = r.json() if r.status_code == 201 else {}
        check("register C 201 user", r.status_code == 201 and cc.get("role") == "user",
              str(r.status_code))
        token_c, id_c = cc.get("access_token", ""), cc.get("id")

        h_a = {"Authorization": f"Bearer {token_a}"}
        h_b = {"Authorization": f"Bearer {token_b}"}

        # ---- A creates conversation with B, texts + media ----
        r = await c.post("/api/conversations", json={"user_id": id_b}, headers=h_a)
        check("A creates conv with B", r.status_code == 201, str(r.status_code))
        conv_id = r.json().get("id") if r.status_code == 201 else None

        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "Hello Bob!"},
                         headers=h_a)
        check("A sends text", r.status_code == 201, str(r.status_code))
        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "Hi Alice!"},
                         headers=h_b)
        check("B sends text", r.status_code == 201, str(r.status_code))

        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("pic-a.png", PNG, "image/png")}, headers=h_a)
        check("A uploads PNG", r.status_code == 201, str(r.status_code) + " " + r.text[:150])
        png_a_id = r.json().get("id") if r.status_code == 201 else None

        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("note.wav", WAV, "audio/wav")},
                         data={"kind": "voice"}, headers=h_a)
        check("A uploads WAV voice", r.status_code == 201, str(r.status_code))
        wav_id = r.json().get("id") if r.status_code == 201 else None

        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("pic-b.png", PNG, "image/png")}, headers=h_b)
        check("B uploads PNG", r.status_code == 201, str(r.status_code))
        png_b_id = r.json().get("id") if r.status_code == 201 else None

        # ---- admin: stats ----
        r = await c.get("/api/admin/stats", headers=h_a)
        s = r.json() if r.status_code == 200 else {}
        check("admin stats 200", r.status_code == 200, str(r.status_code))
        check("stats total_users=3", s.get("total_users") == 3, str(s))
        check("stats total_messages=5", s.get("total_messages") == 5, str(s))
        check("stats total_media=3", s.get("total_media") == 3, str(s))
        check("stats messages_today=5", s.get("messages_today") == 5, str(s))
        check("stats online_now=0 (no WS)", s.get("online_now") == 0, str(s))

        # ---- admin: users ----
        r = await c.get("/api/admin/users", headers=h_a)
        users = r.json() if r.status_code == 200 else []
        check("admin users 200, 3 users", r.status_code == 200 and len(users) == 3,
              str(r.status_code) + " " + str(len(users)))
        by_email = {u["email"]: u for u in users}
        ua, ub, uc = by_email.get("alice@example.com", {}), by_email.get("bob@example.com", {}), by_email.get("carol@example.com", {})
        check("users: A is_admin, counts 3/2",
              ua.get("is_admin") is True and ua.get("message_count") == 3 and ua.get("media_count") == 2,
              str(ua))
        check("users: B not admin, counts 2/1",
              ub.get("is_admin") is False and ub.get("message_count") == 2 and ub.get("media_count") == 1,
              str(ub))
        check("users: C not admin, counts 0/0",
              uc.get("is_admin") is False and uc.get("message_count") == 0 and uc.get("media_count") == 0,
              str(uc))
        check("users: all offline, last_seen set",
              all(u.get("online") is False and u.get("last_seen") for u in users), str(users))

        # ---- admin: activity ----
        r = await c.get("/api/admin/activity", headers=h_a)
        acts = r.json() if r.status_code == 200 else []
        check("admin activity 200", r.status_code == 200, str(r.status_code))
        types = [e["type"] for e in acts]
        check("activity has 3 register events", types.count("register") == 3, str(types))
        check("activity has 2 message events", types.count("message") == 2, str(types))
        check("activity has 3 media events", types.count("media") == 3, str(types))
        check("activity newest first",
              all(acts[i]["created_at"] >= acts[i + 1]["created_at"] for i in range(len(acts) - 1)),
              str([e["created_at"] for e in acts]))
        check("activity register detail",
              any(e["type"] == "register" and e["user_email"] == "alice@example.com"
                  and e["detail"] == "registered" for e in acts), str(acts))
        check("activity message detail names other user",
              any(e["type"] == "message" and e["user_name"] == "Alice"
                  and e["detail"] == "sent a message to Bob" for e in acts), str(acts))
        check("activity media details",
              any(e["type"] == "media" and e["detail"] == "sent a photo" for e in acts)
              and any(e["type"] == "media" and e["detail"] == "sent a voice message" for e in acts),
              str(acts))

        # ---- admin: conversations ----
        r = await c.get("/api/admin/conversations", headers=h_a)
        convs = r.json() if r.status_code == 200 else []
        check("admin conversations 200, 1 conv", r.status_code == 200 and len(convs) == 1,
              str(r.status_code) + " " + str(len(convs)))
        conv = convs[0] if convs else {}
        names = sorted(p["display_name"] for p in conv.get("participants", []))
        check("conv participants Alice+Bob", names == ["Alice", "Bob"], str(names))
        check("conv message_count=5", conv.get("message_count") == 5, str(conv))
        check("conv last message is B photo",
              conv.get("last_message_kind") == "image" and conv.get("last_message_text") == "📷 Photo",
              str(conv))

        # ---- admin: conversation messages (read-only) ----
        r = await c.get(f"/api/admin/conversations/{conv_id}/messages", headers=h_a)
        msgs = r.json() if r.status_code == 200 else []
        check("admin conv messages 200, 5 msgs", r.status_code == 200 and len(msgs) == 5,
              str(r.status_code) + " " + str(len(msgs)))
        check("conv msgs have sender_name",
              [m.get("sender_name") for m in msgs] == ["Alice", "Bob", "Alice", "Alice", "Bob"],
              str([m.get("sender_name") for m in msgs]))
        check("conv msgs kinds in order",
              [m.get("kind") for m in msgs] == ["text", "text", "image", "voice", "image"],
              str([m.get("kind") for m in msgs]))
        r = await c.get("/api/admin/conversations/99999/messages", headers=h_a)
        check("admin conv messages missing 404", r.status_code == 404, str(r.status_code))

        # ---- admin: media library ----
        r = await c.get("/api/admin/media", headers=h_a)
        media = r.json() if r.status_code == 200 else []
        check("admin media 200, 3 items", r.status_code == 200 and len(media) == 3,
              str(r.status_code) + " " + str(len(media)))
        by_id = {m["id"]: m for m in media}
        ma = by_id.get(png_a_id, {})
        check("media PNG A: kind photo, filename, sender",
              ma.get("kind") == "photo" and ma.get("filename") == "pic-a.png"
              and ma.get("sender_name") == "Alice" and ma.get("sender_email") == "alice@example.com"
              and ma.get("size_bytes") == len(PNG)
              and ma.get("download_url") == f"/api/media/{png_a_id}",
              str(ma))
        mw = by_id.get(wav_id, {})
        check("media WAV: kind audio, participants Alice+Bob",
              mw.get("kind") == "audio" and mw.get("filename") == "note.wav"
              and sorted(mw.get("conversation_participants", [])) == ["Alice", "Bob"],
              str(mw))
        check("media newest first",
              all(media[i]["created_at"] >= media[i + 1]["created_at"] for i in range(len(media) - 1)),
              str([m["created_at"] for m in media]))

        r = await c.get("/api/admin/media", params={"kind": "photo"}, headers=h_a)
        photos = r.json() if r.status_code == 200 else []
        check("media kind=photo only photos",
              r.status_code == 200 and len(photos) == 2 and all(m["kind"] == "photo" for m in photos),
              str(photos))
        r = await c.get("/api/admin/media", params={"kind": "audio"}, headers=h_a)
        audios = r.json() if r.status_code == 200 else []
        check("media kind=audio only audio/voice",
              r.status_code == 200 and len(audios) == 1 and audios[0]["kind"] == "audio",
              str(audios))
        r = await c.get("/api/admin/media", params={"kind": "video"}, headers=h_a)
        check("media kind=video empty",
              r.status_code == 200 and r.json() == [], str(r.status_code))

        # ---- admin: media export ZIP ----
        r = await c.get("/api/admin/media/export", headers=h_a)
        ok_zip = False
        zip_names = []
        if r.status_code == 200 and "application/zip" in r.headers.get("content-type", ""):
            try:
                zf = zipfile.ZipFile(io.BytesIO(r.content))
                zip_names = zf.namelist()
                ok_zip = sorted(zip_names) == [
                    "Alice_3_pic-a.png", "Alice_4_note.wav", "Bob_5_pic-b.png"]
                # verify bytes of one entry match the upload
                entry = next(n for n in zip_names if n.endswith("pic-a.png"))
                ok_zip = ok_zip and zf.read(entry) == PNG
            except Exception as e:
                ok_zip = False
                print("zip error:", e)
        check("admin media export 200 zip with 3 files + bytes match",
              ok_zip, f"status={r.status_code} ct={r.headers.get('content-type')} names={zip_names}")

        # ---- admin download of media in a conversation A is NOT in ----
        r = await c.post("/api/conversations", json={"user_id": id_c}, headers=h_b)
        conv_bc = r.json().get("id") if r.status_code in (200, 201) else None
        check("B creates conv with C", r.status_code == 201, str(r.status_code))
        r = await c.post(f"/api/conversations/{conv_bc}/upload",
                         files={"file": ("secret.png", PNG, "image/png")}, headers=h_b)
        check("B uploads PNG in B-C conv", r.status_code == 201, str(r.status_code))
        secret_id = r.json().get("id") if r.status_code == 201 else None
        r = await c.get(f"/api/media/{secret_id}", headers=h_a)
        check("admin A downloads B-C media (non-participant) exact bytes",
              r.status_code == 200 and r.content == PNG, f"status={r.status_code} len={len(r.content)}")
        # non-admin non-participant still blocked
        r = await c.get(f"/api/media/{png_a_id}", headers={"Authorization": f"Bearer {token_c}"})
        check("non-participant C still cannot download A-B media",
              r.status_code == 403, str(r.status_code))

        # ---- non-admin B: every admin endpoint -> 403 ----
        admin_urls = [
            "/api/admin/stats",
            "/api/admin/users",
            "/api/admin/activity",
            "/api/admin/conversations",
            f"/api/admin/conversations/{conv_id}/messages",
            "/api/admin/media",
            "/api/admin/media/export",
        ]
        for url in admin_urls:
            r = await c.get(url, headers=h_b)
            check(f"non-admin B {url} -> 403", r.status_code == 403, str(r.status_code))

        # ---- unauthenticated: -> 401 ----
        for url in admin_urls:
            r = await c.get(url)
            check(f"unauthenticated {url} -> 401", r.status_code == 401, str(r.status_code))

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


asyncio.run(main())
