"""MPRIS D-Bus player service registered as org.mpris.MediaPlayer2.cider --
the exact bus name Spun's Cider client watches for. Implements the full
standard property set (verified live: other desktop services probe all
of these on any MPRIS player and error noisily if they're missing).

All method/property-setter handlers just schedule the corresponding
async browser action and return immediately; the periodic poll loop in
main.py feeds the real resulting state back in via update_state().
"""
import asyncio

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess, BusType
from dbus_next import Variant

BUS_NAME = "org.mpris.MediaPlayer2.cider"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
LOOP_STATUS = ["None", "Track", "Playlist"]  # index == repeatMode 0/1/2


def _fire(coro):
    asyncio.ensure_future(coro)


class RootInterface(ServiceInterface):
    def __init__(self, driver):
        super().__init__("org.mpris.MediaPlayer2")
        self._driver = driver

    @method()
    async def Raise(self):
        await self._driver.bring_to_front()

    @method()
    def Quit(self):
        pass

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSetFullscreen(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Fullscreen(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return "Cider"

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> "s":
        return "sh.cider.Cider"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":
        return []


class PlayerInterface(ServiceInterface):
    def __init__(self, driver):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self._driver = driver
        self._status = "Stopped"
        self._metadata = {}
        self._position_us = 0
        self._volume = 1.0
        self._shuffle = False
        self._loop_status = "None"
        self._can_next = False
        self._can_previous = False
        self._can_seek = False
        self._rate = 1.0
        self._track_path = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
        self._pending_seek_us = 0

    # --- properties -----------------------------------------------------
    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        return self._status

    @dbus_property(access=PropertyAccess.READWRITE)
    def LoopStatus(self) -> "s":
        return self._loop_status

    @LoopStatus.setter
    def LoopStatus(self, value: "s"):
        if value in LOOP_STATUS:
            _fire(self._driver.set_repeat_mode(LOOP_STATUS.index(value)))

    @dbus_property(access=PropertyAccess.READWRITE)
    def Rate(self) -> "d":
        return self._rate

    @Rate.setter
    def Rate(self, value: "d"):
        self._rate = value or 1.0

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        return self._metadata

    @dbus_property(access=PropertyAccess.READWRITE)
    def Volume(self) -> "d":
        return self._volume

    @Volume.setter
    def Volume(self, value: "d"):
        _fire(self._driver.set_volume(max(0.0, min(1.0, value))))

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        return self._position_us

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READWRITE)
    def Shuffle(self) -> "b":
        return self._shuffle

    @Shuffle.setter
    def Shuffle(self, value: "b"):
        if value != self._shuffle:
            _fire(self._driver.toggle_shuffle())

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return self._can_next

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":
        return self._can_previous

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":
        return self._can_seek

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":
        return True

    # --- methods ----------------------------------------------------------
    @method()
    async def Next(self):
        await self._driver.next()

    @method()
    async def Previous(self):
        await self._driver.previous()

    @method()
    async def Pause(self):
        await self._driver.pause()

    @method()
    async def PlayPause(self):
        await self._driver.play_pause()

    @method()
    async def Stop(self):
        await self._driver.pause()

    @method()
    async def Play(self):
        await self._driver.play()

    @method()
    async def Seek(self, offset: "x"):
        target_us = max(0, self._position_us + offset)
        await self._seek_to(target_us)

    @method()
    async def SetPosition(self, track_id: "o", position: "x"):
        if track_id != self._track_path:
            return
        await self._seek_to(max(0, position))

    async def _seek_to(self, target_us: int):
        # Update our cached Position *before* the round-trip to Chrome: if
        # Spun does an immediate GetAll right after this method returns (it
        # does), a still-stale cached value here reads as "the seek did
        # nothing" and Spun snaps the displayed position back.
        self._position_us = target_us
        self._pending_seek_us = target_us
        self.Seeked()
        await self._driver.seek(target_us / 1_000_000)

    @method()
    def OpenUri(self, uri: "s"):
        pass

    @signal()
    def Seeked(self) -> "x":
        return self._pending_seek_us

    # --- state sync from the poll loop ------------------------------------
    def apply_state(self, state: dict, queue_meta: dict):
        """state: browser.Driver.state() result. queue_meta: {can_next, can_previous}.
        Diffs against current exported values and emits PropertiesChanged only
        for what actually moved, per the MPRIS convention."""
        changed = {}

        status = "Playing" if state.get("playing") else "Paused"
        if not state.get("ready") or not state.get("item"):
            status = "Stopped"
        if status != self._status:
            self._status = status
            changed["PlaybackStatus"] = status

        item = state.get("item")
        if item:
            track_id = "/sh/cider/track/" + "".join(
                c if c.isalnum() else "_" for c in str(item.get("id") or "0")
            )
            artwork = (item.get("artworkUrl") or "").replace("{w}", "1200").replace("{h}", "1200").replace("{f}", "jpg")
            metadata = {
                "mpris:trackid": Variant("o", track_id),
                "mpris:length": Variant("x", int((item.get("duration") or 0) * 1_000_000)),
                "xesam:title": Variant("s", item.get("title") or ""),
                "xesam:artist": Variant("as", [item.get("artist") or ""]),
                "xesam:album": Variant("s", item.get("album") or ""),
            }
            if artwork:
                metadata["mpris:artUrl"] = Variant("s", artwork)
            if self._track_path != track_id or self._metadata_repr(metadata) != self._metadata_repr(self._metadata):
                self._track_path = track_id
                self._metadata = metadata
                changed["Metadata"] = metadata
        else:
            if self._metadata:
                self._track_path = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
                self._metadata = {}
                changed["Metadata"] = {}

        position_us = int((state.get("position") or 0) * 1_000_000)
        # A jump bigger than ~1.5s from where our own clock would put it is a
        # real seek (user dragged the bar in Chrome, or another client acted).
        if abs(position_us - self._position_us) > 1_500_000:
            self._position_us = position_us
            self._pending_seek_us = position_us
            self.Seeked()
        else:
            self._position_us = position_us

        volume = state.get("volume")
        if volume is not None and abs(volume - self._volume) > 0.001:
            self._volume = volume
            changed["Volume"] = volume

        shuffle = bool(state.get("shuffleMode"))
        if shuffle != self._shuffle:
            self._shuffle = shuffle
            changed["Shuffle"] = shuffle

        loop_status = LOOP_STATUS[min(2, max(0, int(state.get("repeatMode") or 0)))]
        if loop_status != self._loop_status:
            self._loop_status = loop_status
            changed["LoopStatus"] = loop_status

        can_next = bool(queue_meta.get("can_next"))
        can_previous = bool(queue_meta.get("can_previous"))
        can_seek = bool(item)
        if can_next != self._can_next:
            self._can_next = can_next
            changed["CanGoNext"] = can_next
        if can_previous != self._can_previous:
            self._can_previous = can_previous
            changed["CanGoPrevious"] = can_previous
        if can_seek != self._can_seek:
            self._can_seek = can_seek
            changed["CanSeek"] = can_seek

        if changed:
            self.emit_properties_changed(changed)

    @staticmethod
    def _metadata_repr(metadata: dict) -> tuple:
        return tuple(
            (k, v.value if hasattr(v, "value") else v) for k, v in sorted(metadata.items())
        )


class MprisService:
    def __init__(self, driver):
        self.driver = driver
        self.bus: MessageBus | None = None
        self.root: RootInterface | None = None
        self.player: PlayerInterface | None = None

    async def start(self):
        self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self.root = RootInterface(self.driver)
        self.player = PlayerInterface(self.driver)
        self.bus.export(OBJECT_PATH, self.root)
        self.bus.export(OBJECT_PATH, self.player)
        await self.bus.request_name(BUS_NAME)

    def apply_state(self, state: dict, queue_meta: dict):
        if self.player:
            self.player.apply_state(state, queue_meta)
