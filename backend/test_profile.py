"""End-to-end verification for the Vibe profile features:
usernames (register / search / change / backfill) and profile pictures
(upload / view / remove), plus the chat close/back state logic is covered
by the frontend logic test (see TESTS.md).

Run against a FRESH database (fresh DATA_DIR). BASE is read from the PORT
env var (default 8003), same style as test_e2e.py / test_admin.py.
"""
import asyncio
import base64
import os
import sys

import httpx

PORT = os.environ.get("PORT", "8003")
BASE = f"http://127.0.0.1:{PORT}"
results = []

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PNG2 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFklEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30, trust_env=False) as c:
        # ---- register A with an explicit username ----
        r = await c.post("/api/auth/register", json={
            "display_name": "Alice", "email": "alice@example.com",
            "password": "secret123", "username": "alice_wonder"})
        check("register A with username 201", r.status_code == 201, str(r.status_code) + " " + r.text[:200])
        a = r.json() if r.status_code == 201 else {}
        check("A username kept", a.get("username") == "alice_wonder", str(a.get("username")))
        check("A avatar_url null", a.get("avatar_url") is None, str(a.get("avatar_url")))
        token_a, id_a = a.get("access_token", ""), a.get("id")
        h_a = {"Authorization": f"Bearer {token_a}"}

        # ---- register B with NO username -> auto-generated from email ----
        r = await c.post("/api/auth/register", json={
            "display_name": "Bob", "email": "bob@example.com", "password": "secret123"})
        b = r.json() if r.status_code == 201 else {}
        check("register B no username 201", r.status_code == 201, str(r.status_code))
        check("B username auto from email", b.get("username") == "bob", str(b.get("username")))
        token_b, id_b = b.get("access_token", ""), b.get("id")
        h_b = {"Authorization": f"Bearer {token_b}"}

        # ---- duplicate username rejected ----
        r = await c.post("/api/auth/register", json={
            "display_name": "Alice2", "email": "alice2@example.com",
            "password": "secret123", "username": "alice_wonder"})
        check("duplicate username 400", r.status_code == 400, str(r.status_code))

        # ---- invalid username rejected ----
        r = await c.post("/api/auth/register", json={
            "display_name": "Bad", "email": "bad@example.com",
            "password": "secret123", "username": "ab"})
        check("too-short username 400", r.status_code == 400, str(r.status_code))
        r = await c.post("/api/auth/register", json={
            "display_name": "Bad", "email": "bad2@example.com",
            "password": "secret123", "username": "Bad Name!"})
        check("bad-chars username 400", r.status_code == 400, str(r.status_code))

        # ---- me returns username ----
        r = await c.get("/api/auth/me", headers=h_a)
        check("me has username+avatar_url", r.status_code == 200 and r.json().get("username") == "alice_wonder"
              and "avatar_url" in r.json(), r.text[:150])

        # ---- search by username / name / email ----
        r = await c.get("/api/users/search", params={"q": "alice_wonder"}, headers=h_b)
        found = r.json() if r.status_code == 200 else []
        check("search by username finds A", any(u["id"] == id_a for u in found), str(found))
        check("search result has avatar_url", all("avatar_url" in u and "username" in u for u in found), str(found))
        r = await c.get("/api/users/search", params={"q": "Alice"}, headers=h_b)
        check("search by name still works", any(u["id"] == id_a for u in r.json()), str(r.status_code))
        r = await c.get("/api/users/search", params={"q": "alice@example.com"}, headers=h_b)
        check("search by email still works", any(u["id"] == id_a for u in r.json()), str(r.status_code))
        r = await c.get("/api/users/search", params={"q": "ALICE_WONDER"}, headers=h_b)
        check("search case-insensitive", any(u["id"] == id_a for u in r.json()), str(r.status_code))
        r = await c.get("/api/users/search", params={"q": "ali"}, headers=h_b)
        check("search partial match", any(u["id"] == id_a for u in r.json()), str(r.status_code))

        # ---- change username ----
        r = await c.patch("/api/users/me", json={"username": "bob_builder"}, headers=h_b)
        check("B changes username 200", r.status_code == 200 and r.json().get("username") == "bob_builder",
              str(r.status_code) + " " + r.text[:150])
        r = await c.get("/api/users/search", params={"q": "bob_builder"}, headers=h_a)
        check("new username searchable", any(u["id"] == id_b for u in r.json()), str(r.status_code))
        r = await c.get("/api/users/search", params={"q": "bob"}, headers=h_a)
        # "bob" still matches B via display name "Bob" (partial match) —
        # the point is no user carries the OLD username value any more.
        check("old username 'bob' not carried by any user",
              not any(u.get("username") == "bob" for u in r.json()), str(r.json()))
        r = await c.patch("/api/users/me", json={"username": "alice_wonder"}, headers=h_b)
        check("change to taken username 400", r.status_code == 400, str(r.status_code))
        r = await c.patch("/api/users/me", json={"username": "x"}, headers=h_b)
        check("change to invalid username 400", r.status_code == 400, str(r.status_code))
        r = await c.patch("/api/users/me", json={"display_name": "Bobby"}, headers=h_b)
        check("change display_name 200", r.status_code == 200 and r.json().get("display_name") == "Bobby",
              str(r.status_code))

        # ---- avatar upload as A ----
        r = await c.post("/api/users/me/avatar",
                         files={"file": ("pic.png", PNG, "image/png")}, headers=h_a)
        check("upload avatar PNG 201", r.status_code == 201, str(r.status_code) + " " + r.text[:200])
        a2 = r.json() if r.status_code == 201 else {}
        check("avatar_url set after upload",
              a2.get("avatar_url") == f"/api/users/{id_a}/avatar", str(a2.get("avatar_url")))

        # ---- avatar viewable via ?token= (bytes match) ----
        r = await c.get(f"/api/users/{id_a}/avatar", params={"token": token_b})
        check("avatar download bytes match (B views A)",
              r.status_code == 200 and r.content == PNG, f"status={r.status_code} len={len(r.content)}")
        r = await c.get(f"/api/users/{id_a}/avatar", headers=h_b)
        check("avatar download via Bearer", r.status_code == 200 and r.content == PNG, str(r.status_code))
        r = await c.get(f"/api/users/{id_a}/avatar")
        check("avatar without auth 401", r.status_code == 401, str(r.status_code))

        # ---- me/search/conversation list show avatar_url ----
        r = await c.get("/api/auth/me", headers=h_a)
        check("me avatar_url set", r.json().get("avatar_url") == f"/api/users/{id_a}/avatar", r.text[:150])
        r = await c.get("/api/users/search", params={"q": "alice_wonder"}, headers=h_b)
        check("search shows A avatar_url",
              any(u.get("avatar_url") == f"/api/users/{id_a}/avatar" for u in r.json()), str(r.json()))
        r = await c.post("/api/conversations", json={"user_id": id_b}, headers=h_a)
        check("A creates conv with B", r.status_code == 201, str(r.status_code))
        conv_id = r.json().get("id") if r.status_code in (200, 201) else None
        r = await c.get("/api/conversations", headers=h_b)
        entry = next((x for x in r.json() if x["id"] == conv_id), None)
        check("conv list other_user has username+avatar",
              entry is not None and entry["other_user"].get("username") == "alice_wonder"
              and entry["other_user"].get("avatar_url") == f"/api/users/{id_a}/avatar",
              str(entry))

        # ---- non-image avatar upload rejected; oversize rejected ----
        r = await c.post("/api/users/me/avatar",
                         files={"file": ("evil.txt", b"hello", "text/plain")}, headers=h_a)
        check("avatar text/plain 400", r.status_code == 400, str(r.status_code))
        big = b"\x89PNG" + b"\x00" * (5 * 1024 * 1024 + 1)
        r = await c.post("/api/users/me/avatar",
                         files={"file": ("big.png", big, "image/png")}, headers=h_a)
        check("avatar over 5MB 400", r.status_code == 400, str(r.status_code))

        # ---- replace avatar (old file dropped, new bytes served) ----
        r = await c.post("/api/users/me/avatar",
                         files={"file": ("pic2.png", PNG2, "image/png")}, headers=h_a)
        check("replace avatar 201", r.status_code == 201, str(r.status_code))
        r = await c.get(f"/api/users/{id_a}/avatar", params={"token": token_b})
        check("new avatar bytes served", r.status_code == 200 and r.content == PNG2,
              f"status={r.status_code} len={len(r.content)}")

        # ---- remove avatar ----
        r = await c.request("DELETE", "/api/users/me/avatar", headers=h_a)
        check("remove avatar 200", r.status_code == 200 and r.json().get("avatar_url") is None,
              str(r.status_code) + " " + r.text[:150])
        r = await c.get(f"/api/users/{id_a}/avatar", params={"token": token_b})
        check("avatar 404 after remove", r.status_code == 404, str(r.status_code))
        r = await c.get("/api/auth/me", headers=h_a)
        check("me avatar_url null after remove", r.json().get("avatar_url") is None, r.text[:150])

        # ---- admin users include username + avatar_url ----
        r = await c.get("/api/admin/users", headers=h_a)
        users = r.json() if r.status_code == 200 else []
        check("admin users have username+avatar_url",
              r.status_code == 200 and all("username" in u and "avatar_url" in u for u in users),
              str(r.status_code))

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


asyncio.run(main())
