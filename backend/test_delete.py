"""End-to-end verification for message delete (for me / for everyone).

Run against a FRESH database (fresh DATA_DIR). BASE is read from the PORT
env var (default 8007), same style as test_e2e.py / test_disappearing.py.

Covers: delete-for-me hides a message only for that user (history,
chat-list preview + unread, media album, direct download) while the
other user still sees it and can download its media; delete-for-everyone
by the sender hides content/media for BOTH users (history placeholder,
preview, album, download) and broadcasts message_deleted over WS;
non-sender delete-for-everyone -> 403; non-participant -> 403; message
not found -> 404; invalid/missing mode -> 400/422; call-history lines
cannot be deleted -> 400; idempotency; delete-for-me on an already
deleted-for-everyone message still works; admin still sees the full
original content/media (labelled deleted) and can still download it;
admin (moderation) can delete ANY user's message for everyone, even in
a conversation they are not a participant of (deleted_by /
deleted_by_name recorded); admin Media Library items include
conversation_id; large media uploads stream to disk under a 1 GB cap
(120 MB upload succeeds, over-limit rejected on a small-cap server).
"""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import websockets

PORT = os.environ.get("PORT", "8007")
BASE = f"http://127.0.0.1:{PORT}"
results = []

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        # ---- setup: A (admin), B, C (non-participant) ----
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

        async def history(h):
            r = await c.get(f"/api/conversations/{conv_id}/messages", headers=h)
            return r.json() if r.status_code == 200 else []

        async def conv_entry(h):
            r = await c.get("/api/conversations", headers=h)
            lst = r.json() if r.status_code == 200 else []
            return next((x for x in lst if x["id"] == conv_id), None)

        async def album(h):
            r = await c.get(f"/api/conversations/{conv_id}/media", headers=h)
            return r.json() if r.status_code == 200 else []

        # ---- delete-for-me: text ----
        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "hello one"}, headers=h_a)
        m1 = r.json() if r.status_code == 201 else {}
        m1_id = m1.get("id")
        check("A sends m1", r.status_code == 201 and m1_id is not None, str(r.status_code))

        entry_b = await conv_entry(h_b)
        check("B unread=1 before delete", entry_b is not None and entry_b.get("unread_count") == 1,
              str(entry_b))

        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m1_id}",
                           params={"mode": "me"}, headers=h_b)
        check("B delete-for-me m1 200", r.status_code == 200, str(r.status_code) + " " + r.text[:150])
        d = r.json() if r.status_code == 200 else {}
        check("delete-for-me response shape",
              d.get("ok") is True and d.get("message_id") == m1_id
              and d.get("mode") == "me" and d.get("message") is None, str(d))

        hist_b = await history(h_b)
        check("B history hides m1", all(m["id"] != m1_id for m in hist_b), str(hist_b))
        hist_a = await history(h_a)
        m1_a = next((m for m in hist_a if m["id"] == m1_id), None)
        check("A history still shows m1", m1_a is not None and m1_a.get("content") == "hello one",
              str(m1_a))
        entry_b = await conv_entry(h_b)
        check("B preview falls back (last_message null, unread 0)",
              entry_b is not None and entry_b.get("last_message") is None
              and entry_b.get("unread_count") == 0, str(entry_b))
        entry_a = await conv_entry(h_a)
        check("A preview still shows m1",
              entry_a is not None
              and entry_a.get("last_message", {}).get("content") == "hello one", str(entry_a))

        # idempotent delete-for-me
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m1_id}",
                           params={"mode": "me"}, headers=h_b)
        check("delete-for-me again 200 (idempotent)", r.status_code == 200, str(r.status_code))

        # ---- new message carries deleted=false ----
        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "hello two"}, headers=h_a)
        m2 = r.json() if r.status_code == 201 else {}
        m2_id = m2.get("id")
        check("new message deleted=false",
              r.status_code == 201 and m2.get("deleted") is False
              and m2.get("deleted_for_everyone") is False, str(m2))

        # ---- delete-for-me: media ----
        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("pic.png", PNG, "image/png")}, headers=h_b)
        m3 = r.json() if r.status_code == 201 else {}
        m3_id = m3.get("id")
        check("B uploads photo m3", r.status_code == 201 and m3_id is not None,
              str(r.status_code) + " " + r.text[:150])
        r = await c.get(f"/api/media/{m3_id}", headers=h_a)
        check("A downloads m3 before delete (bytes match)",
              r.status_code == 200 and r.content == PNG, str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m3_id}",
                           params={"mode": "me"}, headers=h_b)
        check("B delete-for-me m3 200", r.status_code == 200, str(r.status_code))
        alb_b = await album(h_b)
        check("B album hides m3", all(m["id"] != m3_id for m in alb_b), str(alb_b))
        r = await c.get(f"/api/media/{m3_id}", headers=h_b)
        check("B download m3 after delete-for-me -> 404", r.status_code == 404, str(r.status_code))
        hist_b = await history(h_b)
        check("B history hides m3", all(m["id"] != m3_id for m in hist_b), str(hist_b))
        alb_a = await album(h_a)
        check("A album still has m3", any(m["id"] == m3_id for m in alb_a), str(alb_a))
        r = await c.get(f"/api/media/{m3_id}", headers=h_a)
        check("A download m3 still works (bytes match)",
              r.status_code == 200 and r.content == PNG, str(r.status_code))
        hist_a = await history(h_a)
        m3_a = next((m for m in hist_a if m["id"] == m3_id), None)
        check("A history still shows m3 media",
              m3_a is not None and m3_a.get("media_url") == f"/api/media/{m3_id}", str(m3_a))

        # ---- delete-for-everyone: validation errors ----
        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "hello three"}, headers=h_a)
        m4 = r.json() if r.status_code == 201 else {}
        m4_id = m4.get("id")
        check("A sends m4", r.status_code == 201 and m4_id is not None, str(r.status_code))

        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m4_id}",
                           params={"mode": "everyone"}, headers=h_b)
        check("non-sender delete-for-everyone -> 403", r.status_code == 403, str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m4_id}",
                           params={"mode": "bogus"}, headers=h_b)
        check("invalid mode -> 400", r.status_code == 400, str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m4_id}", headers=h_b)
        check("missing mode -> 422", r.status_code == 422, str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m4_id}",
                           params={"mode": "me"}, headers=h_c)
        check("non-participant delete -> 403", r.status_code == 403, str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_id}/messages/999999",
                           params={"mode": "me"}, headers=h_b)
        check("message not found -> 404", r.status_code == 404, str(r.status_code))
        r = await c.delete(f"/api/conversations/999999/messages/{m4_id}",
                           params={"mode": "me"}, headers=h_b)
        check("conversation not found -> 404", r.status_code == 404, str(r.status_code))
        # m4 must be untouched by the failed attempts above
        hist_b = await history(h_b)
        m4_b = next((m for m in hist_b if m["id"] == m4_id), None)
        check("m4 intact after failed deletes",
              m4_b is not None and m4_b.get("content") == "hello three", str(m4_b))

        # ---- delete-for-everyone: sender ----
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m4_id}",
                           params={"mode": "everyone"}, headers=h_a)
        check("A delete-for-everyone m4 200", r.status_code == 200, str(r.status_code) + " " + r.text[:200])
        d = r.json() if r.status_code == 200 else {}
        md = d.get("message") or {}
        check("delete-for-everyone response is placeholder",
              d.get("ok") is True and d.get("mode") == "everyone"
              and md.get("id") == m4_id and md.get("content") is None
              and md.get("media_url") is None and md.get("deleted") is True
              and md.get("deleted_for_everyone") is True
              and md.get("kind") == "text", str(d))
        for label, h in (("A", h_a), ("B", h_b)):
            hist = await history(h)
            mm = next((m for m in hist if m["id"] == m4_id), None)
            check(f"{label} history shows m4 as placeholder",
                  mm is not None and mm.get("content") is None and mm.get("deleted") is True
                  and mm.get("deleted_for_everyone") is True, str(mm))
            entry = await conv_entry(h)
            check(f"{label} preview shows m4 deleted (content null)",
                  entry is not None and entry.get("last_message", {}).get("id") == m4_id
                  and entry.get("last_message", {}).get("content") is None
                  and entry.get("last_message", {}).get("deleted") is True, str(entry))
        entry_b = await conv_entry(h_b)
        check("B unread ignores deleted m4 (only m2 unread)",
              entry_b is not None and entry_b.get("unread_count") == 1, str(entry_b))

        # delete-for-me on an already deleted-for-everyone message
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m4_id}",
                           params={"mode": "me"}, headers=h_b)
        check("delete-for-me on deleted-for-everyone m4 200", r.status_code == 200,
              str(r.status_code))
        # idempotent delete-for-everyone
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m4_id}",
                           params={"mode": "everyone"}, headers=h_a)
        check("delete-for-everyone again 200 (idempotent)", r.status_code == 200,
              str(r.status_code))

        # ---- delete-for-everyone: media (admin sender) ----
        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("pic2.png", PNG, "image/png")}, headers=h_a)
        m5 = r.json() if r.status_code == 201 else {}
        m5_id = m5.get("id")
        check("A uploads photo m5", r.status_code == 201 and m5_id is not None, str(r.status_code))
        r = await c.get(f"/api/media/{m5_id}", headers=h_b)
        check("B downloads m5 before delete", r.status_code == 200 and r.content == PNG,
              str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m5_id}",
                           params={"mode": "everyone"}, headers=h_a)
        d = r.json() if r.status_code == 200 else {}
        check("A delete-for-everyone m5 placeholder (media stripped)",
              r.status_code == 200 and (d.get("message") or {}).get("media_url") is None
              and (d.get("message") or {}).get("deleted") is True, str(d))
        alb_a = await album(h_a)
        alb_b = await album(h_b)
        check("album hides m5 for both",
              all(m["id"] != m5_id for m in alb_a) and all(m["id"] != m5_id for m in alb_b),
              str(alb_a) + " " + str(alb_b))
        r = await c.get(f"/api/media/{m5_id}", headers=h_b)
        check("B download m5 after delete-for-everyone -> 404", r.status_code == 404,
              str(r.status_code))
        r = await c.get(f"/api/media/{m5_id}", headers=h_a)
        check("admin (A) download m5 still works", r.status_code == 200 and r.content == PNG,
              str(r.status_code))

        # ---- delete-for-everyone: media (non-admin sender) ----
        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("pic3.png", PNG, "image/png")}, headers=h_b)
        m6 = r.json() if r.status_code == 201 else {}
        m6_id = m6.get("id")
        check("B uploads photo m6", r.status_code == 201 and m6_id is not None, str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m6_id}",
                           params={"mode": "everyone"}, headers=h_b)
        check("B delete-for-everyone m6 200", r.status_code == 200, str(r.status_code))
        r = await c.get(f"/api/media/{m6_id}", headers=h_b)
        check("B download m6 after own delete-for-everyone -> 404", r.status_code == 404,
              str(r.status_code))
        r = await c.get(f"/api/media/{m6_id}", headers=h_a)
        check("admin download m6 still works", r.status_code == 200 and r.content == PNG,
              str(r.status_code))

    # ---- WebSocket: live message_deleted + call line cannot be deleted ----
    ws_a = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_a}")
    ws_b = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_b}")
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
            h_a = {"Authorization": f"Bearer {token_a}"}
            h_b = {"Authorization": f"Bearer {token_b}"}
            r = await c.post(f"/api/conversations/{conv_id}/messages",
                             json={"kind": "text", "content": "live delete me"}, headers=h_a)
            m7 = r.json() if r.status_code == 201 else {}
            m7_id = m7.get("id")
            check("A sends m7 for WS test", r.status_code == 201 and m7_id is not None,
                  str(r.status_code))
            # Drain B's socket of the "message" event for m7 (and presence).
            for _ in range(5):
                raw = await asyncio.wait_for(ws_b.recv(), timeout=5)
                d = json.loads(raw)
                if d.get("type") == "message" and (d.get("message") or {}).get("id") == m7_id:
                    break
            r = await c.delete(f"/api/conversations/{conv_id}/messages/{m7_id}",
                               params={"mode": "everyone"}, headers=h_a)
            check("A delete-for-everyone m7 200", r.status_code == 200, str(r.status_code))
            got = None
            for _ in range(5):
                raw = await asyncio.wait_for(ws_b.recv(), timeout=5)
                d = json.loads(raw)
                if d.get("type") == "message_deleted":
                    got = d
                    break
            check("WS B receives message_deleted",
                  got is not None and got.get("conversation_id") == conv_id
                  and got.get("message_id") == m7_id and got.get("mode") == "everyone"
                  and (got.get("message") or {}).get("id") == m7_id
                  and (got.get("message") or {}).get("content") is None
                  and (got.get("message") or {}).get("deleted") is True, str(got))

            # Call-history line: B goes offline, A invites -> missed call stored.
            await ws_b.close()
            await asyncio.sleep(0.2)
            await ws_a.send(json.dumps(
                {"type": "call:invite", "conversation_id": conv_id, "call_type": "audio"}))
            for _ in range(5):
                raw = await asyncio.wait_for(ws_a.recv(), timeout=5)
                d = json.loads(raw)
                if d.get("type") == "call:unavailable":
                    break
            r = await c.get(f"/api/conversations/{conv_id}/messages", headers=h_a)
            hist = r.json() if r.status_code == 200 else []
            call_msg = next((m for m in hist if m.get("kind") == "call"), None)
            check("missed-call line stored", call_msg is not None, str(hist))
            if call_msg is not None:
                r = await c.delete(
                    f"/api/conversations/{conv_id}/messages/{call_msg['id']}",
                    params={"mode": "me"}, headers=h_a)
                check("deleting call-history line -> 400", r.status_code == 400,
                      str(r.status_code))
                r = await c.delete(
                    f"/api/conversations/{conv_id}/messages/{call_msg['id']}",
                    params={"mode": "everyone"}, headers=h_a)
                check("deleting call-history line for everyone -> 400", r.status_code == 400,
                      str(r.status_code))
    finally:
        try:
            await ws_a.close()
        except Exception:
            pass
        try:
            await ws_b.close()
        except Exception:
            pass

    # ---- Admin still sees everything ----
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        h_a = {"Authorization": f"Bearer {token_a}"}
        r = await c.get(f"/api/admin/conversations/{conv_id}/messages", headers=h_a)
        adm = r.json() if r.status_code == 200 else []
        check("admin conversation messages 200", r.status_code == 200, str(r.status_code))
        m1_adm = next((m for m in adm if m["id"] == m1_id), None)
        check("admin sees m1 full content + deleted_for=[B]",
              m1_adm is not None and m1_adm.get("content") == "hello one"
              and id_b in (m1_adm.get("deleted_for") or [])
              and m1_adm.get("deleted_for_everyone") is False, str(m1_adm))
        m4_adm = next((m for m in adm if m["id"] == m4_id), None)
        check("admin sees m4 full content + deleted flags",
              m4_adm is not None and m4_adm.get("content") == "hello three"
              and m4_adm.get("deleted") is True
              and m4_adm.get("deleted_for_everyone") is True
              and id_b in (m4_adm.get("deleted_for") or []), str(m4_adm))
        m5_adm = next((m for m in adm if m["id"] == m5_id), None)
        check("admin sees m5 full media + deleted",
              m5_adm is not None and m5_adm.get("media_url") == f"/api/media/{m5_id}"
              and m5_adm.get("deleted") is True, str(m5_adm))
        m3_adm = next((m for m in adm if m["id"] == m3_id), None)
        check("admin sees m3 (delete-for-me) full media, not everyone-deleted",
              m3_adm is not None and m3_adm.get("media_url") == f"/api/media/{m3_id}"
              and m3_adm.get("deleted") is False
              and id_b in (m3_adm.get("deleted_for") or []), str(m3_adm))
        r = await c.get("/api/admin/media", headers=h_a)
        lib = r.json() if r.status_code == 200 else []
        m5_lib = next((m for m in lib if m["id"] == m5_id), None)
        m3_lib = next((m for m in lib if m["id"] == m3_id), None)
        check("admin Media Library: m5 deleted=true",
              m5_lib is not None and m5_lib.get("deleted") is True, str(m5_lib))
        check("admin Media Library: m3 deleted=false (delete-for-me only)",
              m3_lib is not None and m3_lib.get("deleted") is False, str(m3_lib))
        r = await c.get(f"/api/media/{m5_id}", headers=h_a)
        check("admin download of deleted m5 works (bytes match)",
              r.status_code == 200 and r.content == PNG, str(r.status_code))

    # ---- Admin deletes ANOTHER user's message (moderation) ----
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        h_a = {"Authorization": f"Bearer {token_a}"}
        h_b = {"Authorization": f"Bearer {token_b}"}
        h_c = {"Authorization": f"Bearer {token_c}"}

        # B sends a text; A (admin, not the sender) deletes it for everyone.
        r = await c.post(f"/api/conversations/{conv_id}/messages",
                         json={"kind": "text", "content": "bob says hi"}, headers=h_b)
        m8 = r.json() if r.status_code == 201 else {}
        m8_id = m8.get("id")
        check("B sends m8", r.status_code == 201 and m8_id is not None, str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m8_id}",
                           params={"mode": "everyone"}, headers=h_a)
        d = r.json() if r.status_code == 200 else {}
        check("admin deletes B's text m8 for everyone 200",
              r.status_code == 200 and d.get("ok") is True and d.get("mode") == "everyone"
              and (d.get("message") or {}).get("content") is None
              and (d.get("message") or {}).get("deleted") is True,
              str(r.status_code) + " " + r.text[:200])
        for label, h in (("A", h_a), ("B", h_b)):
            hist = await history(h)
            mm = next((m for m in hist if m["id"] == m8_id), None)
            check(f"{label} history shows m8 placeholder, no deleted_by (admin delete)",
                  mm is not None and mm.get("content") is None and mm.get("deleted") is True
                  and "deleted_by" not in mm, str(mm))
            entry = await conv_entry(h)
            check(f"{label} preview shows m8 deleted (admin delete)",
                  entry is not None and entry.get("last_message", {}).get("id") == m8_id
                  and entry.get("last_message", {}).get("content") is None
                  and entry.get("last_message", {}).get("deleted") is True, str(entry))

        # B uploads a photo; admin deletes it for everyone.
        r = await c.post(f"/api/conversations/{conv_id}/upload",
                         files={"file": ("pic4.png", PNG, "image/png")}, headers=h_b)
        m9 = r.json() if r.status_code == 201 else {}
        m9_id = m9.get("id")
        check("B uploads photo m9", r.status_code == 201 and m9_id is not None,
              str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m9_id}",
                           params={"mode": "everyone"}, headers=h_a)
        check("admin deletes B's photo m9 for everyone 200", r.status_code == 200,
              str(r.status_code))
        alb_a = await album(h_a)
        alb_b = await album(h_b)
        check("album hides m9 for both (admin delete)",
              all(m["id"] != m9_id for m in alb_a) and all(m["id"] != m9_id for m in alb_b),
              str(alb_a) + " " + str(alb_b))
        r = await c.get(f"/api/media/{m9_id}", headers=h_b)
        check("B download m9 after admin delete -> 404", r.status_code == 404,
              str(r.status_code))
        r = await c.get(f"/api/media/{m9_id}", headers=h_a)
        check("admin download m9 still works (bytes match)",
              r.status_code == 200 and r.content == PNG, str(r.status_code))

        # Admin panel: full content retained + deleted_by / deleted_by_name.
        r = await c.get(f"/api/admin/conversations/{conv_id}/messages", headers=h_a)
        adm = r.json() if r.status_code == 200 else []
        m8_adm = next((m for m in adm if m["id"] == m8_id), None)
        check("admin sees m8 full content + deleted_by=A + name Alice",
              m8_adm is not None and m8_adm.get("content") == "bob says hi"
              and m8_adm.get("deleted") is True
              and m8_adm.get("deleted_by") == id_a
              and m8_adm.get("deleted_by_name") == "Alice", str(m8_adm))
        m9_adm = next((m for m in adm if m["id"] == m9_id), None)
        check("admin sees m9 full media + deleted_by=A + name Alice",
              m9_adm is not None and m9_adm.get("media_url") == f"/api/media/{m9_id}"
              and m9_adm.get("deleted") is True
              and m9_adm.get("deleted_by") == id_a
              and m9_adm.get("deleted_by_name") == "Alice", str(m9_adm))
        m4_adm = next((m for m in adm if m["id"] == m4_id), None)
        check("sender delete (m4) records deleted_by=sender A",
              m4_adm is not None and m4_adm.get("deleted_by") == id_a
              and m4_adm.get("deleted_by_name") == "Alice", str(m4_adm))
        m6_adm = next((m for m in adm if m["id"] == m6_id), None)
        check("sender delete (m6, by B) records deleted_by=B",
              m6_adm is not None and m6_adm.get("deleted_by") == id_b
              and m6_adm.get("deleted_by_name") == "Bob", str(m6_adm))
        m1_adm = next((m for m in adm if m["id"] == m1_id), None)
        check("delete-for-me only (m1) has deleted_by null",
              m1_adm is not None and m1_adm.get("deleted_by") is None
              and m1_adm.get("deleted_by_name") is None, str(m1_adm))
        m2_adm = next((m for m in adm if m["id"] == m2_id), None)
        check("never-deleted (m2) has deleted_by null",
              m2_adm is not None and m2_adm.get("deleted_by") is None
              and m2_adm.get("deleted_by_name") is None, str(m2_adm))
        r = await c.get("/api/admin/media", headers=h_a)
        lib = r.json() if r.status_code == 200 else []
        m9_lib = next((m for m in lib if m["id"] == m9_id), None)
        check("admin Media Library: m9 deleted=true + conversation_id present",
              m9_lib is not None and m9_lib.get("deleted") is True
              and m9_lib.get("conversation_id") == conv_id, str(m9_lib))

        # ---- Admin deletes in a conversation they are NOT a participant of ----
        r = await c.post("/api/conversations", json={"user_id": id_c}, headers=h_b)
        conv_bc = r.json().get("id") if r.status_code in (200, 201) else None
        check("B creates conv with C", conv_bc is not None, str(r.status_code))
        r = await c.post(f"/api/conversations/{conv_bc}/messages",
                         json={"kind": "text", "content": "bc secret"}, headers=h_b)
        m10 = r.json() if r.status_code == 201 else {}
        m10_id = m10.get("id")
        check("B sends m10 in B-C conv", r.status_code == 201 and m10_id is not None,
              str(r.status_code))

        async def history_bc(h):
            r2 = await c.get(f"/api/conversations/{conv_bc}/messages", headers=h)
            return r2.json() if r2.status_code == 200 else []

        r = await c.delete(f"/api/conversations/{conv_bc}/messages/{m10_id}",
                           params={"mode": "everyone"}, headers=h_a)
        d = r.json() if r.status_code == 200 else {}
        check("admin (non-participant) deletes m10 for everyone 200",
              r.status_code == 200 and d.get("ok") is True
              and (d.get("message") or {}).get("deleted") is True
              and (d.get("message") or {}).get("content") is None,
              str(r.status_code) + " " + r.text[:200])
        hist_b = await history_bc(h_b)
        check("B history shows m10 placeholder (admin non-participant delete)",
              any(m["id"] == m10_id and m.get("content") is None and m.get("deleted") is True
                  for m in hist_b), str(hist_b))
        hist_c = await history_bc(h_c)
        check("C history shows m10 placeholder (admin non-participant delete)",
              any(m["id"] == m10_id and m.get("content") is None and m.get("deleted") is True
                  for m in hist_c), str(hist_c))
        r = await c.get(f"/api/admin/conversations/{conv_bc}/messages", headers=h_a)
        adm_bc = r.json() if r.status_code == 200 else []
        m10_adm = next((m for m in adm_bc if m["id"] == m10_id), None)
        check("admin sees m10 full content + deleted_by=A",
              m10_adm is not None and m10_adm.get("content") == "bc secret"
              and m10_adm.get("deleted_by") == id_a
              and m10_adm.get("deleted_by_name") == "Alice", str(m10_adm))

        # Admin mode=me on a conversation they are not a participant of -> 403.
        r = await c.post(f"/api/conversations/{conv_bc}/messages",
                         json={"kind": "text", "content": "bc second"}, headers=h_c)
        m11 = r.json() if r.status_code == 201 else {}
        m11_id = m11.get("id")
        check("C sends m11 in B-C conv", r.status_code == 201 and m11_id is not None,
              str(r.status_code))
        r = await c.delete(f"/api/conversations/{conv_bc}/messages/{m11_id}",
                           params={"mode": "me"}, headers=h_a)
        check("admin mode=me on non-participant conv -> 403", r.status_code == 403,
              str(r.status_code))
        # Non-participant non-admin, mode=everyone, on the A-B conversation -> 403.
        r = await c.delete(f"/api/conversations/{conv_id}/messages/{m2_id}",
                           params={"mode": "everyone"}, headers=h_c)
        check("non-participant non-admin mode=everyone -> 403", r.status_code == 403,
              str(r.status_code))
        # Admin is still not a participant of B-C for normal chat reads.
        r = await c.get(f"/api/conversations/{conv_bc}/messages", headers=h_a)
        check("admin GET B-C messages -> 403 (not a participant)", r.status_code == 403,
              str(r.status_code))
        # m11 untouched by the failed admin mode=me attempt.
        hist_c = await history_bc(h_c)
        m11_c = next((m for m in hist_c if m["id"] == m11_id), None)
        check("m11 intact after failed admin mode=me",
              m11_c is not None and m11_c.get("content") == "bc second", str(m11_c))

    # ---- WebSocket: admin (non-participant) delete reaches the participants ----
    ws_b = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_b}")
    ws_c = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_c}")
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
            h_a = {"Authorization": f"Bearer {token_a}"}
            h_c = {"Authorization": f"Bearer {token_c}"}
            r = await c.post(f"/api/conversations/{conv_bc}/messages",
                             json={"kind": "text", "content": "bc live"}, headers=h_c)
            m12 = r.json() if r.status_code == 201 else {}
            m12_id = m12.get("id")
            check("C sends m12 in B-C conv for WS test",
                  r.status_code == 201 and m12_id is not None, str(r.status_code))
            # Drain C's socket of the "message" event for m12 (and presence).
            for _ in range(5):
                raw = await asyncio.wait_for(ws_c.recv(), timeout=5)
                d = json.loads(raw)
                if d.get("type") == "message" and (d.get("message") or {}).get("id") == m12_id:
                    break
            r = await c.delete(f"/api/conversations/{conv_bc}/messages/{m12_id}",
                               params={"mode": "everyone"}, headers=h_a)
            check("admin (non-participant) deletes m12 200", r.status_code == 200,
                  str(r.status_code))
            for label, ws in (("B", ws_b), ("C", ws_c)):
                got = None
                for _ in range(5):
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    d = json.loads(raw)
                    if d.get("type") == "message_deleted":
                        got = d
                        break
                check(f"WS {label} receives message_deleted for admin delete",
                      got is not None and got.get("conversation_id") == conv_bc
                      and got.get("message_id") == m12_id and got.get("mode") == "everyone"
                      and (got.get("message") or {}).get("id") == m12_id
                      and (got.get("message") or {}).get("content") is None
                      and (got.get("message") or {}).get("deleted") is True, str(got))
    finally:
        try:
            await ws_b.close()
        except Exception:
            pass
        try:
            await ws_c.close()
        except Exception:
            pass

    # ---- Large media upload: streaming + 1 GB cap ----
    # A ~120 MB video is generated on disk in chunks (never held in
    # memory), uploaded via a streaming multipart body, and must
    # succeed with media_size == the counted bytes; its download is
    # streamed back and counted, never loaded whole.
    BIG_MB = 120
    BIG = BIG_MB * 1024 * 1024
    big_path = Path(tempfile.gettempdir()) / "vibe-big-upload.mp4"
    try:
        with big_path.open("wb") as f:
            for _ in range(BIG_MB):
                f.write(b"\0" * (1024 * 1024))
        async with httpx.AsyncClient(base_url=BASE, timeout=120, trust_env=False) as c:
            h_a = {"Authorization": f"Bearer {token_a}"}
            with big_path.open("rb") as fh:
                r = await c.post(f"/api/conversations/{conv_id}/upload",
                                 files={"file": ("big.mp4", fh, "video/mp4")},
                                 data={"kind": "video"}, headers=h_a)
            big = r.json() if r.status_code == 201 else {}
            big_id = big.get("id")
            check("120 MB video upload 201 (streamed)",
                  r.status_code == 201 and big_id is not None,
                  str(r.status_code) + " " + r.text[:200])
            check("120 MB upload media_size matches counted bytes",
                  big.get("media_size") == BIG and big.get("kind") == "video", str(big))
            # Streamed download: count bytes without holding them.
            total_dl = 0
            dl_ok = False
            async with c.stream("GET", f"/api/media/{big_id}", headers=h_a) as resp:
                dl_ok = resp.status_code == 200
                async for chunk in resp.aiter_bytes(1024 * 1024):
                    total_dl += len(chunk)
            check("streamed download of 120 MB video counts full size",
                  dl_ok and total_dl == BIG, f"status_ok={dl_ok} bytes={total_dl}")
    finally:
        big_path.unlink(missing_ok=True)

    # ---- Over-limit guard, exercised with a small cap ----
    # A real 1 GB upload is NOT sent. Instead a second server is
    # started with MAX_MEDIA_BYTES=5 MB: a 6 MB upload must 400 with a
    # clear error, leave no partial file and create no message, while
    # a 4 MB upload under the cap still succeeds. This exercises the
    # exact same chunk-counting/abort code path as the 1 GB cap.
    small_port = int(PORT) + 1
    small_dir = Path(tempfile.mkdtemp(prefix="vibe-smallcap-"))
    small_env = dict(os.environ)
    small_env["DATA_DIR"] = str(small_dir / "data")
    small_env["PORT"] = str(small_port)
    small_env["MAX_MEDIA_BYTES"] = str(5 * 1024 * 1024)
    backend_dir = Path(__file__).resolve().parent
    small_proc = subprocess.Popen(
        [sys.executable, "run.py"], cwd=str(backend_dir), env=small_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        small_base = f"http://127.0.0.1:{small_port}"
        async with httpx.AsyncClient(base_url=small_base, timeout=60, trust_env=False) as c:
            up = False
            for _ in range(50):
                try:
                    r = await c.get("/api/health")
                    if r.status_code == 200:
                        up = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.2)
            check("small-cap server boots", up, "")
            r = await c.post("/api/auth/register", json={
                "display_name": "Dave", "email": "dave@example.com", "password": "secret123"})
            d1 = r.json() if r.status_code == 201 else {}
            h_d = {"Authorization": f"Bearer {d1.get('access_token', '')}"}
            r = await c.post("/api/auth/register", json={
                "display_name": "Erin", "email": "erin@example.com", "password": "secret123"})
            d2 = r.json() if r.status_code == 201 else {}
            id_e = d2.get("id")
            r = await c.post("/api/conversations", json={"user_id": id_e}, headers=h_d)
            conv_s = r.json().get("id") if r.status_code in (200, 201) else None
            check("small-cap server: conv created", conv_s is not None, str(r.status_code))
            over_path = small_dir / "over.mp4"
            with over_path.open("wb") as f:
                for _ in range(6):
                    f.write(b"\0" * (1024 * 1024))
            with over_path.open("rb") as fh:
                r = await c.post(f"/api/conversations/{conv_s}/upload",
                                 files={"file": ("over.mp4", fh, "video/mp4")},
                                 data={"kind": "video"}, headers=h_d)
            check("6 MB upload over 5 MB cap -> 400 with clear error",
                  r.status_code == 400 and "too large" in r.text.lower()
                  and "5 MB" in r.text, str(r.status_code) + " " + r.text[:200])
            r = await c.get(f"/api/conversations/{conv_s}/messages", headers=h_d)
            hist_s = r.json() if r.status_code == 200 else []
            check("over-limit upload creates no message", hist_s == [], str(hist_s))
            leftovers = [p for p in (small_dir / "data" / "uploads").rglob("*")
                         if p.is_file()]
            check("over-limit upload leaves no partial file",
                  leftovers == [], str(leftovers))
            under_path = small_dir / "under.mp4"
            with under_path.open("wb") as f:
                for _ in range(4):
                    f.write(b"\0" * (1024 * 1024))
            with under_path.open("rb") as fh:
                r = await c.post(f"/api/conversations/{conv_s}/upload",
                                 files={"file": ("under.mp4", fh, "video/mp4")},
                                 data={"kind": "video"}, headers=h_d)
            u = r.json() if r.status_code == 201 else {}
            check("4 MB upload under 5 MB cap succeeds",
                  r.status_code == 201 and u.get("media_size") == 4 * 1024 * 1024,
                  str(r.status_code) + " " + r.text[:200])
    finally:
        small_proc.terminate()
        try:
            small_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            small_proc.kill()
        shutil.rmtree(small_dir, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


asyncio.run(main())
