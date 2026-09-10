import asyncio
import signal
from pathlib import Path

from aiohttp import web

from . import api as api_module
from .browser import Driver
from .mpris import MprisService

PROFILE_DIR = Path.home() / ".config" / "cider-shim" / "chrome-profile"
POLL_INTERVAL = 1.0


async def poll_loop(driver: Driver, mpris: MprisService):
    while True:
        state = await driver.state()
        queue = await driver.queue()
        position = queue.get("position", -1)
        items = queue.get("items", [])
        queue_meta = {
            "can_next": position >= 0 and position < len(items) - 1,
            "can_previous": position > 0,
        }
        mpris.apply_state(state, queue_meta)
        await asyncio.sleep(POLL_INTERVAL)


async def async_main():
    driver = Driver(PROFILE_DIR)
    stop_event = asyncio.Event()
    driver.on_disconnect = lambda: (print("[cider-shim] Chrome was closed -- shutting down."), stop_event.set())
    print("[cider-shim] Launching Chrome against beta.music.apple.com ...")
    await driver.start()

    mpris = MprisService(driver)
    await mpris.start()
    print(f"[cider-shim] Registered MPRIS service as {mpris.player.__class__.__module__}")

    app, port = api_module.build_app(driver)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    token_path = api_module.TOKEN_PATH
    print(f"""
[cider-shim] Ready.
  - If Chrome shows Apple Music but you're signed out, log in there now.
  - In Spun: Queue tab -> Connect to Cider (pairing auto-approves), OR
    Use an app token, pasting the token from: {token_path}
  - Local API listening on http://127.0.0.1:{port}
  - MPRIS bus name: org.mpris.MediaPlayer2.cider
""")

    poll_task = asyncio.create_task(poll_loop(driver, mpris))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()

    print("\n[cider-shim] Shutting down ...")
    poll_task.cancel()
    await runner.cleanup()
    await driver.stop()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
