"""Drives a real Chrome session against Apple's own beta.music.apple.com,
reading and controlling the page's own MusicKit JS instance.

This never touches DRM or the audio stream directly -- playback happens
entirely inside Apple's official web player. We just read/write the
MusicKit JS object the page itself already created after you log in.
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

APPLE_MUSIC_URL = "https://beta.music.apple.com"

# One JS expression, reused everywhere, that finds the page's own
# MusicKit instance. If Apple's page structure changes this is the
# single place to adjust.
_GET_MK = "window.MusicKit && window.MusicKit.getInstance && window.MusicKit.getInstance()"

STATE_JS = f"""() => {{
    const mk = {_GET_MK};
    if (!mk) return {{ ready: false }};
    const item = mk.nowPlayingItem;
    const attrsOf = (it) => (it && it.attributes) ? it.attributes : {{}};
    const a = attrsOf(item);
    // Confirmed live: item.playbackDuration is milliseconds here, not
    // seconds as MusicKit JS's public docs imply -- don't re-derive this
    // without checking raw_debug_keys() again first.
    const durationMs = item ? (a.durationInMillis || item.playbackDuration || 0) : 0;
    return {{
        ready: true,
        authorized: !!mk.isAuthorized,
        playing: !!mk.isPlaying,
        position: mk.currentPlaybackTime || 0,
        duration: durationMs / 1000,
        volume: (typeof mk.volume === 'number') ? mk.volume : 1,
        shuffleMode: mk.shuffleMode || 0,
        repeatMode: mk.repeatMode || 0,
        item: item ? {{
            id: item.id || a.playParams && a.playParams.catalogId || '',
            type: item.type || 'songs',
            title: item.title || a.name || 'Unknown',
            artist: item.artistName || a.artistName || 'Unknown',
            album: item.albumName || a.albumName || '',
            duration: durationMs / 1000,
            artworkUrl: (a.artwork && a.artwork.url) || (item.artworkURL || ''),
            url: a.url || '',
            catalogId: (a.playParams && a.playParams.catalogId) || item.id || '',
        }} : null,
    }};
}}"""

QUEUE_JS = f"""() => {{
    const mk = {_GET_MK};
    if (!mk || !mk.queue) return {{ items: [], position: -1 }};
    const items = (mk.queue.items || []).map((it) => {{
        const a = (it && it.attributes) ? it.attributes : {{}};
        return {{
            id: it.id || (a.playParams && a.playParams.catalogId) || '',
            type: it.type || 'songs',
            title: it.title || a.name || 'Unknown',
            artist: it.artistName || a.artistName || 'Unknown',
            durationInMillis: a.durationInMillis || Math.round(it.playbackDuration||0),
            artworkUrl: (a.artwork && a.artwork.url) || (it.artworkURL || ''),
            url: a.url || '',
            catalogId: (a.playParams && a.playParams.catalogId) || it.id || '',
        }};
    }});
    return {{ items, position: mk.queue.position != null ? mk.queue.position : -1 }};
}}"""


_SINGULAR_TYPE = {
    "songs": "song", "library-songs": "song",
    "albums": "album", "library-albums": "album",
    "playlists": "playlist", "library-playlists": "playlist",
    "music-videos": "musicVideo",
}


def _singular(kind: str) -> str:
    # MusicKit JS's setQueue/playNext/playLater descriptors want their own
    # singular vocabulary (confirmed live: plural Apple API resource type
    # strings like "songs" fail to resolve with MKError NOT_FOUND).
    return _SINGULAR_TYPE.get(kind, kind[:-1] if kind.endswith("s") else kind)


class Driver:
    def __init__(self, profile_dir: Path):
        self.profile_dir = profile_dir
        self._playwright = None
        self.context = None
        self.page = None
        # Set by main.py before start(): called if you close the Chrome
        # window yourself, so the whole shim exits instead of lingering as
        # a zombie process that a restart script would think is still fine.
        self.on_disconnect = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.context = await self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            executable_path="/usr/bin/chromium",
            headless=False,
            viewport={"width": 1180, "height": 820},
            args=["--app-name=CiderShim"],
            # Playwright disables Chromium's component updater by default,
            # which would stop the Widevine CDM (seeded once, manually) from
            # ever getting security/version updates on its own.
            ignore_default_args=["--disable-component-update"],
        )
        self.context.on("close", lambda: self.on_disconnect and self.on_disconnect())
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        if APPLE_MUSIC_URL not in (self.page.url or ""):
            await self.page.goto(APPLE_MUSIC_URL)

    async def stop(self):
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass  # already gone, e.g. you closed the window yourself
        if self._playwright:
            await self._playwright.stop()

    async def raw_debug_keys(self):
        """Dump what the live page actually exposes -- use this once, live,
        to correct any property names in STATE_JS/QUEUE_JS above."""
        return await self.page.evaluate(f"""() => {{
            const mk = {_GET_MK};
            if (!mk) return {{ found: false }};
            return {{
                found: true,
                instanceKeys: Object.keys(mk),
                nowPlayingItemKeys: mk.nowPlayingItem ? Object.keys(mk.nowPlayingItem) : null,
                queueKeys: mk.queue ? Object.keys(mk.queue) : null,
            }};
        }}""")

    async def state(self):
        try:
            return await self.page.evaluate(STATE_JS)
        except Exception:
            return {"ready": False}

    async def queue(self):
        try:
            return await self.page.evaluate(QUEUE_JS)
        except Exception:
            return {"items": [], "position": -1}

    async def _call(self, expr: str):
        # Async wrapper so calls that need to `await` a MusicKit promise
        # (setQueue, etc.) can -- Playwright awaits the returned promise
        # itself, so this is a no-op change for callers with plain statements.
        try:
            await self.page.evaluate(f"async () => {{ const mk = {_GET_MK}; if (mk) {{ {expr} }} }}")
            return True
        except Exception as e:
            print(f"[cider-shim] driver call failed: {expr!r} -> {e}")
            return False

    async def play(self):
        return await self._call("mk.play();")

    async def pause(self):
        return await self._call("mk.pause();")

    async def play_pause(self):
        return await self._call("mk.isPlaying ? mk.pause() : mk.play();")

    async def next(self):
        return await self._call("mk.skipToNextItem();")

    async def previous(self):
        return await self._call("mk.skipToPreviousItem();")

    async def seek(self, seconds: float):
        return await self._call(f"mk.seekToTime({seconds});")

    async def set_volume(self, volume: float):
        return await self._call(f"mk.volume = {volume};")

    async def toggle_shuffle(self):
        return await self._call("mk.shuffleMode = mk.shuffleMode ? 0 : 1;")

    async def set_repeat_mode(self, mode: int):
        return await self._call(f"mk.repeatMode = {mode};")

    async def play_item(self, kind: str, item_id: str):
        # Apple's documented pattern for a single item: a top-level
        # singular key (song/album/playlist), not a generic items array of
        # bare {id,type} stubs -- confirmed live, the items-array form
        # threw MKError NOT_FOUND even for a valid catalog id.
        key = _singular(kind)
        return await self._call(f"await mk.setQueue({{{key}: {json.dumps(item_id)}, startPlaying: true}});")

    async def play_collection(self, kind: str, item_id: str, shuffle: bool):
        key = _singular(kind)
        expr = f"await mk.setQueue({{{key}: {json.dumps(item_id)}, startPlaying: true}});"
        if shuffle:
            expr += " mk.shuffleMode = 1;"
        return await self._call(expr)

    async def jump_to_index(self, index: int):
        return await self._call(f"mk.changeToMediaAtIndex && mk.changeToMediaAtIndex({index});")

    async def play_next(self, kind: str, item_id: str):
        return await self._call(
            f"mk.playNext && mk.playNext({{items: [{{id: {json.dumps(item_id)}, type: {json.dumps(_singular(kind))}}}]}});"
        )

    async def play_later(self, kind: str, item_id: str):
        return await self._call(
            f"mk.playLater && mk.playLater({{items: [{{id: {json.dumps(item_id)}, type: {json.dumps(_singular(kind))}}}]}});"
        )

    async def run_v3(self, path: str):
        """Passthrough for Apple's Music Catalog API, via the page's own
        already-authenticated MusicKit instance (its developer token +
        your Music-User-Token) -- same mechanism as everything else here,
        no separate API credentials needed. Backs Spun's search, library
        browsing, storefront lookup, radio, and disc/album details, which
        all go through this one call in Spun's own source."""
        try:
            return await self.page.evaluate(
                f"""async () => {{
                    const mk = {_GET_MK};
                    if (!mk) return null;
                    return await mk.api.music({json.dumps(path)});
                }}"""
            )
        except Exception:
            return None

    async def bring_to_front(self):
        try:
            await self.page.bring_to_front()
            return True
        except Exception:
            return False
