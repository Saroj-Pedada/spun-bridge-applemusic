"""HTTP server on :10767 implementing the subset of Cider's documented
v2 API that Spun's src/cider.cpp actually calls (confirmed by reading
that file directly, not just the public docs). Endpoints intentionally
NOT implemented yet (crossfade, automix, listening-mode, audio-quality,
the Apple Music catalog passthrough for album/disc view) return 404,
which Spun already handles gracefully by disabling that bit of UI.
"""
import json
import secrets
from pathlib import Path

from aiohttp import web

TOKEN_PATH = Path.home() / ".config" / "spun-bridge" / "token.json"
PORT = 10767


def _load_or_create_token() -> str:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.exists():
        try:
            return json.loads(TOKEN_PATH.read_text())["token"]
        except Exception:
            pass
    token = secrets.token_hex(24)
    TOKEN_PATH.write_text(json.dumps({"token": token}))
    TOKEN_PATH.chmod(0o600)
    return token


class Api:
    def __init__(self, driver):
        self.driver = driver
        self.token = _load_or_create_token()
        self._autoplay = True  # local-only; MusicKit JS has no equivalent flag to mirror
        self.app = web.Application(middlewares=[self._auth_middleware])
        self._add_routes()

    @web.middleware
    async def _auth_middleware(self, request, handler):
        if request.path == "/api/v2/auth/request":
            return await handler(request)
        if request.headers.get("apptoken", "") != self.token:
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    def _add_routes(self):
        r = self.app.router
        r.add_post("/api/v2/auth/request", self.auth_request)
        r.add_get("/api/v2/client/info", self.client_info)
        r.add_get("/api/v2/queue", self.get_queue)
        r.add_post("/api/v2/queue/jump", self.queue_jump)
        r.add_post("/api/v2/queue/add-later", self.queue_add_later)
        r.add_post("/api/v2/queue/move", self.queue_move)
        r.add_delete("/api/v2/queue/items/{index}", self.queue_remove)
        r.add_get("/api/v2/playback/now-playing", self.now_playing)
        r.add_get("/api/v2/playback", self.playback_state)
        r.add_post("/api/v2/playback/play-item", self.play_item)
        r.add_post("/api/v2/playback/play-collection", self.play_collection)
        r.add_post("/api/v2/playback/seek", self.seek)
        r.add_post("/api/v2/playback/shuffle/toggle", self.toggle_shuffle)
        r.add_post("/api/v2/playback/autoplay/toggle", self.toggle_autoplay)
        r.add_get("/api/v2/audio/volume", self.get_volume)
        r.add_route("PATCH", "/api/v2/audio/volume", self.set_volume)
        r.add_post("/api/v1/amapi/run-v3", self.amapi_run_v3)

    # --- pairing / health -------------------------------------------------
    async def auth_request(self, request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        print(f"[spun-bridge] Pairing request from '{body.get('app_name', '?')}' -- auto-approved.")
        return web.json_response({"data": {"token": self.token}})

    async def client_info(self, request):
        return web.json_response({"data": {"version": "4.0.0-shim"}})

    # --- now playing / transport -------------------------------------------
    @staticmethod
    def _item_attrs(item: dict) -> dict:
        return {
            "name": item.get("title", ""),
            "artistName": item.get("artist", ""),
            "albumName": item.get("album", ""),
            "durationInMillis": (item.get("duration") or 0) * 1000,
            "artwork": {"url": item.get("artworkUrl", "")},
            "url": item.get("url", ""),
            "playParams": {"catalogId": item.get("catalogId", ""), "id": item.get("catalogId", "")},
        }

    async def now_playing(self, request):
        state = await self.driver.state()
        item = state.get("item")
        if not item:
            return web.json_response({"data": {}})
        data = {"id": item.get("id", ""), "type": item.get("type", "songs")}
        data.update(self._item_attrs(item))
        return web.json_response({"data": data})

    async def playback_state(self, request):
        state = await self.driver.state()
        return web.json_response({
            "data": {
                "time": {"currentTime": state.get("position", 0)},
                "shuffleMode": bool(state.get("shuffleMode")),
                "autoplay": self._autoplay,
            }
        })

    async def play_item(self, request):
        body = await request.json()
        ok = await self.driver.play_item(body.get("type", "songs"), body.get("id", ""))
        if not ok:
            return web.json_response({"error": "could not play that item"}, status=502)
        return web.json_response({"status": "ok"})

    async def play_collection(self, request):
        body = await request.json()
        ok = await self.driver.play_collection(body.get("type", "albums"), body.get("id", ""), bool(body.get("shuffle")))
        if not ok:
            return web.json_response({"error": "could not play that collection"}, status=502)
        return web.json_response({"status": "ok"})

    async def seek(self, request):
        body = await request.json()
        await self.driver.seek(float(body.get("position", 0)))
        return web.json_response({"status": "ok"})

    async def toggle_shuffle(self, request):
        await self.driver.toggle_shuffle()
        return web.json_response({"status": "ok"})

    async def toggle_autoplay(self, request):
        self._autoplay = not self._autoplay
        return web.json_response({"status": "ok"})

    # --- volume -------------------------------------------------------------
    async def get_volume(self, request):
        state = await self.driver.state()
        return web.json_response({"data": {"volume": state.get("volume", 1)}})

    async def set_volume(self, request):
        body = await request.json()
        await self.driver.set_volume(float(body.get("volume", 1)))
        return web.json_response({"status": "ok"})

    # --- queue --------------------------------------------------------------
    async def get_queue(self, request):
        offset = int(request.query.get("offset", 0))
        limit = int(request.query.get("limit", 50))
        q = await self.driver.queue()
        items = q.get("items", [])
        page = items[offset:offset + limit]
        data_items = [{"id": it["id"], "type": it["type"], "attributes": self._item_attrs({
            "title": it["title"], "artist": it["artist"], "duration": it["durationInMillis"] / 1000,
            "artworkUrl": it["artworkUrl"], "url": it["url"], "catalogId": it["catalogId"],
        })} for it in page]
        return web.json_response({
            "data": {"items": data_items, "position": q.get("position", -1)},
            "meta": {"total": len(items)},
        })

    async def queue_jump(self, request):
        body = await request.json()
        await self.driver.jump_to_index(int(body.get("index", 0)))
        return web.json_response({"status": "ok"})

    async def queue_add_later(self, request):
        body = await request.json()
        await self.driver.play_later(body.get("type", "songs"), body.get("id", ""))
        return web.json_response({"status": "ok"})

    async def queue_move(self, request):
        # MusicKit JS has no confirmed public reorder-in-place call; report
        # "not supported" rather than silently doing nothing.
        return web.json_response({"error": "not supported by spun-bridge yet"}, status=404)

    async def queue_remove(self, request):
        return web.json_response({"error": "not supported by spun-bridge yet"}, status=404)

    # --- catalog passthrough (search, library, storefront, radio, disc) ----
    async def amapi_run_v3(self, request):
        body = await request.json()
        path = body.get("path", "")
        if not path:
            return web.json_response({"error": "missing path"}, status=400)
        result = await self.driver.run_v3(path)
        if result is None:
            return web.json_response({"error": "catalog request failed"}, status=502)
        # MusicKit JS's raw result carries extra fields (status, url, an
        # always-present "errors": null, ...) alongside "data" -- some of
        # Spun's parsing only checks for an "errors" *key*, not its value,
        # so pass through just the part Cider's real API actually returns.
        return web.json_response({"data": result.get("data", {})})


def build_app(driver) -> tuple[web.Application, int]:
    api = Api(driver)
    return api.app, PORT
