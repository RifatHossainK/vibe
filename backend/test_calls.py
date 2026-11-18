"""End-to-end verification for Vibe Phase 2 backend: groups, call
signaling over WS, call-history messages, and ICE config.

Run against a FRESH database (fresh DATA_DIR). BASE is read from the PORT
env var (default 8006), same style as test_e2e.py / test_admin.py.
Start the server first, e.g.:
    DATA_DIR=/tmp/vibe-calls-test/data PORT=8006 python run.py
then:
    python test_calls.py
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import httpx
import websockets

PORT = os.environ.get("PORT", "8006")
BASE = f"http://127.0.0.1:{PORT}"
results = []

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def recv_of(ws, want_type, timeout=5):
    """Read WS messages until one of type `want_type` arrives (skipping
    presence/typing/etc.), or return None on timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return None
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        try:
            d = json.loads(raw)
        except Exception:
            continue
        if d.get("type") == want_type:
            return d


async def collect(ws, seconds):
    """Collect every WS message arriving within `seconds` (for
    'gets nothing' assertions)."""
    out = []
    deadline = asyncio.get_event_loop().time() + seconds
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return out
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            out.append(json.loads(raw))
        except asyncio.TimeoutError:
            return out


async def call_history(c, token, conv_id):
    r = await c.get(f"/api/conversations/{conv_id}/messages",
                    headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        return []
    return [m for m in r.json() if m.get("kind") == "call"]


async def reg(c, name, email):
    r = await c.post("/api/auth/register", json={
        "display_name": name, "email": email, "password": "secret123"})
    assert r.status_code == 201, f"register {name}: {r.status_code} {r.text[:200]}"
    j = r.json()
    return j["id"], j["access_token"]


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        r = await c.get("/api/health")
        check("health", r.status_code == 200, str(r.status_code))

        id_a, token_a = await reg(c, "Alice", "alice@example.com")
        id_b, token_b = await reg(c, "Bob", "bob@example.com")
        id_c, token_c = await reg(c, "Carol", "carol@example.com")
        id_d, token_d = await reg(c, "Dave", "dave@example.com")
        check("register A-D", True, f"ids={id_a},{id_b},{id_c},{id_d}")

        # ---- 1:1 conversation A-B (used by the call tests) ----
        r = await c.post("/api/conversations", json={"user_id": id_b},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("create 1:1 A-B", r.status_code == 201, str(r.status_code))
        conv_ab = r.json().get("id")
        check("1:1 shape: is_group False, name None, other_user B",
              r.json().get("is_group") is False and r.json().get("name") is None
              and r.json().get("other_user", {}).get("id") == id_b
              and {p["id"] for p in r.json().get("participants", [])} == {id_a, id_b},
              str(r.json()))

        # ---- Groups ----
        # Duplicate user_ids deduped, self excluded, creator auto-added.
        r = await c.post("/api/conversations/groups",
                         json={"name": "Squad", "user_ids": [id_b, id_c, id_b, id_a]},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("create group 201", r.status_code == 201, str(r.status_code) + " " + r.text[:250])
        grp = r.json() if r.status_code == 201 else {}
        grp_id = grp.get("id")
        check("group shape: is_group True, name Squad, other_user None, 3 participants",
              grp.get("is_group") is True and grp.get("name") == "Squad"
              and grp.get("other_user") is None
              and {p["id"] for p in grp.get("participants", [])} == {id_a, id_b, id_c},
              str(grp))
        # Bad user -> 400; empty name -> 400; no others -> 400; >49 -> 400.
        r = await c.post("/api/conversations/groups",
                         json={"name": "Bad", "user_ids": [id_b, 99999]},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("group bad user 400", r.status_code == 400, str(r.status_code))
        r = await c.post("/api/conversations/groups",
                         json={"name": "   ", "user_ids": [id_b]},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("group blank name 400", r.status_code == 400, str(r.status_code))
        r = await c.post("/api/conversations/groups",
                         json={"name": "Solo", "user_ids": [id_a]},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("group only-self 400", r.status_code == 400, str(r.status_code))
        r = await c.post("/api/conversations/groups",
                         json={"name": "Big", "user_ids": list(range(100, 150))},
                         headers={"Authorization": f"Bearer {token_a}"})
        check("group 50 others 400", r.status_code == 400, str(r.status_code))

        # GET single conversation.
        r = await c.get(f"/api/conversations/{grp_id}",
                        headers={"Authorization": f"Bearer {token_b}"})
        check("GET group as member 200", r.status_code == 200 and r.json().get("is_group") is True,
              str(r.status_code))
        r = await c.get(f"/api/conversations/{grp_id}",
                        headers={"Authorization": f"Bearer {token_d}"})
        check("GET group as non-member 403", r.status_code == 403, str(r.status_code))
        r = await c.get("/api/conversations/99999",
                        headers={"Authorization": f"Bearer {token_a}"})
        check("GET missing conversation 404", r.status_code == 404, str(r.status_code))

    # ---- WebSocket: group message reaches ALL members, nobody else ----
    ws_a = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_a}")
    ws_b = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_b}")
    ws_c = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_c}")
    ws_d = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_d}")
    try:
        # Drain presence broadcasts from the connects.
        for ws in (ws_a, ws_b, ws_c, ws_d):
            await collect(ws, 0.3)

        await ws_a.send(json.dumps({"type": "message", "conversation_id": grp_id, "content": "hey squad"}))
        got_b = await recv_of(ws_b, "message")
        got_c = await recv_of(ws_c, "message")
        got_d = await collect(ws_d, 0.8)
        check("group WS message reaches B",
              got_b is not None and got_b["message"]["content"] == "hey squad", str(got_b))
        check("group WS message reaches C",
              got_c is not None and got_c["message"]["content"] == "hey squad", str(got_c))
        check("group WS message does NOT reach non-member D",
              got_d == [], str(got_d))

        async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
            r = await c.get(f"/api/conversations/{grp_id}/messages",
                            headers={"Authorization": f"Bearer {token_d}"})
            check("non-member GET group messages 403", r.status_code == 403, str(r.status_code))

            # ---- Membership changes ----
            r = await c.patch(f"/api/conversations/{grp_id}/members",
                              json={"add": [id_d]},
                              headers={"Authorization": f"Bearer {token_a}"})
            check("add member D",
                  r.status_code == 200
                  and {p["id"] for p in r.json().get("participants", [])} == {id_a, id_b, id_c, id_d},
                  r.text[:200])
            r = await c.patch(f"/api/conversations/{grp_id}/members",
                              json={"add": [99999]},
                              headers={"Authorization": f"Bearer {token_a}"})
            check("add nonexistent user 400", r.status_code == 400, str(r.status_code))
            r = await c.patch(f"/api/conversations/{grp_id}/members",
                              json={"add": [id_d]},
                              headers={"Authorization": f"Bearer {token_a}"})
            check("add already-member ignored",
                  r.status_code == 200 and len(r.json().get("participants", [])) == 4,
                  r.text[:200])
            r = await c.patch(f"/api/conversations/{grp_id}/members",
                              json={"remove": [id_c]},
                              headers={"Authorization": f"Bearer {token_a}"})
            check("remove member C",
                  r.status_code == 200
                  and {p["id"] for p in r.json().get("participants", [])} == {id_a, id_b, id_d},
                  r.text[:200])
            r = await c.get(f"/api/conversations/{grp_id}/messages",
                            headers={"Authorization": f"Bearer {token_c}"})
            check("removed C GET group messages 403", r.status_code == 403, str(r.status_code))
            # Leave: B removes self.
            r = await c.patch(f"/api/conversations/{grp_id}/members",
                              json={"remove": [id_b]},
                              headers={"Authorization": f"Bearer {token_b}"})
            check("leave (remove self)",
                  r.status_code == 200
                  and {p["id"] for p in r.json().get("participants", [])} == {id_a, id_d},
                  r.text[:200])
            r = await c.get(f"/api/conversations/{grp_id}",
                            headers={"Authorization": f"Bearer {token_b}"})
            check("left member GET group 403", r.status_code == 403, str(r.status_code))
            # PATCH on 1:1 -> 400; PATCH by non-member -> 403.
            r = await c.patch(f"/api/conversations/{conv_ab}/members",
                              json={"add": [id_c]},
                              headers={"Authorization": f"Bearer {token_a}"})
            check("PATCH members on 1:1 -> 400", r.status_code == 400, str(r.status_code))
            r = await c.patch(f"/api/conversations/{grp_id}/members",
                              json={"add": [id_b]},
                              headers={"Authorization": f"Bearer {token_b}"})
            check("PATCH members by non-member 403", r.status_code == 403, str(r.status_code))

        # Group message after leave: reaches D, NOT B (left) or C (removed).
        await ws_a.send(json.dumps({"type": "message", "conversation_id": grp_id, "content": "after leave"}))
        got_d = await recv_of(ws_d, "message")
        got_b = await collect(ws_b, 0.8)
        got_c = await collect(ws_c, 0.8)
        check("group message after leave reaches D",
              got_d is not None and got_d["message"]["content"] == "after leave", str(got_d))
        check("group message after leave NOT to B", got_b == [], str(got_b))
        check("group message after leave NOT to C", got_c == [], str(got_c))

        async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
            # Group disappearing timer + media upload keep working.
            r = await c.patch(f"/api/conversations/{grp_id}/disappearing",
                              json={"seconds": 3600},
                              headers={"Authorization": f"Bearer {token_a}"})
            check("group disappearing timer set",
                  r.status_code == 200 and r.json().get("disappearing_seconds") == 3600
                  and r.json().get("is_group") is True, r.text[:200])
            r = await c.post(f"/api/conversations/{grp_id}/upload",
                             files={"file": ("tiny.png", PNG, "image/png")},
                             headers={"Authorization": f"Bearer {token_a}"})
            check("group media upload 201", r.status_code == 201, str(r.status_code) + " " + r.text[:200])
            media_id = r.json().get("id") if r.status_code == 201 else None
            r = await c.get(f"/api/conversations/{grp_id}/media",
                            headers={"Authorization": f"Bearer {token_d}"})
            check("group media list has PNG",
                  r.status_code == 200 and any(x["id"] == media_id for x in r.json()), r.text[:200])
            # Admin sees the group (is_group + name), its messages, and its media.
            r = await c.get("/api/admin/conversations",
                            headers={"Authorization": f"Bearer {token_a}"})
            admin_list = r.json() if r.status_code == 200 else []
            admin_grp = next((x for x in admin_list if x["id"] == grp_id), None)
            check("admin list has group with is_group + name",
                  r.status_code == 200 and admin_grp is not None
                  and admin_grp.get("is_group") is True and admin_grp.get("name") == "Squad",
                  str(admin_grp))
            r = await c.get(f"/api/admin/conversations/{grp_id}/messages",
                            headers={"Authorization": f"Bearer {token_a}"})
            check("admin sees group messages",
                  r.status_code == 200 and any(m.get("content") == "hey squad" for m in r.json()),
                  str(r.status_code))
            r = await c.get("/api/admin/media",
                            headers={"Authorization": f"Bearer {token_a}"})
            check("admin Media Library has group photo",
                  r.status_code == 200 and any(x["id"] == media_id for x in r.json()), r.text[:200])

            # ---- Calls on the 1:1 A-B conversation ----
            # A-D 1:1 conversation (for the no-call offer error check).
            r = await c.post("/api/conversations", json={"user_id": id_d},
                             headers={"Authorization": f"Bearer {token_a}"})
            conv_ad = r.json().get("id")

            n_call_before = len(await call_history(c, token_a, conv_ab))

            # Invite audio A -> B; C (third user) gets nothing.
            await ws_a.send(json.dumps({"type": "call:invite", "conversation_id": conv_ab, "call_type": "audio"}))
            inv = await recv_of(ws_b, "call:invite")
            got_c = await collect(ws_c, 0.6)
            check("call:invite reaches B only",
                  inv is not None and inv.get("call_type") == "audio"
                  and inv.get("from_user_id") == id_a and inv.get("from_name") == "Alice",
                  str(inv))
            check("call:invite NOT to third user C", got_c == [], str(got_c))

            # Busy: second invite while ringing.
            await ws_a.send(json.dumps({"type": "call:invite", "conversation_id": conv_ab, "call_type": "audio"}))
            busy = await recv_of(ws_a, "call:busy")
            check("second invite while ringing -> call:busy",
                  busy is not None and busy.get("conversation_id") == conv_ab, str(busy))
            check("busy invite stores no history",
                  len(await call_history(c, token_a, conv_ab)) == n_call_before, "")

            # Screen flag while only ringing -> error.
            await ws_a.send(json.dumps({"type": "call:screen", "conversation_id": conv_ab, "sharing": True}))
            err = await recv_of(ws_a, "error")
            check("call:screen while ringing -> error", err is not None, str(err))

            # Offer/answer/ICE relay between the two peers only.
            await ws_a.send(json.dumps({"type": "call:offer", "conversation_id": conv_ab, "sdp": "offer-sdp"}))
            off = await recv_of(ws_b, "call:offer")
            check("call:offer relayed to B",
                  off is not None and off.get("sdp") == "offer-sdp" and off.get("from_user_id") == id_a,
                  str(off))
            await ws_b.send(json.dumps({"type": "call:answer", "conversation_id": conv_ab, "sdp": "answer-sdp"}))
            ans = await recv_of(ws_a, "call:answer")
            check("call:answer relayed to A",
                  ans is not None and ans.get("sdp") == "answer-sdp" and ans.get("from_user_id") == id_b,
                  str(ans))
            await ws_a.send(json.dumps({"type": "call:ice", "conversation_id": conv_ab, "candidate": "cand-1"}))
            ice = await recv_of(ws_b, "call:ice")
            got_c = await collect(ws_c, 0.6)
            check("call:ice relayed to B",
                  ice is not None and ice.get("candidate") == "cand-1", str(ice))
            check("call:ice NOT to third user C", got_c == [], str(got_c))

            # Offer with no call registered -> error, no relay.
            await ws_a.send(json.dumps({"type": "call:offer", "conversation_id": conv_ad, "sdp": "x"}))
            err = await recv_of(ws_a, "error")
            check("call:offer with no call -> error", err is not None, str(err))

            # Accept by the caller (not the callee) -> error.
            await ws_a.send(json.dumps({"type": "call:accept", "conversation_id": conv_ab}))
            err = await recv_of(ws_a, "error")
            check("call:accept by caller -> error", err is not None, str(err))

            # Callee accepts; screen sharing relays both ways.
            await ws_b.send(json.dumps({"type": "call:accept", "conversation_id": conv_ab}))
            acc = await recv_of(ws_a, "call:accept")
            check("call:accept relayed to caller",
                  acc is not None and acc.get("from_user_id") == id_b, str(acc))
            await ws_a.send(json.dumps({"type": "call:screen", "conversation_id": conv_ab, "sharing": True}))
            scr = await recv_of(ws_b, "call:screen")
            check("call:screen True relayed",
                  scr is not None and scr.get("sharing") is True and scr.get("from_user_id") == id_a,
                  str(scr))
            await ws_b.send(json.dumps({"type": "call:screen", "conversation_id": conv_ab, "sharing": False}))
            scr = await recv_of(ws_a, "call:screen")
            check("call:screen False relayed",
                  scr is not None and scr.get("sharing") is False, str(scr))

            # End: 154s -> "📞 Audio call · 2:34", sender = ender (A).
            # Second end relays but stores no duplicate.
            await ws_a.send(json.dumps({"type": "call:end", "conversation_id": conv_ab, "duration_seconds": 154}))
            end = await recv_of(ws_b, "call:end")
            check("call:end relayed to B",
                  end is not None and end.get("duration_seconds") == 154
                  and end.get("reason") == "ended" and end.get("from_user_id") == id_a,
                  str(end))
            hist = await call_history(c, token_a, conv_ab)
            ended = [m for m in hist if m.get("content") == "📞 Audio call · 2:34"]
            check("call:end stores ended-call history '📞 Audio call · 2:34'",
                  len(ended) == 1 and ended[0].get("sender_id") == id_a
                  and ended[0].get("expires_at") is None, str(hist))
            await ws_b.send(json.dumps({"type": "call:end", "conversation_id": conv_ab, "duration_seconds": 154}))
            end2 = await recv_of(ws_a, "call:end")
            hist2 = await call_history(c, token_a, conv_ab)
            check("second call:end relays, no duplicate history",
                  end2 is not None and len(hist2) == len(hist), f"before={len(hist)} after={len(hist2)}")

            # Reject -> missed video call, sender = caller.
            await ws_a.send(json.dumps({"type": "call:invite", "conversation_id": conv_ab, "call_type": "video"}))
            inv = await recv_of(ws_b, "call:invite")
            check("video invite reaches B", inv is not None and inv.get("call_type") == "video", str(inv))
            await ws_b.send(json.dumps({"type": "call:reject", "conversation_id": conv_ab}))
            rej = await recv_of(ws_a, "call:reject")
            check("call:reject relayed to caller",
                  rej is not None and rej.get("from_user_id") == id_b, str(rej))
            hist = await call_history(c, token_a, conv_ab)
            missed = [m for m in hist if m.get("content") == "📹 Missed video call"]
            check("reject stores '📹 Missed video call' from caller",
                  len(missed) == 1 and missed[0].get("sender_id") == id_a, str(hist))

            # Reject with no ringing call -> error.
            await ws_b.send(json.dumps({"type": "call:reject", "conversation_id": conv_ab}))
            err = await recv_of(ws_b, "error")
            check("call:reject with no call -> error", err is not None, str(err))

            # Group invite rejected.
            await ws_a.send(json.dumps({"type": "call:invite", "conversation_id": grp_id, "call_type": "audio"}))
            err = await recv_of(ws_a, "error")
            check("group call:invite rejected",
                  err is not None and "Group calls are not supported in Phase 2" in str(err.get("detail", "")),
                  str(err))

            # Non-participant invite -> error.
            await ws_c.send(json.dumps({"type": "call:invite", "conversation_id": conv_ab, "call_type": "audio"}))
            err = await recv_of(ws_c, "error")
            check("call:invite by non-participant -> error", err is not None, str(err))

            # Invalid call_type -> error.
            await ws_a.send(json.dumps({"type": "call:invite", "conversation_id": conv_ab, "call_type": "smoke"}))
            err = await recv_of(ws_a, "error")
            check("call:invite bad call_type -> error", err is not None, str(err))

            # GET /api/config returns the STUN default.
            r = await c.get("/api/config", headers={"Authorization": f"Bearer {token_a}"})
            check("GET /api/config STUN default",
                  r.status_code == 200
                  and r.json().get("ice_servers") == [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}],
                  r.text[:300])
            r = await c.get("/api/config")
            check("GET /api/config without auth 401", r.status_code == 401, str(r.status_code))

            # Invite to an OFFLINE user -> call:unavailable + missed-call stored.
            await ws_b.close()
            await asyncio.sleep(0.3)
            n_before = len(await call_history(c, token_a, conv_ab))
            await ws_a.send(json.dumps({"type": "call:invite", "conversation_id": conv_ab, "call_type": "audio"}))
            unav = await recv_of(ws_a, "call:unavailable")
            check("invite to offline user -> call:unavailable",
                  unav is not None and unav.get("conversation_id") == conv_ab, str(unav))
            hist = await call_history(c, token_a, conv_ab)
            missed = [m for m in hist if m.get("content") == "📞 Missed audio call"]
            check("offline invite stores '📞 Missed audio call'",
                  len(hist) == n_before + 1 and len(missed) >= 1
                  and missed[-1].get("sender_id") == id_a, str(hist))
            # Admin sees the call-history message.
            r = await c.get(f"/api/admin/conversations/{conv_ab}/messages",
                            headers={"Authorization": f"Bearer {token_a}"})
            check("admin sees call-history messages",
                  r.status_code == 200 and any(m.get("kind") == "call" for m in r.json()),
                  str(r.status_code))

        # Reconnect B for the disconnect test.
        ws_b = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_b}")
        await collect(ws_b, 0.3)
        await collect(ws_a, 0.3)

        # Disconnect during an ACTIVE call ends it for the peer + history.
        await ws_a.send(json.dumps({"type": "call:invite", "conversation_id": conv_ab, "call_type": "audio"}))
        inv = await recv_of(ws_b, "call:invite")
        check("disconnect test: invite reaches B", inv is not None, str(inv))
        await ws_b.send(json.dumps({"type": "call:accept", "conversation_id": conv_ab}))
        acc = await recv_of(ws_a, "call:accept")
        check("disconnect test: accept reaches A", acc is not None, str(acc))
        async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
            n_before = len(await call_history(c, token_a, conv_ab))
        await ws_a.close()
        end = await recv_of(ws_b, "call:end")
        check("disconnect during active call -> peer gets call:end",
              end is not None and end.get("reason") == "peer-disconnected"
              and end.get("duration_seconds") == 0, str(end))
        async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
            hist = await call_history(c, token_b, conv_ab)
            check("disconnect during active call stores ended history",
                  len(hist) == n_before + 1
                  and hist[-1].get("content", "").startswith("📞 Audio call · "),
                  str(hist))
        # Reconnect A (ws_a was closed).
        ws_a = await websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws?token={token_a}")
        await collect(ws_a, 0.3)

        # Disconnect during a RINGING call: peer gets call:end, NO history.
        await ws_a.send(json.dumps({"type": "call:invite", "conversation_id": conv_ab, "call_type": "audio"}))
        inv = await recv_of(ws_b, "call:invite")
        check("ringing-disconnect test: invite reaches B", inv is not None, str(inv))
        async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
            n_before = len(await call_history(c, token_a, conv_ab))
        await ws_a.close()
        end = await recv_of(ws_b, "call:end")
        check("disconnect during ringing call -> peer gets call:end",
              end is not None and end.get("reason") == "peer-disconnected", str(end))
        async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
            hist = await call_history(c, token_b, conv_ab)
            check("disconnect during ringing call stores NO history",
                  len(hist) == n_before, f"before={n_before} after={len(hist)}")
    finally:
        for ws in (ws_a, ws_b, ws_c, ws_d):
            try:
                await ws.close()
            except Exception:
                pass

    # ---- ICE_SERVERS_JSON override (separate server instance) ----
    ice_port = str(int(PORT) + 1)
    # Fresh temp dir every run: the ICE server registers a user, and a
    # fixed data dir would make the second run fail with duplicate email.
    ice_data = tempfile.mkdtemp(prefix="vibe-calls-ice-") + "/data"
    env = dict(os.environ)
    env["DATA_DIR"] = ice_data
    env["PORT"] = ice_port
    env["ICE_SERVERS_JSON"] = json.dumps([{"urls": ["stun:custom.example.com:19302"]}])
    backend_dir = Path(__file__).resolve().parent
    proc = subprocess.Popen(
        [sys.executable, "run.py"], cwd=str(backend_dir), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ok = False
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{ice_port}/api/health", timeout=2) as resp:
                    if resp.status == 200:
                        ok = True
                        break
            except Exception:
                time.sleep(0.2)
        check("ICE override server boots", ok, "")
        if ok:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{ice_port}", timeout=10, trust_env=False) as c:
                r = await c.post("/api/auth/register", json={
                    "display_name": "Ice", "email": "ice@example.com", "password": "secret123"})
                tok = r.json().get("access_token", "")
                r = await c.get("/api/config", headers={"Authorization": f"Bearer {tok}"})
                check("ICE_SERVERS_JSON override works",
                      r.status_code == 200
                      and r.json().get("ice_servers") == [{"urls": ["stun:custom.example.com:19302"]}],
                      r.text[:300])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


asyncio.run(main())
