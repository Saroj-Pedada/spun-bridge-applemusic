# Spun ↔ Apple Music Bridge

A local bridge that lets [Spun](https://github.com/yappologistic/Spun) (a
Linux vinyl/cassette-style music player) control real Apple Music playback,
without needing [Cider](https://cider.sh)'s paid app.

**What this is not:** it does not strip DRM, crack anything, or give you
Apple Music for free. You still need your own active Apple Music
subscription. What it replaces is Cider's *desktop client* -- this drives a
real, ordinary Chrome tab on Apple's own `beta.music.apple.com`, where you
log in exactly like in any browser, and re-exposes that tab's state as the
same local interfaces Cider provides, which Spun already knows how to talk
to (Cider's `/api/v2` HTTP API is [publicly documented](https://cider.gitbook.io/welcome-to-gitbook/docs/1.client/rpc);
this is an independent, compatible implementation of that documented
contract -- no Cider code is used or distributed).

## How it works

```
 Spun  <--MPRIS (D-Bus)-->  spun-bridge  <--CDP-->  Chrome  <--MusicKit JS-->  Apple Music
 Spun  <--HTTP :10767 -->  spun-bridge
```

- **Chrome** (a real, unmodified browser) loads `beta.music.apple.com`. You
  sign in there like you would anywhere else. Playback happens entirely
  inside Apple's own official web player.
- **spun-bridge** reads and controls that page's own MusicKit JS instance
  (via [Playwright](https://playwright.dev)) -- the same JS object Apple's
  own site already created after you log in. It doesn't touch any
  developer credentials of its own.
- That state is re-exposed two ways Spun already expects:
  - an **MPRIS** service (`org.mpris.MediaPlayer2.cider`) for now-playing
    metadata and basic transport controls
  - an **HTTP API on `127.0.0.1:10767`** implementing the subset of
    Cider's documented `/api/v2` routes that Spun actually calls, for
    queue view, search/library browsing, seeking, and volume

Because MPRIS is a per-login-session D-Bus mechanism and the HTTP API only
binds to localhost, **this cannot be shared over a network** -- each person
who wants to use it needs their own copy running on their own machine,
signed into their own Apple Music subscription. See [Multiple
machines](#multiple-machines-eg-a-friends-laptop) below.

## Requirements

- Linux with a D-Bus session bus (any normal desktop session has this)
- Python 3.10+
- Chromium or Google Chrome, **with Widevine CDM available** (needed to
  play DRM-protected streams at all -- see [Widevine setup](#widevine-setup)
  if yours doesn't have it yet)
- An active Apple Music subscription
- [Spun](https://github.com/yappologistic/Spun) built and installed

## Install

```bash
git clone <this-repo-url> ~/spun-bridge
cd ~/spun-bridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Widevine setup

Distro-packaged Chromium usually doesn't ship Widevine (licensing), but
fetches it automatically via its component updater on first real use. If
you already have a working Chromium/Chrome you use normally, it likely
already has it:

```bash
ls ~/.config/chromium/WidevineCdm    # or ~/.config/google-chrome/WidevineCdm
```

If that exists, seed spun-bridge's dedicated profile with it once:

```bash
mkdir -p ~/.config/spun-bridge/chrome-profile
cp -r ~/.config/chromium/WidevineCdm ~/.config/spun-bridge/chrome-profile/
```

If it doesn't exist yet anywhere, open your regular browser once, play any
DRM-protected video (a streaming service, YouTube Premium download, etc.)
to trigger the download, then copy it as above. spun-bridge's own profile
also has component updates left enabled, so it should eventually fetch
Widevine on its own even without the manual copy -- the copy just avoids
waiting for that.

By default spun-bridge expects `/usr/bin/chromium` at
`spun_bridge/browser.py` (`executable_path`). Edit that if your browser
lives elsewhere (e.g. Google Chrome).

## Run

```bash
./start.sh
```

This starts spun-bridge (which opens a visible Chrome window -- log in
there the first time; it's remembered after) and then launches Spun.

In Spun: **Queue tab -> Connect to Cider**. Pairing auto-approves (it's
your own local instance, so there's no real device-approval step). The
token is saved at `~/.config/spun-bridge/token.json` if you ever need it
for the manual "Use an app token" fallback.

```bash
./stop.sh
```

Stops spun-bridge and its Chrome instance. (Closing the Chrome window
yourself also cleanly shuts spun-bridge down.)

## Multiple machines (e.g. a friend's laptop)

Copy the repo over (skip `.venv` -- rebuild that fresh on the other
machine), then repeat the Install + Widevine setup steps there. The Chrome
profile and login live outside the repo, under `~/.config/spun-bridge/`,
so there's nothing there to carry over -- each person logs in fresh.
Each person needs:

- their own copy of this repo, running locally
- their own Chrome/Chromium with Widevine
- their own Spun install
- their own Apple Music subscription, signed in on their own machine

There is no way to point multiple machines at one shared instance -- see
[How it works](#how-it-works) for why.

## What works

Now-playing metadata + artwork, play/pause/next/previous, seek, volume,
shuffle, repeat mode, queue viewing, catalog/library search, and playing a
search result.

## Known limitations

- Reordering/removing individual queue items isn't implemented
  (`/api/v2/queue/move`, `/api/v2/queue/items/{index}`) -- Spun shows
  these as unavailable rather than crashing.
- Crossfade, automix, listening mode, and audio quality display aren't
  wired up (they return 404; Spun disables that bit of UI gracefully).
- The exact shape of MusicKit JS's live object (property names, singular
  vs. plural type strings, units) was worked out empirically against a
  real logged-in session, not just from Apple's docs, since those don't
  always match reality. If Apple changes their web app's internals, some
  of this may need re-checking -- see `Driver.raw_debug_keys()` in
  `spun_bridge/browser.py`, and the shim logs the real JS error whenever a
  page call throws (`spun_bridge/browser.py`, `_call`), which is usually
  enough to spot what changed.

## Troubleshooting

- **"Check Spun's API permissions in Cider"**: Spun has a stale token
  from a previous pairing (e.g. against real Cider, or an older shim
  run). Reconnect via Queue -> Connect to Cider.
- **Nothing plays / seek does nothing**: check `~/.cache/spun-bridge/shim.log`
  for the actual JS error spun-bridge caught.
- **Spun says Cider isn't reachable**: the Chrome window was probably
  closed -- rerun `./start.sh`.

## Disclaimer

This is an independent, unofficial project. It is not affiliated with,
endorsed by, or associated with Apple Inc. or Cider Collective. It
requires and respects your own paid Apple Music subscription and plays
back only through Apple's own official web player -- it does not
circumvent DRM or any technical protection measure.

## License

MIT -- see [LICENSE](LICENSE).
