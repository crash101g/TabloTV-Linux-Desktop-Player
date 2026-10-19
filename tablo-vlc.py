#!/usr/bin/env python3
"""tablo-vlc: watch live TV from a Tablo 4th Gen in VLC.

Needs nothing but Python 3 (built into Ubuntu) and VLC. No install.
Settings (email, password, favorites) live in tablo.ini next to this script;
activity is logged to tablo-vlc.log in the same folder.

Command line:
  ./tablo-vlc.py                 channel menu in the terminal; type a number to watch
  ./tablo-vlc.py --play 2.1      play one channel and exit (VLC, or ffplay if no VLC)
  ./tablo-vlc.py --play favorites   open your favorites as a VLC playlist
  ./tablo-vlc.py --play all      open every channel as a VLC playlist
  ./tablo-vlc.py --list          print favorites and all channels, then exit
  ./tablo-vlc.py --info 9.1      what's on a channel now, its description, and what's next
  ./tablo-vlc.py --add-fav 9.1 702.1     save channels to favorites (tablo.ini)
  ./tablo-vlc.py --remove-fav 9.1        remove a favorite
  In the menu: +3 adds menu item 3 to favorites, -3 removes it
  ./tablo-vlc.py --setup         enter your Tablo email + password

Optional web page and other devices:
  ./tablo-vlc.py --web           channel menu + a web page with Play buttons
  ./tablo-vlc.py --lan           share with phones, tablets and TVs on your Wi-Fi
                                 (needs ffmpeg on this PC for browser/TV playback)
     iPhone/iPad, Android:  http://<PC-IP>:8000/  in Safari/Chrome (AirPlay / Cast from the player)
     Fire TV, Samsung TV:   http://<PC-IP>:8000/tv  in Silk / Internet (remote-friendly)
     Google TV, Apple TV:   Cast / AirPlay from a phone, or an IPTV app
     IPTV apps:             /tv.m3u + /guide.xml  (TiviMate, UHF, iPlayTV, Kodi)
  Runs on Ubuntu, Pop!_OS and other Linux, and Windows 11 (py tablo-vlc.py).

Extras:
  --tune 9.1       print a one-time VLC command for the Tablo's raw stream
  --vlc-commands   print a VLC command per local channel (script must be running)
  --ffplay         play in ffplay instead of VLC (or --player mpv, --player vlc)
  --offline        LAN-only mode: no internet at all; local channels from the saved list
  --tablo-ip ADDR  your Tablo's address if it changed (e.g. 192.168.1.50)
  --direct         never use ffmpeg, even if it's installed
  --port N         use a different local port (default 8000)
  --debug          show detailed messages on screen
"""

import argparse
import configparser
import getpass
import hashlib
import hmac
import html
import json
import logging
import logging.handlers
import os
import re
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import textwrap
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict
from http.client import HTTPException
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


IS_WINDOWS = os.name == "nt"
# How to run this script, for help text: Windows uses the py launcher.
RUN = "py tablo-vlc.py" if IS_WINDOWS else "./tablo-vlc.py"

# Never crash printing ★ · – on a console or file that can't show them.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


def enable_windows_ansi() -> bool:
    """Turn on color codes in Windows consoles (Windows Terminal already has them)."""
    if not IS_WINDOWS:
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)                     # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k32.SetConsoleMode(h, mode.value | 0x0004))  # VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


def spawn(cmd, **kw):
    """Start a player/helper that keeps running on its own (and survives Ctrl+C here)."""
    if IS_WINDOWS:
        kw.setdefault("creationflags", subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    else:
        kw.setdefault("start_new_session", True)
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kw)


def clock(dt) -> str:
    """'5:37 AM' on every OS (Linux's %-I doesn't exist on Windows)."""
    return dt.astimezone().strftime("%I:%M %p").lstrip("0")


# Config lives next to this script: same folder as tablo-vlc.py
CONFIG_FILE = Path(__file__).resolve().parent / "tablo.ini"
LOG_FILE = Path(__file__).resolve().parent / "tablo-vlc.log"
# Saved copy of your Tablo's address + channel list (+ last guide info) so the
# script still starts and plays local channels when the internet is down.
CACHE_FILE = Path(__file__).resolve().parent / "tablo-cache.json"
DEFAULT_PORT = 8000
USE_FFMPEG = False  # set in main()

CONFIG_HEADER = """\
# tablo-vlc settings. Edit with any text editor.
#   email / password : your Tablo account login
#   favorites        : channels to list first, e.g. 4.1, 9.1, KFOR (or use --add-fav / --remove-fav)
#   port             : local port for the playlist server
#   device           : (optional) Tablo name, if your account has more than one
#   vlc_buffer       : VLC buffer for live TV in milliseconds (default 3000; more = smoother, slower start)
#   deinterlace      : VLC deinterlace mode for 1080i broadcasts: yadif2x (default), yadif, bob, off
#   browser_quality  : max picture height when watching in a phone/tablet browser: 480, 720 (default), 1080
#   tv_quality       : max picture height for TVs, Cast and IPTV apps: 720, 1080 (default)
#   player           : (optional) video player: vlc, flatpak (Flatpak VLC), ffplay, mpv,
#                      or a full command. Blank = VLC (regular, Snap or Flatpak), else ffplay.
#   hash_key / device_key : Tablo app signing keys (needed to tune channels).
#                      Same for every Tablo owner. Filled in automatically from
#                      the tablo-api project on GitHub; refresh with --fetch-keys.
# No quotes needed around values. Keep this file private (don't commit it).

"""


# ----------------------------------------------------------------------
# Logging: console + tablo-vlc.log next to the script (rotates at 1 MB,
# keeps 3 old files). Your password is never logged.
# ----------------------------------------------------------------------
log = logging.getLogger("tablo-vlc")
CONSOLE: logging.Handler | None = None


def setup_logging(debug: bool = False) -> None:
    log.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    global CONSOLE
    ch = CONSOLE = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    log.addHandler(ch)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)  # allow % in passwords
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE, encoding="utf-8")
    if "tablo" not in cfg:
        cfg["tablo"] = {}
    return cfg


def save_config(cfg: configparser.ConfigParser) -> None:
    t = cfg["tablo"]
    for key, default in (("email", ""), ("password", ""), ("favorites", ""),
                         ("port", str(DEFAULT_PORT)), ("player", ""), ("vlc_buffer", "3000"),
                         ("deinterlace", "yadif2x"), ("browser_quality", "720"), ("tv_quality", "1080"), ("hash_key", ""), ("device_key", "")):
        t.setdefault(key, default)
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(CONFIG_HEADER)
        cfg.write(f)
    os.chmod(CONFIG_FILE, 0o600)  # only you can read it


# ----------------------------------------------------------------------
# Tablo 4th Gen API (standard library only)
#   Login flow and HMAC-MD5 request signing as documented by the
#   tablo-api (trevor-viljoen) and tablo2plex projects. The two signing keys
#   are static values embedded in the official Tablo app; they are NOT in
#   this file. Put them in tablo.ini (hash_key / device_key), or install the
#   tablo-api package and they're read from there.
# ----------------------------------------------------------------------
CLOUD = "https://lighthousetv.ewscloud.com"
CLOUD_UA = "Tablo-FAST/2.0.0 (Mobile; iPhone; iOS 16.6)"
LOCAL_UA = "Tablo-FAST/1.7.0 (Mobile; iPhone; iOS 18.4)"
HASH_KEY = ""    # loaded by load_keys()
DEVICE_KEY = ""


KEY_SOURCES = [
    # (description, raw file URL, regex for hash key, regex for device key)
    ("tablo-api (trevor-viljoen) on GitHub",
     "https://raw.githubusercontent.com/trevor-viljoen/tablo-api/main/tablo_api/auth.py",
     r'_HASH_KEY\s*=\s*"([A-Za-z0-9]{20,})"', r'_DEVICE_KEY\s*=\s*"([A-Za-z0-9]{20,})"'),
    ("tablo2plex (hearhellacopters) on GitHub",
     "https://raw.githubusercontent.com/hearhellacopters/tablo2plex/main/src/Encryption.js",
     r'HashKey\s*==\s*undefined\s*\?\s*"([A-Za-z0-9]{20,})"', r'DeviceKey\s*==\s*undefined\s*\?\s*"([A-Za-z0-9]{20,})"'),
]


def fetch_keys(cfg) -> bool:
    """Download the public signing keys from GitHub and save them to tablo.ini."""
    for name, url, hash_re, dev_re in KEY_SOURCES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tablo-vlc"})
            with urllib.request.urlopen(req, timeout=15) as r:
                text = r.read().decode("utf-8", "replace")
        except Exception as e:
            log.warning(f"Couldn't download keys from {name}: {e}")
            continue
        h, d = re.search(hash_re, text), re.search(dev_re, text)
        if h and d:
            cfg["tablo"]["hash_key"], cfg["tablo"]["device_key"] = h.group(1), d.group(1)
            save_config(cfg)
            log.info(f"Saved Tablo signing keys to {CONFIG_FILE.name} (from {name})")
            return True
        log.warning(f"Keys not found in {name} (the file may have changed)")
    return False


def load_keys(t) -> bool:
    """Signing keys from tablo.ini, else from the tablo-api package if installed."""
    global HASH_KEY, DEVICE_KEY
    HASH_KEY = t.get("hash_key", "").strip().strip('"')
    DEVICE_KEY = t.get("device_key", "").strip().strip('"')
    if not (HASH_KEY and DEVICE_KEY):
        try:
            from tablo_api import auth as _ta  # optional: pip install tablo-api
            HASH_KEY = HASH_KEY or _ta._HASH_KEY
            DEVICE_KEY = DEVICE_KEY or _ta._DEVICE_KEY
            log.debug("Signing keys loaded from the tablo-api package")
        except Exception:
            pass
    return bool(HASH_KEY and DEVICE_KEY)


class NetworkError(RuntimeError):
    """Couldn't reach a host: offline, DNS failure, timeout, connection refused/reset."""


class HTTPStatusError(RuntimeError):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


def http(method, url, headers=None, body=None, timeout=15, retries=2):
    """JSON request with retries for flaky connections. Raises NetworkError when
    the host can't be reached and HTTPStatusError for HTTP error codes."""
    host = urllib.parse.urlsplit(url).hostname
    err = None
    for attempt in range(retries + 1):
        log.debug(f"{method} {url}" + (f" (retry {attempt})" if attempt else ""))
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            msg = f"{method} {url.split('?')[0]} -> HTTP {e.code}: {e.read()[:200]!r}"
            if (e.code >= 500 or e.code == 429) and attempt < retries:
                err = HTTPStatusError(msg, e.code)
            else:
                raise HTTPStatusError(msg, e.code) from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.gaierror):
                err = NetworkError(
                    f"Can't look up '{host}' (DNS). Check this PC is online and that no "
                    f"DNS filter (Pi-hole, AdGuard, VPN) is blocking it.")
            else:
                err = NetworkError(f"Can't reach {host}: {e.reason}")
        except (TimeoutError, socket.timeout):
            err = NetworkError(f"Timed out talking to {host}")
        except (ConnectionError, HTTPException, OSError) as e:
            err = NetworkError(f"Connection to {host} failed: {e}")
        except json.JSONDecodeError:
            raise RuntimeError(f"Unexpected (non-JSON) reply from {host}") from None
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise err


def load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(data: dict) -> None:
    try:
        tmp = CACHE_FILE.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, CACHE_FILE)
    except Exception as e:
        log.debug(f"Couldn't save {CACHE_FILE.name}: {e}")


def device_auth(method, path, body=""):
    date = format_datetime(datetime.now(timezone.utc), usegmt=True)
    md5 = hashlib.md5(body.encode()).hexdigest() if body else ""
    sig = hmac.new(HASH_KEY.encode(), f"{method}\n{path}\n{md5}\n{date}".encode(), hashlib.md5).hexdigest()
    return f"tablo:{DEVICE_KEY}:{sig}", date


def pick_logo(logos) -> str:
    logos = [l for l in (logos or []) if isinstance(l, dict) and l.get("url")]
    pref = next((l for l in logos if l.get("kind") == "lightLarge"), None)
    return (pref or (logos[0] if logos else {})).get("url", "")


def vlc_tuning() -> dict:
    """Per-item VLC options for smooth live TV."""
    opts = {"network-caching": str(VLC_BUFFER_MS)}
    if DEINTERLACE and DEINTERLACE != "off":
        opts["deinterlace"] = "-1"            # automatic: only interlaced (e.g. 1080i) broadcasts
        opts["deinterlace-mode"] = DEINTERLACE
    return opts


def vlc_meta(ch, a: dict | None) -> dict:
    """Title/description/genre VLC shows in its title bar and Media Information."""
    title = ch.display_name + (f" · {airing_line(a)}" if a else "")
    desc = ""
    if a:
        desc = " | ".join(x for x in (
            f"{a['start']}–{a['end']}" if a["start"] else "",
            " · ".join(x for x in (a["kind"], a["genres"], a["rating"]) if x),
            a["description"]) if x)
    meta = {"meta-title": title}
    if desc:
        meta["meta-description"] = desc
    if a and a["genres"]:
        meta["meta-genre"] = a["genres"]
    if ch.network:
        meta["meta-publisher"] = ch.network
    return {k: v.replace("\n", " ").replace("\r", " ") for k, v in meta.items()}


def parse_airing(a: dict) -> dict:
    """Flatten a Tablo guide airing into the fields we display."""
    show = a.get("show") or {}
    ep = a.get("episode") or {}
    season = (ep.get("season") or {}).get("number")
    show_title = show.get("title") or a.get("title") or ""
    ep_title = a.get("title") if a.get("title") and a.get("title") != show_title else ""
    start = end = None
    try:
        start = datetime.fromisoformat((a.get("datetime") or "").replace("Z", "+00:00"))
        end = start + timedelta(seconds=int(a.get("duration") or 0))
    except Exception:
        pass
    return {
        "show": show_title, "episode": ep_title,
        "se": (f"S{season}E{ep['episodeNumber']}" if season and ep.get("episodeNumber")
               else f"E{ep['episodeNumber']}" if ep.get("episodeNumber") else ""),
        "rating": ep.get("rating") or (a.get("movieAiring") or {}).get("rating") or "",
        "genres": ", ".join(a.get("genres") or []),
        "description": (a.get("description") or "").strip(),
        "kind": {"episode": "", "movieAiring": "Movie", "sportEvent": "Sports"}.get(a.get("kind"), ""),
        "_start": start, "_end": end,
        "start": clock(start) if start else "",
        "end": clock(end) if end else "",
    }


def airing_line(a: dict) -> str:
    """'Jeopardy! — S41E12 "Teachers Tournament"' style one-liner."""
    if not a:
        return ""
    bits = [a["show"] or "?"]
    if a["se"]:
        bits.append(a["se"])
    if a["episode"]:
        bits.append(f'"{a["episode"]}"')
    return " ".join(bits)


def time_left(a: dict) -> str:
    if not a or not a.get("_end"):
        return ""
    mins = int((a["_end"] - datetime.now(timezone.utc)).total_seconds() // 60)
    return f"{mins} min left" if 0 <= mins < 600 else ""


@dataclass
class Channel:
    identifier: str
    call_sign: str
    major: int = 0
    minor: int = 0
    network: str = ""
    kind: str = "ota"
    stream_url: str = ""   # FAST channels: the provider's own internet stream
    logo: str = ""

    @property
    def display_name(self):
        return f"{self.major}.{self.minor} {self.call_sign}" if self.major else self.call_sign


class Tablo:
    def __init__(self, email: str, password: str, device_name: str = "",
                 lan_only: bool = False, tablo_ip: str = ""):
        self.email, self.password, self.device_name = email, password, device_name
        self.lan_only = lan_only
        self.lock = threading.Lock()
        self.cache = load_cache()
        self.client_id = self.cache.get("client_id") or str(uuid.uuid4())
        self.channels: list[Channel] = []
        self.online = False
        self.access_token = self.lh_token = ""
        self._last_try = 0.0
        self._timeout, self._retries = 10, 2
        if lan_only:
            if not self.cache.get("channels"):
                raise NetworkError("No saved channel list yet. Run once with the internet connected "
                                   "(without --offline) so it can be saved.")
            self._use_cache(None)
        else:
            try:
                self.connect()
            except NetworkError as e:
                if not self.cache.get("channels"):
                    raise NetworkError(f"{e}\nNo saved channel list yet, so the script needs the internet "
                                       f"once to get started.") from None
                self._use_cache(e)
        if tablo_ip:
            self.set_address(tablo_ip)

    def set_address(self, addr: str) -> None:
        """--tablo-ip: '192.168.1.50', '192.168.1.50:8885' or 'http://...'."""
        addr = addr.strip().rstrip("/")
        if not addr.startswith("http"):
            if ":" not in addr:
                port = urllib.parse.urlsplit(self.device_url).port if self.device_url else None
                addr = f"{addr}:{port or 8885}"
            addr = "http://" + addr
        if addr != self.device_url:
            log.info(f"Using Tablo address {addr}" + (f" (was {self.device_url})" if self.device_url else ""))
            self.device_url = addr
            if self.channels:
                self._save_cache()

    def _use_cache(self, why) -> None:
        """Start from the saved channel list (no internet)."""
        c = self.cache
        self.device_url = c.get("device_url", "")
        self.channels = [Channel(**ch) for ch in c.get("channels", [])]
        self.online = False
        self.device_label = c.get("device_name", "")
        self._last_try = time.time()
        saved = f"the channel list saved {c.get('saved', 'earlier')} for '{c.get('device_name', 'Tablo')}' at {self.device_url}"
        if why is None:   # --offline
            log.info(f"LAN-only mode (no internet): using {saved}.")
            return
        log.warning(f"OFFLINE: can't reach the Tablo cloud ({why}).")
        log.warning(f"Using {saved}. Local channels should still play; FAST channels and the guide "
                    f"need the internet. Will retry automatically.")

    def _save_cache(self, **extra) -> None:
        self.cache.update({
            "saved": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "device_name": getattr(self, "device_label", ""),
            "device_url": self.device_url,
            "client_id": self.client_id,
            "channels": [asdict(c) for c in self.channels],
            **extra})
        save_cache(self.cache)

    def maybe_reconnect(self, min_gap: int = 60) -> bool:
        """When offline, try the cloud again (at most once a minute). True if online."""
        if self.online:
            return True
        if self.lan_only:     # --offline: never touch the internet
            return False
        if time.time() - self._last_try < min_gap:
            return False
        self._last_try = time.time()
        old = (self._timeout, self._retries)
        self._timeout, self._retries = 5, 0   # quick check; don't stall the menu
        try:
            self.connect()
            log.warning("Internet is back: reconnected to the Tablo cloud.")
            return True
        except Exception as e:
            log.debug(f"Still offline: {e}")
            return False
        finally:
            self._timeout, self._retries = old

    def connect(self) -> None:
        jhdr = {"User-Agent": CLOUD_UA, "Content-Type": "application/json"}
        to, rt = self._timeout, self._retries
        login = http("POST", f"{CLOUD}/api/v2/login/", jhdr,
                     json.dumps({"email": self.email, "password": self.password}).encode(),
                     timeout=to, retries=rt)
        self.access_token = login["access_token"]
        auth = {**jhdr, "Authorization": f"{login['token_type']} {self.access_token}"}

        acct = http("GET", f"{CLOUD}/api/v2/account/", auth, timeout=to, retries=rt)
        if not acct.get("devices"):
            sys.exit("No Tablo devices found on this account.")
        dev = acct["devices"][0]
        if self.device_name:
            dev = next((d for d in acct["devices"]
                        if d["name"].lower() == self.device_name.lower()), dev)
        self.device_url = dev["url"].rstrip("/")

        sel = http("POST", f"{CLOUD}/api/v2/account/select/", auth,
                   json.dumps({"pid": acct["profiles"][0]["identifier"],
                               "sid": dev["serverId"]}).encode(), timeout=to, retries=rt)
        self.lh_token = sel["token"]

        raw = http("GET", f"{CLOUD}/api/v2/account/{self.lh_token}/guide/channels/",
                   {**jhdr, "Authorization": f"Bearer {self.access_token}",
                    "Lighthouse": self.lh_token, "Accept": "*/*"}, timeout=to, retries=rt)
        chans, kinds = [], {}
        for c in raw:
            kind = (c.get("kind") or "ota").lower()
            kinds[kind] = kinds.get(kind, 0) + 1
            info = c.get(kind) or c.get("ota") or c.get("ott") or {}
            if kind == "ota":
                label = info.get("callSign") or c.get("name") or c["identifier"]
                network = info.get("network") or ""
            else:  # FAST / streaming: the channel's name is the useful label
                label = c.get("name") or info.get("network") or info.get("callSign") or c["identifier"]
                network = info.get("provider") or ""
            chans.append(Channel(
                identifier=c["identifier"], call_sign=label,
                major=info.get("major") or 0, minor=info.get("minor") or 0,
                network=network, kind="ota" if kind == "ota" else "ott",
                stream_url=info.get("streamUrl") or "",
                logo=pick_logo(c.get("logos"))))
        log.debug(f"Channel kinds from Tablo cloud: {kinds}")
        if not any(k != "ota" for k in kinds):
            log.info("Tablo returned no FAST/streaming channels for this account "
                     "(check FAST channels are enabled in the Tablo app).")
        chans.sort(key=lambda c: (c.kind != "ota", c.major, c.minor, c.call_sign.lower()))
        self.channels = chans
        self.device_label = dev["name"]
        self.online = True
        self._save_cache()
        log.info(f"Connected to '{dev['name']}' at {self.device_url} "
              f"({len(self.ota)} local, {len(self.ott)} FAST channels)")

    @property
    def ota(self):
        return [c for c in self.channels if c.kind == "ota"]

    @property
    def ott(self):
        return [c for c in self.channels if c.kind != "ota"]

    # ---------------- guide (what's on) ----------------
    def _guide_headers(self):
        return {"User-Agent": CLOUD_UA, "Accept": "*/*", "Content-Type": "application/json",
                "Authorization": f"Bearer {self.access_token}", "Lighthouse": self.lh_token}

    def now_playing(self, max_age: int = 120) -> dict:
        """{channel id: airing} for what's on right now (one request, cached briefly)."""
        cache = getattr(self, "_now_cache", None)
        if cache and time.time() - cache[0] < max_age and all(
                not a.get("_end") or a["_end"] > datetime.now(timezone.utc) for a in cache[1].values()):
            return cache[1]
        if not self.maybe_reconnect():
            return self._saved_now()
        now, raw = {}, {}
        try:
            rows = http("GET", f"{CLOUD}/api/v2/account/{self.lh_token}/guide/channels/live/",
                        self._guide_headers(), timeout=6, retries=1) or []
            for row in rows:
                ch, airing = row.get("channel") or {}, row.get("airing") or {}
                if ch.get("identifier") and airing:
                    now[ch["identifier"]] = parse_airing(airing)
                    raw[ch["identifier"]] = airing
            log.debug(f"Guide: now-playing info for {len(now)} channels")
            self._save_cache(now=raw)
        except NetworkError as e:
            log.warning(f"Couldn't load what's on now: {e}")
            self.online = False   # back off; maybe_reconnect() retries later
            self._last_try = time.time()
            now = self._saved_now()
        except Exception as e:
            log.warning(f"Couldn't load what's on now: {e}")
            now = self._saved_now()
        self._now_cache = (time.time(), now)
        return now

    def _saved_now(self) -> dict:
        """Last saved guide info that is still on air."""
        out, t = {}, datetime.now(timezone.utc)
        for ident, raw in (self.cache.get("now") or {}).items():
            a = parse_airing(raw)
            if a["_start"] and a["_end"] and a["_start"] <= t < a["_end"]:
                out[ident] = a
        return out

    def airings(self, ident: str, days: int = 2, max_age: int = 1800) -> list:
        """Guide listings for one channel (today + tomorrow), cached for 30 minutes.
        Each item keeps the raw Tablo airing under '_raw' for the XMLTV guide."""
        cache = getattr(self, "_air_cache", None)
        if cache is None:
            cache = self._air_cache = {}
        hit = cache.get(ident)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        if not self.maybe_reconnect():
            return hit[1] if hit else []
        out, seen = [], set()
        for n in range(days):
            day = datetime.now().date() + timedelta(days=n)
            try:
                rows = http("GET", f"{CLOUD}/api/v2/account/guide/channels/{ident}/airings/{day.isoformat()}/",
                            self._guide_headers(), timeout=6, retries=1) or []
            except Exception as e:
                log.debug(f"Couldn't load guide for {ident} on {day}: {e}")
                continue
            for raw in rows:
                a = parse_airing(raw)
                if a["_start"] and a["_start"] not in seen:
                    seen.add(a["_start"])
                    a["_raw"] = raw
                    out.append(a)
        out.sort(key=lambda a: a["_start"])
        cache[ident] = (time.time(), out)
        return out

    def upcoming(self, ident: str, count: int = 6) -> list:
        """Next few airings on one channel."""
        now = datetime.now(timezone.utc)
        return [a for a in self.airings(ident) if a["_end"] and a["_end"] > now][:count]

    def by_id(self, ident: str):
        return next((c for c in self.channels if c.identifier == ident), None)

    def find(self, wanted: str):
        """Match '9.1', '9-1', '9', 'KWTV', 'kwtv-dt', 'CBS', a streaming channel name or an id."""
        w = wanted.strip().strip("\"'[]()").strip().lower()
        if not w:
            return None

        def base(cs):  # KWTV-DT -> kwtv
            return re.sub(r"[-_ ](dt|hd|tv|ld|cd|ca)\d*$", "", cs.lower())

        m = re.fullmatch(r"(\d+)(?:\s*[.\-_ ]\s*(\d+))?", w)
        if m:
            major, minor = int(m.group(1)), m.group(2)
            for ch in self.ota + self.ott:
                if ch.major == major and (minor is None or ch.minor == int(minor)):
                    return ch
            return None
        tests = (
            lambda c: c.identifier.lower() == w,
            lambda c: c.call_sign.lower() == w,
            lambda c: base(c.call_sign) == base(w),
            lambda c: c.kind == "ota" and c.network.lower() == w,
            lambda c: c.kind != "ota" and w in c.call_sign.lower(),
        )
        for test in tests:
            for ch in self.channels:
                if test(ch):
                    return ch
        return None

    def _watch(self, ident: str) -> str:
        path = f"/guide/channels/{ident}/watch"
        body = json.dumps({
            "bandwidth": None,
            "extra": {"limitedAdTracking": 1, "deviceOSVersion": "16.6", "lang": "en_US",
                      "height": 1080, "deviceId": "00000000-0000-0000-0000-000000000000",
                      "width": 1920, "deviceModel": "iPhone10,1", "deviceMake": "Apple",
                      "deviceOS": "iOS"},
            "device_id": self.client_id,
            "platform": "ios",
        })
        auth, date = device_auth("POST", path, body)
        resp = http("POST", f"{self.device_url}{path}?lh", {
            "Authorization": auth, "Date": date, "Accept": "*/*", "User-Agent": LOCAL_UA,
            "Content-Type": "application/x-www-form-urlencoded"}, body.encode(), timeout=10, retries=1)
        if not resp or not resp.get("playlist_url"):
            raise RuntimeError(f"No playlist_url in response: {resp}")
        self._last_token = resp.get("token") or ""
        return resp["playlist_url"]

    def end_session(self, token: str) -> None:
        """Tell the Tablo we're done with a stream so its tuner is freed right away
        (instead of when the session times out)."""
        if not token:
            return
        path = f"/player/sessions/{token}"
        auth, date = device_auth("DELETE", path)
        try:
            http("DELETE", f"{self.device_url}{path}?lh", {
                "Authorization": auth, "Date": date, "Accept": "*/*", "User-Agent": LOCAL_UA},
                timeout=5, retries=0)
            log.debug(f"Ended Tablo session {token[:8]}…")
        except Exception as e:
            log.debug(f"Couldn't end Tablo session {token[:8]}…: {e}")

    def watch_session(self, ident: str) -> tuple[str, str]:
        """(stream URL, session token); token is '' for a FAST direct stream."""
        self._last_token = ""
        url = self.watch_url(ident)
        return url, self._last_token

    def watch_url(self, ident: str) -> str:
        ch = self.by_id(ident)
        with self.lock:
            if ch and ch.kind != "ota" and not self.maybe_reconnect(min_gap=15):
                raise RuntimeError("FAST (online) channels need the internet" +
                                   (" (you're in --offline mode)." if self.lan_only else
                                    ", which isn't available right now.") + " Local channels still work.")
            try:
                return self._watch(ident)
            except Exception as e:
                first = e
            if ch and ch.stream_url and self.online:
                log.warning(f"[{ch.display_name}] Tablo watch failed ({first}); using the channel's direct stream")
                return ch.stream_url
            if self.lan_only:
                if isinstance(first, NetworkError):
                    raise RuntimeError(f"Can't reach your Tablo at {self.device_url}. Check it's powered on "
                                       f"and on your network, or give its address with --tablo-ip. ({first})") from None
                raise RuntimeError(f"Tablo refused the request in --offline mode ({first}). It may need a "
                                   f"recent internet check-in: try once without --offline.") from None
            # The Tablo may have a new IP address or need a fresh cloud session: refresh if we can.
            try:
                old_url = self.device_url
                self.connect()
                if self.device_url != old_url:
                    log.warning(f"Tablo address changed: {old_url} -> {self.device_url}")
            except NetworkError:
                if isinstance(first, NetworkError):
                    raise RuntimeError(f"Can't reach your Tablo at {self.device_url}. Check it's powered on "
                                       f"and on your home network. ({first})") from None
                raise RuntimeError(f"Tablo refused the request ({first}), and the internet isn't "
                                   f"available to refresh the session.") from None
            log.warning(f"Watch failed ({first}); retrying after refreshing the Tablo session...")
            return self._watch(ident)


# ----------------------------------------------------------------------
# Favorites (stored as channel identifiers)
# ----------------------------------------------------------------------
class Favorites:
    """The 'favorites' line in tablo.ini, e.g. favorites = 4.1, 9.1, KFOR.
    Edit it by hand, or use --add-fav / --remove-fav / +N / -N in the menu."""
    def __init__(self, cfg, tablo: Tablo):
        self.cfg, self.tablo = cfg, tablo
        t = cfg["tablo"]
        raw = t.get("favorites", "") or t.get("channels", "")
        if not raw:  # favorites line put under some other [section]?
            raw = next((cfg[sec].get("favorites", "") for sec in cfg.sections()
                        if cfg[sec].get("favorites")), "")
        log.debug(f"favorites line in tablo.ini: {raw!r}")
        self._chans, self.missing = [], []
        for tok in (x.strip() for x in re.split(r"[,;\n]", raw)):
            if not tok:
                continue
            found = [tablo.find(tok)]
            if not found[0] and " " in tok:  # "4.1 9.1 13.1" without commas
                found = [tablo.find(word) for word in tok.split()]
            for word, ch in zip([tok] if len(found) == 1 else tok.split(), found):
                if ch and ch not in self._chans:
                    self._chans.append(ch)
                elif not ch:
                    self.missing.append(word)
                    log.warning(f"Favorite '{word}' in tablo.ini not found (run --list to see channels)")
        self.ids = [c.identifier for c in self._chans]

    def channels(self):
        return list(self._chans)

    def others(self, chans):
        return [c for c in chans if c.identifier not in self.ids]

    def add(self, ch) -> bool:
        if ch.identifier in self.ids:
            return False
        self._chans.append(ch)
        self.ids.append(ch.identifier)
        return True

    def remove(self, ch) -> bool:
        if ch.identifier not in self.ids:
            return False
        self._chans = [c for c in self._chans if c.identifier != ch.identifier]
        self.ids.remove(ch.identifier)
        return True

    def save(self) -> None:
        """Write favorites back to tablo.ini as readable numbers/names.
        Entries that didn't match a channel are kept so nothing you typed is lost."""
        tokens = [f"{c.major}.{c.minor}" if c.major else c.call_sign for c in self._chans]
        self.cfg["tablo"]["favorites"] = ", ".join(tokens + self.missing)
        self.cfg["tablo"].pop("channels", None)
        save_config(self.cfg)
        log.info("Favorites saved: " + (", ".join(c.display_name for c in self._chans) or "(none)"))


# ----------------------------------------------------------------------
# TV guide (XMLTV) for IPTV apps: TiviMate, UHF, iPlayTV, Kodi, Televizo...
# Full listings for local channels and favorites; "now playing" for the rest.
# ----------------------------------------------------------------------
_xmltv_cache = {"at": 0.0, "xml": b""}
_xmltv_lock = threading.Lock()


def xmltv(tablo, favs) -> bytes:
    with _xmltv_lock:
        if _xmltv_cache["xml"] and time.time() - _xmltv_cache["at"] < 1800:
            return _xmltv_cache["xml"]
        def esc(t):
            return html.escape(str(t or ""), quote=True)
        def stamp(dt):
            return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")
        now = tablo.now_playing()
        full = {c.identifier for c in tablo.ota} | set(favs.ids)
        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="tablo-vlc">']
        chans = [c for _, cs in ordered(tablo, favs) for c in cs]
        for c in chans:
            num = f"{c.major}.{c.minor}" if c.major else ""
            out.append(f'<channel id="{esc(c.identifier)}">'
                       + (f"<display-name>{esc(num + ' ' + short_name(c))}</display-name>" if num else "")
                       + f"<display-name>{esc(c.call_sign)}</display-name>"
                       + (f"<display-name>{esc(num)}</display-name>" if num else "")
                       + (f'<icon src="{esc(c.logo)}"/>' if c.logo else "") + "</channel>")
        for c in chans:
            items = tablo.airings(c.identifier) if c.identifier in full else []
            if not items and now.get(c.identifier):
                items = [now[c.identifier]]
            for a in items:
                if not (a["_start"] and a["_end"]):
                    continue
                out.append(f'<programme start="{stamp(a["_start"])}" stop="{stamp(a["_end"])}" channel="{esc(c.identifier)}">'
                           f"<title>{esc(a['show'])}</title>"
                           + (f"<sub-title>{esc(a['episode'])}</sub-title>" if a["episode"] else "")
                           + (f"<desc>{esc(a['description'])}</desc>" if a["description"] else "")
                           + "".join(f"<category>{esc(g.strip())}</category>" for g in a["genres"].split(",") if g.strip())
                           + (f'<episode-num system="onscreen">{esc(a["se"])}</episode-num>' if a["se"] else "")
                           + (f"<rating><value>{esc(a['rating'])}</value></rating>" if a["rating"] else "")
                           + "</programme>")
        out.append("</tv>")
        _xmltv_cache.update(at=time.time(), xml="\n".join(out).encode())
        log.debug(f"Built TV guide: {len(chans)} channels")
        return _xmltv_cache["xml"]


def tv_m3u(tablo, favs, host: str, favorites_only: bool = False) -> bytes:
    """Playlist for TV/IPTV apps: converted H.264 streams + the XMLTV guide."""
    now = tablo.now_playing()
    lines = [f'#EXTM3U url-tvg="http://{host}/guide.xml" x-tvg-url="http://{host}/guide.xml"']
    for group, chans in ordered(tablo, favs):
        if favorites_only and group != "Favorites":
            continue
        for c in chans:
            num = f"{c.major}.{c.minor}" if c.major else ""
            a = now.get(c.identifier)
            title = c.display_name + (f" · {a['show']}" if a else "")
            attrs = (f'tvg-id="{c.identifier}" tvg-name="{c.call_sign}"' + (f' tvg-chno="{num}"' if num else "")
                     + (f' tvg-logo="{c.logo}"' if c.logo else "") + f' group-title="{group}"')
            lines += [f"#EXTINF:-1 {attrs},{title}", f"http://{host}/live/{urllib.parse.quote(c.identifier)}/{TV_QUALITY}/index.m3u8"]
    return ("\n".join(lines) + "\n").encode()


# ----------------------------------------------------------------------
# Browser playback (iPhone Safari, Android Chrome, any browser)
# The Tablo 4th Gen sends the raw broadcast (usually MPEG-2 video), which phone
# browsers can't play. ffmpeg on this PC converts it on the fly to H.264/AAC HLS.
# ----------------------------------------------------------------------
HLS_JS_URL = "https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"
HLS_JS_FILE = Path(__file__).resolve().parent / "tablo-hls.min.js"
BROWSER_QUALITY = 720        # phones/tablets: max picture height (tablo.ini browser_quality)
TV_QUALITY = 1080            # TVs and IPTV apps: max picture height (tablo.ini tv_quality)
LAN_BASE = ""                # "http://<this PC's IP>:<port>" when --lan is on (for Cast)
QUALITIES = (360, 480, 540, 720, 1080)


def ensure_hls_js() -> None:
    """Keep a local copy of hls.js (needed by Android/desktop browsers) so the page
    also works when the internet is down."""
    if HLS_JS_FILE.exists():
        return
    try:
        req = urllib.request.Request(HLS_JS_URL, headers={"User-Agent": "tablo-vlc"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        if b"Hls" in data:
            HLS_JS_FILE.write_bytes(data)
            log.debug(f"Saved {HLS_JS_FILE.name} for browser playback")
    except Exception as e:
        log.debug(f"Couldn't download hls.js: {e}")


class LiveJob:
    def __init__(self, ident, q, label, token, folder, proc):
        self.ident, self.q, self.label, self.token, self.folder, self.proc = ident, q, label, token, folder, proc
        self.last = time.time()
        self.viewers: dict[str, float] = {}


class LiveTV:
    """One ffmpeg converter per channel, shared by everyone watching it in a browser.
    Stopped (and its Tablo tuner freed) ~45 s after the last viewer goes away, or
    as soon as that viewer switches channels."""
    IDLE = 45

    def __init__(self, tablo):
        self.tablo = tablo
        self.jobs: dict[tuple, LiveJob] = {}       # (channel id, height) -> converter
        self.viewer_ch: dict[str, tuple] = {}
        self.lock = threading.Lock()
        threading.Thread(target=self._reaper, daemon=True).start()

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("ffmpeg"))

    def _start(self, ident, q) -> LiveJob:
        ch = self.tablo.by_id(ident)
        url, token = self.tablo.watch_session(ident)
        folder = Path(tempfile.mkdtemp(prefix="tablo-live-"))
        height = max(240, min(int(q), 2160))
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
               "-i", url, "-map", "0:v:0", "-map", "0:a:0?",
               "-vf", f"yadif=deint=interlaced,scale=-2:'min({height},ih)'",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-maxrate", "5M", "-bufsize", "10M",
               "-pix_fmt", "yuv420p", "-profile:v", "high", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
               "-c:a", "aac", "-b:a", "160k", "-ac", "2",
               "-f", "hls", "-hls_time", "2", "-hls_list_size", "8",
               "-hls_flags", "delete_segments+independent_segments+omit_endlist",
               "-hls_segment_filename", str(folder / "seg%05d.ts"), str(folder / "index.m3u8")]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        label = ch.display_name if ch else ident
        threading.Thread(target=lambda: [log.warning(f"[{label}] browser ffmpeg: {l.decode(errors='replace').rstrip()}")
                                         for l in proc.stderr], daemon=True).start()
        log.info(f"[{label}] converting for browser playback ({height}p)")
        return LiveJob(ident, q, label, token, folder, proc)

    def _stop(self, key) -> None:
        job = self.jobs.pop(key, None)
        if not job:
            return
        if job.proc.poll() is None:
            job.proc.terminate()
            try:
                job.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                job.proc.kill()
        self.tablo.end_session(job.token)
        shutil.rmtree(job.folder, ignore_errors=True)
        log.info(f"[{job.label}] browser playback stopped")

    def touch(self, ident, viewer, q=None) -> LiveJob:
        """Viewer is watching ident: start the converter if needed; stop the one
        they just left if nobody else is on it."""
        key = (ident, int(q or BROWSER_QUALITY))
        with self.lock:
            prev = self.viewer_ch.get(viewer)
            self.viewer_ch[viewer] = key
            job = self.jobs.get(key)
            if job and job.proc.poll() is not None:     # ffmpeg died: restart
                self._stop(key)
                job = None
            if not job:
                job = self.jobs[key] = self._start(*key)
            now = time.time()
            job.last = now
            job.viewers[viewer] = now
            if prev and prev != key and prev in self.jobs:
                old = self.jobs[prev]
                old.viewers.pop(viewer, None)
                if not any(now - t < 15 for t in old.viewers.values()):
                    self._stop(prev)
            return job

    def get(self, ident, q=None):
        return self.jobs.get((ident, int(q or BROWSER_QUALITY)))

    def _reaper(self) -> None:
        while True:
            time.sleep(10)
            with self.lock:
                for key in [k for k, j in self.jobs.items() if time.time() - j.last > self.IDLE]:
                    self._stop(key)

    def stop_all(self) -> None:
        with self.lock:
            for key in list(self.jobs):
                self._stop(key)


# ----------------------------------------------------------------------
# Channel page
# ----------------------------------------------------------------------
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Tablo TV"><meta name="theme-color" content="#16181c">
<title>Tablo Channels</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mute:#666;--line:#e3e3e3;--acc:#e8590c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#eee;--mute:#9a9a9a;--line:#2c2f36}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;margin:0 auto;max-width:1600px;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:24px 0 6px;color:var(--mute);text-transform:uppercase;letter-spacing:.05em}}
p{{color:var(--mute);margin:0 0 12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));column-gap:24px}}
.row{{display:flex;align-items:center;gap:10px;padding:6px 4px;border-bottom:1px solid var(--line);min-width:0}}
.num{{font-variant-numeric:tabular-nums;min-width:52px;font-weight:600}}
.name{{flex:1;min-width:0;display:flex;flex-direction:column}} .net{{color:var(--mute);font-size:13px}}
.ch,.now{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.now{{font-size:13px;color:var(--mute);cursor:help}} .left{{opacity:.8}}
a{{color:var(--acc);text-decoration:none}}
.bar{{position:sticky;bottom:0;background:var(--bg);padding:12px 0;display:flex;gap:12px;flex-wrap:wrap;align-items:center;border-top:1px solid var(--line)}}
button{{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:15px;cursor:pointer}}
#player{{display:none;position:sticky;top:0;z-index:5;background:var(--bg);padding:0 0 10px;border-bottom:1px solid var(--line)}}
#player video{{width:100%;max-height:62vh;background:#000;border-radius:8px;display:block}}
#player .ph{{display:flex;align-items:center;gap:10px;padding:6px 0}} #ptitle{{flex:1;font-weight:600}}
#pstate{{color:var(--mute);font-size:13px}} #pclose{{padding:6px 12px;font-size:14px}}
.saved{{color:var(--acc)}} .warn{{border:1px solid var(--acc);border-radius:8px;padding:8px 12px;margin:8px 0;color:var(--fg)}} .small{{font-size:13px;margin-top:8px}}
</style></head><body>
<div id="player"><div class="ph"><span id="ptitle"></span><span id="pstate"></span>
<google-cast-launcher id="castbtn" style="display:none;width:30px;height:30px;--connected-color:#e8590c;cursor:pointer"></google-cast-launcher>
<button id="pclose" type="button">Close</button></div><video id="video" controls playsinline autoplay x-webkit-airplay="allow"></video></div>
<h1>Tablo Channels</h1>
<p>Click Play to watch. Hover over a show for its description. Favorites come from the <b>favorites</b> line in tablo.ini.</p>
<h2>Favorites</h2>{missing}{fav}
<h2>Local (OTA)</h2>{ota}
<h2>FAST (online)</h2>{ott}
<div class="bar">
<a href="#" class="play pconly" data-id="all">Open all (VLC)</a>
<a href="#" class="play pconly" data-id="favorites">Open favorites (VLC)</a>
<span class="saved" id="msg"></span></div>
<p class="small">{player_note} VLC app on other devices: <a href="/playlist.m3u">all .m3u</a> &middot; <a href="/favorites.m3u">favorites .m3u</a></p>
<script>window.TABLO = {tablo_cfg};</script>
<script src="/hls.min.js"></script>
{cast_tag}<script>{script}</script>
</body></html>"""

# ----------------------------------------------------------------------
# TV page (/tv): Fire TV Silk, Samsung TV browser, Google TV browsers, LG...
# Big tiles, remote-control arrow keys, full-screen player, channel up/down.
# ----------------------------------------------------------------------
TV_PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tablo TV</title>
<style>
html,body{margin:0;background:#0f1115;color:#eef0f4;font:22px/1.3 system-ui,"Samsung One",Roboto,sans-serif}
body{padding:28px 48px 60px} h1{font-size:34px;margin:0 0 6px} .sub{color:#9aa3b2;font-size:18px;margin:0 0 18px}
h2{font-size:20px;letter-spacing:.08em;text-transform:uppercase;color:#9aa3b2;margin:26px 0 10px}
.fav h2{color:#87d7ff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.tile{all:unset;box-sizing:border-box;display:block;background:#1b1f27;border-radius:14px;padding:16px 18px;
  border:3px solid transparent;cursor:pointer;min-height:104px;overflow:hidden}
.tile:focus,.tile:hover{border-color:#87d7ff;background:#243041;transform:scale(1.03);outline:none}
.fav .tile{background:#1a2836}
.num{font-weight:700;font-size:26px;color:#87d7ff;margin-right:10px}
.name{font-weight:600;font-size:24px} .show{color:#c5ccd8;font-size:19px;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.left{color:#8b93a1;font-size:16px}
.warn{background:#3a2f12;color:#ffe08a;border-radius:10px;padding:12px 16px;margin:10px 0;font-size:19px}
#ov{display:none;position:fixed;inset:0;background:#000;z-index:10}
#ov video{width:100%;height:100%;background:#000}
#info{position:absolute;left:0;right:0;bottom:0;padding:28px 48px;background:linear-gradient(transparent,rgba(0,0,0,.85));
  transition:opacity .5s} #info.hide{opacity:0}
#ititle{font-size:32px;font-weight:700} #ishow{font-size:24px;color:#d5dbe6;margin-top:4px}
#istate{font-size:20px;color:#87d7ff;margin-top:6px} .keys{font-size:16px;color:#9aa3b2;margin-top:10px}
</style></head><body>
<h1>Tablo TV</h1><p class="sub">Use the arrow keys and OK to pick a channel. While watching: ▲▼ or CH+/− change channel, OK shows info, Back returns here.</p>
%%NOTICE%%
%%SECTIONS%%
<div id="ov"><video id="v" playsinline autoplay x-webkit-airplay="allow"></video>
<div id="info"><div id="ititle"></div><div id="ishow"></div><div id="istate"></div>
<div class="keys">▲▼ / CH+ CH− change channel · OK info · Back close</div></div></div>
<script src="/hls.min.js"></script>
<script>
const TVQ = %%TVQ%%;
const tiles = [...document.querySelectorAll(".tile")];
const ov = document.getElementById("ov"), v = document.getElementById("v"), info = document.getElementById("info");
const ititle = document.getElementById("ititle"), ishow = document.getElementById("ishow"), istate = document.getElementById("istate");
let hls = null, cur = -1, infoTimer = null;

function showInfo(ms) { info.classList.remove("hide"); clearTimeout(infoTimer); infoTimer = setTimeout(() => info.classList.add("hide"), ms || 5000); }
function stop() { if (hls) { hls.destroy(); hls = null; } v.pause(); v.removeAttribute("src"); v.load(); }
function play(i) {
  if (i < 0 || i >= tiles.length) return;
  cur = i; const t = tiles[i];
  stop();
  if (ov.style.display !== "block") { ov.style.display = "block"; history.pushState({tv: 1}, ""); 
    try { (document.documentElement.requestFullscreen || document.documentElement.webkitRequestFullscreen || function(){}).call(document.documentElement); } catch (e) {} }
  ititle.textContent = t.dataset.label; ishow.textContent = t.dataset.show || ""; istate.textContent = "Tuning…"; showInfo(8000);
  const src = "/live/" + encodeURIComponent(t.dataset.id) + "/" + TVQ + "/index.m3u8";
  if (v.canPlayType("application/vnd.apple.mpegurl")) { v.src = src; }
  else if (window.Hls && Hls.isSupported()) {
    hls = new Hls({liveSyncDurationCount: 3, manifestLoadingMaxRetry: 6, manifestLoadingRetryDelay: 1500});
    hls.on(Hls.Events.ERROR, (e, d) => { if (d.fatal) { istate.textContent = "Playback error (" + d.details + ")"; showInfo(10000); } });
    hls.loadSource(src); hls.attachMedia(v);
  } else { istate.textContent = "This TV browser can't play live video."; return; }
  v.play().catch(() => { istate.textContent = "Press OK / Play to start"; });
}
function close() { stop(); ov.style.display = "none"; try { document.exitFullscreen && document.fullscreenElement && document.exitFullscreen(); } catch (e) {} if (cur >= 0) tiles[cur].focus(); }
v.addEventListener("playing", () => { istate.textContent = ""; showInfo(4000); });
v.addEventListener("waiting", () => { istate.textContent = "Buffering…"; });
window.addEventListener("popstate", () => { if (ov.style.display === "block") close(); });
tiles.forEach((t, i) => t.addEventListener("click", () => play(i)));

// Arrow-key navigation between tiles (remote controls)
function move(dx, dy) {
  const a = document.activeElement, ar = (a && a.classList.contains("tile")) ? a.getBoundingClientRect() : null;
  if (!ar) { tiles[0] && tiles[0].focus(); return; }
  const ax = ar.left + ar.width / 2, ay = ar.top + ar.height / 2;
  let best = null, bestScore = 1e9;
  for (const t of tiles) {
    if (t === a) continue;
    const r = t.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    const ddx = x - ax, ddy = y - ay;
    if ((dx && Math.sign(ddx) !== dx) || (dy && Math.sign(ddy) !== dy)) continue;
    if (dx && Math.abs(ddx) < 5) continue;
    if (dy && Math.abs(ddy) < 5) continue;
    const score = dx ? Math.abs(ddx) + 3 * Math.abs(ddy) : Math.abs(ddy) + 3 * Math.abs(ddx);
    if (score < bestScore) { bestScore = score; best = t; }
  }
  if (best) { best.focus(); best.scrollIntoView({block: "nearest", behavior: "smooth"}); }
}
document.addEventListener("keydown", e => {
  const k = e.key, c = e.keyCode, watching = ov.style.display === "block";
  const back = k === "Escape" || k === "Backspace" || k === "GoBack" || c === 10009 || c === 461;
  if (watching) {
    if (back) { e.preventDefault(); history.back(); return; }
    if (k === "ArrowUp" || k === "PageUp" || k === "ChannelUp" || c === 427) { e.preventDefault(); play((cur - 1 + tiles.length) % tiles.length); return; }
    if (k === "ArrowDown" || k === "PageDown" || k === "ChannelDown" || c === 428) { e.preventDefault(); play((cur + 1) % tiles.length); return; }
    if (k === "Enter" || k === " " || k === "MediaPlayPause") { e.preventDefault(); if (v.paused) v.play(); showInfo(); return; }
    return;
  }
  const dirs = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
  if (dirs[k]) { e.preventDefault(); move(...dirs[k]); }
});
if (tiles.length) tiles[0].focus();
setInterval(() => { if (ov.style.display !== "block") location.reload(); }, 10 * 60 * 1000);  // refresh what's on
</script></body></html>"""


def render_tv_page(tablo, favs) -> bytes:
    now = tablo.now_playing()
    def tile(ch):
        num = f"{ch.major}.{ch.minor}" if ch.major else ""
        a = now.get(ch.identifier)
        show = airing_line(a) if a else ""
        left = time_left(a) if a else ""
        return (f'<button class="tile" data-id="{html.escape(ch.identifier)}" data-label="{html.escape(ch.display_name)}" '
                f'data-show="{html.escape(show)}"><span class="num">{num}</span><span class="name">{html.escape(short_name(ch))}</span>'
                f'<div class="show">{html.escape(show) or "&nbsp;"} <span class="left">{html.escape(left)}</span></div></button>')
    sections = []
    for title, chans in ordered(tablo, favs):
        if not chans or (title.startswith("FAST") and tablo.lan_only):
            continue
        cls = ' class="fav"' if title == "Favorites" else ""
        sections.append(f'<section{cls}><h2>{"★ " if cls else ""}{title}</h2><div class="grid">'
                        + "".join(tile(c) for c in chans) + "</div></section>")
    notice = ""
    if not LiveTV.available():
        notice = '<div class="warn">To watch on a TV, install ffmpeg on the PC running tablo-vlc.</div>'
    elif tablo.lan_only or not tablo.online:
        notice = '<div class="warn">No internet: local channels only.</div>'
    return (TV_PAGE.replace("%%SECTIONS%%", "".join(sections)).replace("%%NOTICE%%", notice)
            .replace("%%TVQ%%", str(TV_QUALITY))).encode()


PAGE_SCRIPT = r"""
// Smart TVs and streaming sticks get the remote-friendly TV page.
if (/SMART-TV|SmartTV|Tizen|Web0S|webOS|NetCast|BRAVIA|AFT[A-Z]|\bSilk\b|GoogleTV|Google TV|Android TV|CrKey|AppleTV/i.test(navigator.userAgent)
    && !location.search.includes("full")) location.replace("/tv");
const IS_PC = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);
const msg = document.getElementById("msg");
const player = document.getElementById("player"), video = document.getElementById("video");
const ptitle = document.getElementById("ptitle"), pstate = document.getElementById("pstate");
let hls = null, current = null;
if (!IS_PC) document.querySelectorAll(".pconly").forEach(e => e.style.display = "none");

// ---- Google Cast (Chrome on Android/PC -> Google TV, onn, Chromecast) ----
function castSession() { try { return cast.framework.CastContext.getInstance().getCurrentSession(); } catch (e) { return null; } }
function castLoad(c) {
  const s = castSession(); if (!s || !c) return false;
  const mi = new chrome.cast.media.MediaInfo(TABLO.lan + "/live/" + encodeURIComponent(c.id) + "/" + TABLO.tvq + "/index.m3u8",
                                             "application/x-mpegurl");
  mi.streamType = chrome.cast.media.StreamType.LIVE;
  mi.hlsSegmentFormat = chrome.cast.media.HlsSegmentFormat.TS;
  if (chrome.cast.media.HlsVideoSegmentFormat) mi.hlsVideoSegmentFormat = chrome.cast.media.HlsVideoSegmentFormat.MPEG2_TS;
  mi.metadata = new chrome.cast.media.GenericMediaMetadata(); mi.metadata.title = c.label;
  stopVideo(); player.style.display = "block"; ptitle.textContent = c.label;
  pstate.textContent = "Casting to " + s.getCastDevice().friendlyName + "…";
  s.loadMedia(new chrome.cast.media.LoadRequest(mi)).then(
    () => pstate.textContent = "Playing on " + s.getCastDevice().friendlyName,
    err => pstate.textContent = "Cast failed: " + (err && err.code || err));
  return true;
}
window.__onGCastApiAvailable = ok => {
  if (!ok || !TABLO.lan) return;
  const ctx = cast.framework.CastContext.getInstance();
  ctx.setOptions({receiverApplicationId: chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
                  autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED});
  document.getElementById("castbtn").style.display = "inline-block";
  ctx.addEventListener(cast.framework.CastContextEventType.SESSION_STATE_CHANGED, e => {
    if ((e.sessionState === "SESSION_STARTED" || e.sessionState === "SESSION_RESUMED") && current) castLoad(current);
  });
};

function stopVideo() {
  if (hls) { hls.destroy(); hls = null; }
  video.pause(); video.removeAttribute("src"); video.load();
}
// Watch right here in the browser (phones, tablets, or a PC with no player).
function watchHere(id, label) {
  current = {id: id, label: label};
  if (castSession() && castLoad(current)) return;      // already casting: send it to the TV
  stopVideo();
  player.style.display = "block"; window.scrollTo(0, 0);
  ptitle.textContent = label; pstate.textContent = "Tuning… (takes a few seconds)";
  const src = "/live/" + encodeURIComponent(id) + "/index.m3u8";
  if (video.canPlayType("application/vnd.apple.mpegurl")) {          // iPhone/iPad Safari
    video.src = src;
  } else if (window.Hls && Hls.isSupported()) {                        // Android Chrome, desktop
    hls = new Hls({liveSyncDurationCount: 3, manifestLoadingMaxRetry: 6, manifestLoadingRetryDelay: 1500});
    hls.on(Hls.Events.ERROR, (e, d) => { if (d.fatal) pstate.textContent = "Playback error (" + d.details + ")"; });
    hls.loadSource(src); hls.attachMedia(video);
  } else {
    pstate.textContent = "This browser can't play live video. Try Safari or Chrome.";
    return;
  }
  video.play().catch(() => { pstate.textContent = "Tap ▶ to start"; });
}
video.addEventListener("playing", () => pstate.textContent = "");
video.addEventListener("waiting", () => pstate.textContent = "Buffering…");
document.getElementById("pclose").onclick = () => { stopVideo(); player.style.display = "none"; };

document.querySelectorAll("a.play").forEach(a => a.addEventListener("click", async e => {
  e.preventDefault();
  const id = a.dataset.id, label = a.dataset.label || "";
  if (!IS_PC && id !== "all" && id !== "favorites") return watchHere(id, label);
  msg.textContent = "Opening...";
  try {
    const r = await fetch("/play/" + encodeURIComponent(id), {method: "POST"});
    const text = await r.text();
    if (r.ok) { msg.textContent = text; return; }
    if (r.status === 409) { location.href = "/m3u/" + encodeURIComponent(id); return; }
    if (r.status === 500 && id !== "all" && id !== "favorites") {    // no player on this PC
      msg.textContent = ""; return watchHere(id, label);
    }
    msg.textContent = "Error: " + text;
  } catch (err) { msg.textContent = "Error: " + err; }
}));
// ?play=<id> link (e.g. a bookmark) starts that channel
const want = new URLSearchParams(location.search).get("play");
if (want) { const a = document.querySelector('a.play[data-id="' + CSS.escape(want) + '"]'); if (a) watchHere(want, a.dataset.label); }
"""


def render_page(tablo: Tablo, favs: Favorites) -> bytes:
    now = tablo.now_playing()

    def row(ch):
        num = f"{ch.major}.{ch.minor}" if ch.major else ""
        a = now.get(ch.identifier)
        info = ""
        if a:
            tip = "\n".join(x for x in (
                f"{a['start']}–{a['end']}  {airing_line(a)}",
                " · ".join(x for x in (a["kind"], a["genres"], a["rating"]) if x),
                a["description"]) if x)
            left = time_left(a)
            info = (f'<span class="now" title="{html.escape(tip)}">{html.escape(airing_line(a))}'
                    + (f' <span class="left">· {html.escape(left)}</span>' if left else "") + "</span>")
        return (f'<div class="row"><span class="num">{num}</span>'
                f'<span class="name"><span class="ch" title="{html.escape(ch.call_sign)}">{html.escape(ch.call_sign)} '
                f'<span class="net">{html.escape(ch.network)}</span></span>{info}</span>'
                f'<a href="#" class="play" data-id="{html.escape(ch.identifier)}" '
                f'data-label="{html.escape(ch.display_name)}">Play</a></div>')

    def section(chans, empty):
        if not chans:
            return f"<p>{empty}</p>"
        return '<div class="grid">' + "".join(row(c) for c in chans) + "</div>"

    missing = ""
    if tablo.lan_only:
        missing += ('<p class="warn">LAN-only mode: local channels from your saved list; no internet used.</p>')
    elif not tablo.online:
        missing += ('<p class="warn">Offline: using your saved channel list. Local channels still play; '
                    'FAST channels and the guide come back when the internet does.</p>')
    if favs.missing:
        missing += ('<p class="warn">Not found from tablo.ini: <b>'
                   + html.escape(", ".join(favs.missing))
                   + "</b>. Use the numbers or call signs shown below.</p>")
    note = ("On a phone or tablet, Play watches right in the browser"
            + (" (and can Cast/AirPlay to a TV)." if LAN_BASE else ".") if LiveTV.available()
            else "To watch in a phone/tablet browser, install ffmpeg on this PC.")
    if LAN_BASE and LiveTV.available():
        note += (f' TVs: <a href="/tv">TV page</a> &middot; IPTV apps: <a href="/tv.m3u">tv.m3u</a> + '
                 f'<a href="/guide.xml">guide.xml</a>.')
    cfg = json.dumps({"lan": LAN_BASE, "tvq": TV_QUALITY})
    cast_tag = ('<script src="https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1"></script>'
                if LAN_BASE and LiveTV.available() and tablo.online else "")
    return PAGE.format(
        script=PAGE_SCRIPT, player_note=note, tablo_cfg=cfg, cast_tag=cast_tag,
        missing=missing,
        fav=section(favs.channels(), "None set. Add e.g. <code>favorites = 4.1, 9.1</code> to tablo.ini."),
        ota=section(favs.others(tablo.ota), "None found."),
        ott=section(favs.others(tablo.ott), "None found."),
    ).encode()


# ----------------------------------------------------------------------
# HTTP server
# ----------------------------------------------------------------------
def make_handler(tablo: Tablo, favs: Favorites, port: int):
    live = LiveTV(tablo)
    sessions: dict[str, str] = {}      # viewer IP -> Tablo session token
    sessions_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep the console quiet

        def send_body(self, body: bytes, ctype: str, code: int = 200, cors: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self.send_body(render_page(tablo, favs),
                                      "text/html; charset=utf-8")
            if path == "/playlist.m3u":
                return self.playlist(all_channels=True)
            if path == "/favorites.m3u":
                return self.playlist(all_channels=False)
            if path.startswith("/ch/"):
                return self.stream(path[4:])
            if path.startswith("/m3u/"):
                return self.single_m3u(urllib.parse.unquote(path[5:]))
            if path.startswith("/live/"):
                return self.live(path)
            if path in ("/tv.m3u", "/tv-favorites.m3u"):
                if not LiveTV.available():
                    return self.send_body(b"Install ffmpeg on the PC for TV playlists.", "text/plain", 501)
                host = self.headers.get("Host", f"127.0.0.1:{port}")
                return self.send_body(tv_m3u(tablo, favs, host, path == "/tv-favorites.m3u"),
                                      "audio/x-mpegurl", cors=True)
            if path in ("/tv", "/tv/"):
                return self.send_body(render_tv_page(tablo, favs), "text/html; charset=utf-8")
            if path == "/guide.xml":
                return self.send_body(xmltv(tablo, favs), "application/xml; charset=utf-8", cors=True)
            if path == "/hls.min.js":
                if HLS_JS_FILE.exists():
                    return self.send_body(HLS_JS_FILE.read_bytes(), "application/javascript")
                self.send_response(302); self.send_header("Location", HLS_JS_URL); self.end_headers()
                return
            self.send_error(404)

        def live(self, path: str):
            """/live/<id>/index.m3u8 and /live/<id>/segNNNNN.ts for browser playback."""
            parts = path.split("/")
            if len(parts) == 4:                       # /live/<id>/<file>  (phone quality)
                q = BROWSER_QUALITY
            elif len(parts) == 5 and parts[3].isdigit() and int(parts[3]) in QUALITIES:
                q = int(parts[3])                     # /live/<id>/<height>/<file>
            else:
                return self.send_error(404)
            ident, name = urllib.parse.unquote(parts[2]), parts[-1]
            ch = tablo.by_id(ident)
            if not ch:
                return self.send_error(404, "Unknown channel")
            if not LiveTV.available():
                return self.send_body(b"Install ffmpeg on the PC to watch in a browser.", "text/plain", 501)
            viewer = self.client_address[0]
            if name == "index.m3u8":
                if ch.kind != "ota" and not tablo.maybe_reconnect(min_gap=15):
                    return self.send_body(b"FAST channels need the internet.", "text/plain", 503)
                try:
                    job = live.touch(ident, viewer, q)
                except Exception as e:
                    log.error(f"[{ch.display_name}] could not tune for browser: {e}")
                    return self.send_body(b"Tablo could not tune (all tuners busy?)", "text/plain", 503)
                playlist = job.folder / "index.m3u8"
                for _ in range(100):                      # first segments take a few seconds
                    if playlist.exists() and b".ts" in playlist.read_bytes():
                        break
                    if job.proc.poll() is not None:
                        return self.send_body(b"Converter stopped; check tablo-vlc.log", "text/plain", 503)
                    time.sleep(0.25)
                else:
                    return self.send_body(b"Still starting; try again", "text/plain", 503)
                return self.send_body(playlist.read_bytes(), "application/vnd.apple.mpegurl", cors=True)
            if re.fullmatch(r"seg\d+\.ts", name):
                job = live.get(ident, q)
                seg = job.folder / name if job else None
                if not seg or not seg.exists():
                    return self.send_error(404)
                job.last = time.time()
                job.viewers[viewer] = job.last
                return self.send_body(seg.read_bytes(), "video/mp2t", cors=True)
            self.send_error(404)

        def target_url(self, ident: str, host: str):
            if ident == "all":
                return f"http://{host}/playlist.m3u", "all channels"
            if ident == "favorites":
                return f"http://{host}/favorites.m3u", "favorites"
            ch = tablo.by_id(ident)
            return (f"http://{host}/ch/{ident}", ch.display_name) if ch else (None, None)

        def play(self, ident: str):
            """Open the channel in VLC on this PC (only for requests from this PC)."""
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self.send_body(b"Open the .m3u on that device instead", "text/plain", 409)
            url, label = self.target_url(ident, f"127.0.0.1:{port}")
            if not url:
                return self.send_body(b"Unknown channel", "text/plain", 404)
            ch = tablo.by_id(ident)
            meta = vlc_meta(ch, tablo.now_playing().get(ident)) if ch else None
            if not launch_player(url, meta, playlist=ident in ("all", "favorites")):
                why = ("ffplay can't open a channel list; pick a single channel." if PLAYER
                       else "No video player found. Install VLC or ffmpeg (ffplay).")
                return self.send_body(why.encode(), "text/plain", 500)
            self.send_body(f"Opening {label} in {player_name()}".encode(), "text/plain")

        def single_m3u(self, ident: str):
            host = self.headers.get("Host", f"127.0.0.1:{port}")
            url, label = self.target_url(ident, host)
            if not url:
                return self.send_error(404)
            if ident in ("all", "favorites"):
                self.send_response(302); self.send_header("Location", url); self.end_headers()
                return
            ch = tablo.by_id(ident)
            body = ("\n".join(["#EXTM3U", *m3u_entry(ch, tablo.now_playing().get(ident), url, "")]) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "audio/x-mpegurl")
            self.send_header("Content-Disposition", f'inline; filename="{ident}.m3u"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.startswith("/play/"):
                return self.play(urllib.parse.unquote(self.path[6:]))
            self.send_error(404)

        def playlist(self, all_channels: bool):
            host = self.headers.get("Host", f"127.0.0.1:{port}")
            lines = ["#EXTM3U"]

            now = tablo.now_playing()

            def add(ch, group):
                lines.extend(m3u_entry(ch, now.get(ch.identifier), f"http://{host}/ch/{ch.identifier}", group))

            for ch in favs.channels():
                add(ch, "Favorites")
            if all_channels:
                for ch in favs.others(tablo.ota):
                    add(ch, "Local (OTA)")
                for ch in favs.others(tablo.ott):
                    add(ch, "FAST (online)")
            self.send_body(("\n".join(lines) + "\n").encode(), "audio/x-mpegurl")

        def stream(self, ident: str):
            ch = tablo.by_id(ident)
            if not ch:
                return self.send_error(404, "Unknown channel")
            viewer = self.client_address[0]
            # Switching channels on the same device: free the old tuner first.
            with sessions_lock:
                old = sessions.pop(viewer, "")
            if old:
                tablo.end_session(old)
            try:
                url, token = tablo.watch_session(ident)
            except Exception as e:
                log.error(f"[{ch.display_name}] could not tune: {e}")
                return self.send_error(503, "Tablo could not tune (all tuners busy?)")
            with sessions_lock:
                sessions[viewer] = token

            log.debug(f"[{ch.display_name}] Tablo stream URL: {url}")
            if not USE_FFMPEG:
                # Hand VLC the Tablo's HLS URL; VLC keeps polling it, which
                # keeps the Tablo session alive while you watch.
                log.info(f"[{ch.display_name}] tuned, VLC playing directly")
                self.send_response(302)
                self.send_header("Location", url)
                self.end_headers()
                return

            log.info(f"[{ch.display_name}] tuned, streaming to {self.client_address[0]}")
            proc = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
                 "-i", url, "-c", "copy", "-f", "mpegts", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            threading.Thread(target=lambda: [log.warning(f"[{ch.display_name}] ffmpeg: {l.decode(errors='replace').rstrip()}")
                                             for l in proc.stderr], daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.end_headers()
            try:
                while True:
                    chunk = proc.stdout.read(188 * 512)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # VLC closed the channel
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                with sessions_lock:
                    mine = sessions.get(viewer) == token
                    if mine:
                        sessions.pop(viewer)
                if mine:
                    tablo.end_session(token)
                log.info(f"[{ch.display_name}] stopped")

    def end_all():
        live.stop_all()
        with sessions_lock:
            tokens = list(sessions.values())
            sessions.clear()
        for t in tokens:
            tablo.end_session(t)

    Handler.end_all_sessions = staticmethod(end_all)
    return Handler


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def cmd_setup(cfg):
    t = cfg["tablo"]
    t["email"] = input(f"Tablo account email [{t.get('email', '')}]: ").strip() or t.get("email", "")
    pw = getpass.getpass("Tablo password (blank = keep current): ")
    if pw:
        t["password"] = pw
    t.setdefault("port", str(DEFAULT_PORT))
    save_config(cfg)
    try:
        Tablo(t["email"], t["password"])  # verify login works
    except Exception as e:
        log.error(f"Saved your login, but could not connect to Tablo: {e}")
        sys.exit(1)
    print(f"\nSaved to {CONFIG_FILE}.")


def print_vlc_commands(tablo: "Tablo", port: int) -> None:
    """One VLC command per local OTA channel, via this script's server."""
    lines = [f"VLC commands for local OTA channels (this script must be running):"]
    for ch in tablo.ota:
        label = f"{ch.display_name}" + (f" ({ch.network})" if ch.network else "")
        lines.append(f"  {label:<28} vlc http://127.0.0.1:{port}/ch/{ch.identifier}")
    lines.append(f"  {'All local channels':<28} vlc http://127.0.0.1:{port}/playlist.m3u")
    for line in lines:
        log.info(line)


def m3u_entry(ch, a, url: str, group: str) -> list:
    """#EXTINF line (title includes what's on now) + VLC options for Media Information."""
    meta = vlc_meta(ch, a)
    attrs = f'tvg-id="{ch.identifier}"' + (f' tvg-logo="{ch.logo}"' if ch.logo else "") \
        + (f' group-title="{group}"' if group else "")
    lines = [f"#EXTINF:-1 {attrs},{meta['meta-title']}"]
    lines += [f"#EXTVLCOPT:{k}={v}" for k, v in {**meta, **vlc_tuning()}.items() if k != "meta-title"]
    return lines + [url]


PLAYER: dict | None = None   # set in main() by resolve_player()
VLC_BUFFER_MS = 3000         # VLC network buffer for live TV (tablo.ini vlc_buffer / --buffer)
DEINTERLACE = "yadif2x"      # VLC deinterlace mode for interlaced broadcasts; "off" = none
_last_player_proc = None     # ffplay: close the old window when switching channels


FLATPAK_VLC_ID = "org.videolan.VLC"


def find_windows_vlc() -> list | None:
    """VLC on Windows: PATH, the registry, then the usual install folders."""
    if not IS_WINDOWS:
        return None
    if shutil.which("vlc"):
        return [shutil.which("vlc")]
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key in (r"SOFTWARE\VideoLAN\VLC", r"SOFTWARE\WOW6432Node\VideoLAN\VLC"):
                try:
                    with winreg.OpenKey(root, key) as k:
                        exe = Path(winreg.QueryValueEx(k, "InstallDir")[0]) / "vlc.exe"
                        if exe.is_file():
                            return [str(exe)]
                except OSError:
                    pass
    except ImportError:
        pass
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            for sub in (r"VideoLAN\VLC\vlc.exe", r"Programs\VideoLAN\VLC\vlc.exe"):
                exe = Path(base) / sub
                if exe.is_file():
                    return [str(exe)]
    return None


def split_command(setting: str) -> list:
    """'player =' value -> argv. Handles Windows paths with spaces/backslashes."""
    if Path(setting.strip('"')).is_file():          # a bare path, even with spaces
        return [setting.strip('"')]
    if IS_WINDOWS:
        return [p.strip('"') for p in shlex.split(setting, posix=False)]
    return shlex.split(setting)


def find_flatpak_vlc() -> list | None:
    """VLC installed from Flathub (e.g. Pop!_OS): its launcher, or 'flatpak run'."""
    for p in (Path.home() / ".local/share/flatpak/exports/bin" / FLATPAK_VLC_ID,
              Path("/var/lib/flatpak/exports/bin") / FLATPAK_VLC_ID):
        if p.is_file() and os.access(p, os.X_OK):
            return [str(p)]
    fp = shutil.which("flatpak")
    if fp:
        try:
            if subprocess.run([fp, "info", FLATPAK_VLC_ID], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=5).returncode == 0:
                return [fp, "run", FLATPAK_VLC_ID]
        except Exception:
            pass
    return None


def resolve_player(setting: str = "") -> dict | None:
    """Which video player to use.
    tablo.ini 'player =' / --player wins: vlc, flatpak (Flatpak VLC), ffplay, mpv,
    or any full command. Otherwise: VLC (regular or Snap), then Flatpak VLC,
    then ffplay (comes with ffmpeg)."""
    setting = (setting or "").strip()
    if setting.lower() in ("flatpak", "flatpak-vlc", "vlc-flatpak"):
        cmd = find_flatpak_vlc()
        if cmd:
            return {"name": "VLC (Flatpak)", "cmd": cmd, "kind": "vlc"}
        log.warning(f"player = {setting}, but Flatpak VLC isn't installed "
                    f"(flatpak install flathub {FLATPAK_VLC_ID}); falling back.")
    elif setting.lower() == "vlc" and find_windows_vlc():
        return {"name": "VLC", "cmd": find_windows_vlc(), "kind": "vlc"}
    elif setting.lower() == "vlc" and not shutil.which("vlc") and find_flatpak_vlc():
        return {"name": "VLC (Flatpak)", "cmd": find_flatpak_vlc(), "kind": "vlc"}
    elif setting:
        cmd = split_command(setting)
        exe = shutil.which(cmd[0])
        if not exe:
            log.warning(f"player = {setting}, but '{cmd[0]}' isn't installed; falling back to VLC/ffplay.")
        else:
            base = Path(cmd[0]).name.lower().removesuffix(".exe")
            kind = ("vlc" if "vlc" in setting.lower() else
                    "ffplay" if base == "ffplay" else "mpv" if base == "mpv" else "other")
            name = ("VLC (Flatpak)" if kind == "vlc" and base == "flatpak" else
                    "VLC" if kind == "vlc" else base)
            return {"name": name, "cmd": [exe, *cmd[1:]], "kind": kind}
    win = find_windows_vlc()
    if win:
        return {"name": "VLC", "cmd": win, "kind": "vlc"}
    if shutil.which("vlc"):
        return {"name": "VLC", "cmd": [shutil.which("vlc")], "kind": "vlc"}
    flat = None if IS_WINDOWS else find_flatpak_vlc()
    if flat:
        return {"name": "VLC (Flatpak)", "cmd": flat, "kind": "vlc"}
    if shutil.which("ffplay"):
        return {"name": "ffplay", "cmd": [shutil.which("ffplay")], "kind": "ffplay"}
    return None


def player_name() -> str:
    return PLAYER["name"] if PLAYER else "VLC"


class VLCControl:
    """Runs one VLC window for this script and switches channels in it through
    VLC's built-in web remote (localhost only, random password): the playlist is
    replaced, not added to. Falls back to closing and reopening VLC."""

    def __init__(self):
        self.proc = None
        self.port = 0
        self.pw = ""

    def _alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _cmd(self, path: str) -> bool:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/requests/status.json?{path}")
        req.add_header("Authorization", "Basic " + __import__("base64").b64encode(f":{self.pw}".encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status == 200
        except Exception as e:
            log.debug(f"VLC remote: {e}")
            return False

    def play(self, base_cmd: list, url: str, options: dict, title: str) -> bool:
        if self._alive():
            q = urllib.parse.urlencode([("command", "in_play"), ("input", url), ("name", title or url)]
                                       + [("option", f"{k}={v}") for k, v in options.items()],
                                       quote_via=urllib.parse.quote)   # VLC wants %20, not +
            if self._cmd("command=pl_empty") and self._cmd(q):
                return True
            log.debug("VLC remote unavailable; restarting VLC")
            self.proc.terminate()
        with socket.socket() as sock:          # pick a free local port
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.pw = uuid.uuid4().hex
        cmd = [*base_cmd, "--no-one-instance", "--extraintf", "http",
               "--http-host", "127.0.0.1", "--http-port", str(self.port), "--http-password", self.pw,
               url, *[f":{k}={v}" for k, v in options.items()]]
        self.proc = spawn(cmd)
        return True


VLC = VLCControl()


def launch_player(url: str, meta: dict | None = None, playlist: bool = False, detach: bool = False) -> bool:
    """Open url in the chosen player. meta = guide info (title/description).
    VLC: one window, channel replaced on each pick. ffplay: old window closed first.
    detach=True (--play): a plain VLC launch that outlives the script."""
    global _last_player_proc
    if not PLAYER:
        log.warning("No video player found. Install VLC (sudo apt install vlc, or "
                    "flatpak install flathub org.videolan.VLC) or ffmpeg, which includes ffplay "
                    "(sudo apt install ffmpeg), or set 'player =' in tablo.ini.")
        return False
    kind, title = PLAYER["kind"], (meta or {}).get("meta-title", "")
    if playlist and kind == "ffplay":
        log.warning("ffplay can't open a channel list; pick a single channel instead "
                    "(or install VLC for playlists).")
        return False
    log.info(f"Launching: {PLAYER['name']} {url}" + (f"  [{title}]" if title else ""))
    if kind == "vlc":
        options = {**(meta or {}), **vlc_tuning()}
        if not detach:
            return VLC.play(PLAYER["cmd"], url, options, title)
        cmd = [*PLAYER["cmd"], "--one-instance", url, *[f":{k}={v}" for k, v in options.items()]]
    elif kind == "ffplay":
        cmd = [*PLAYER["cmd"], "-hide_banner", "-loglevel", "error",
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
               "-window_title", title or "Tablo", url]
        if _last_player_proc and _last_player_proc.poll() is None:
            _last_player_proc.terminate()   # one window at a time, like VLC
    elif kind == "mpv":
        cmd = [*PLAYER["cmd"], *([f"--force-media-title={title}"] if title else []), url]
    else:
        cmd = [*PLAYER["cmd"], url]
    proc = spawn(cmd)
    if kind == "ffplay":
        _last_player_proc = proc
    return True


launch_vlc = launch_player  # older name


def open_url(url: str):
    """Open a page in the default browser (Linux, Windows, macOS)."""
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception as e:
        log.warning(f"Couldn't open a browser ({e}); go to {url}")


def shutdown(server) -> None:
    """Quit: free the Tablo tuners we were using and close our VLC window."""
    try:
        server.RequestHandlerClass.end_all_sessions()
    except Exception as e:
        log.debug(f"Ending sessions: {e}")
    if VLC.proc and VLC.proc.poll() is None:
        VLC.proc.terminate()
    server.shutdown()
    log.info("Stopped.")


def ordered(tablo: "Tablo", favs: "Favorites"):
    """(section, channels) in display order: favorites, then the rest."""
    return [("Favorites", favs.channels()),
            ("Local (OTA)", favs.others(tablo.ota)),
            ("FAST (online)", favs.others(tablo.ott))]


def menu_numbers(tablo, favs):
    nums, n = {}, 0
    for _, chans in ordered(tablo, favs):
        for c in chans:
            n += 1
            nums[n] = c
    return nums


def resolve(tablo, favs, text, numbered=None):
    """'3' = menu number; '9.1', 'KFOR', 'westerns' = channel."""
    t = text.strip()
    if t.isdigit():
        return (numbered or menu_numbers(tablo, favs)).get(int(t))
    return tablo.find(t)


def split_channels(tablo, favs, items, numbered=None):
    """['4.1', 'pluto', 'news,', '9.1'] -> ['4.1', 'pluto news', '9.1']
    Commas always separate; plain words are joined into the longest name that matches."""
    tokens = [t.strip() for item in items for t in re.split(r"(,)", item)]
    groups, cur = [], []
    for t in tokens:
        if t == ",":
            groups.append(cur); cur = []
        elif t:
            cur.extend(t.split())
    groups.append(cur)
    out = []
    for words in groups:
        i = 0
        while i < len(words):
            if re.fullmatch(r"\d+([.\-]\d+)?", words[i]):
                out.append(words[i]); i += 1
                continue
            for j in range(len(words), i, -1):  # longest phrase first
                phrase = " ".join(words[i:j])
                if resolve(tablo, favs, phrase, numbered) or j == i + 1:
                    out.append(phrase); i = j
                    break
    return out


def change_favs(tablo, favs, items, add: bool, numbered=None) -> None:
    """Add/remove channels and save if anything changed."""
    # resolve everything first: menu numbers shift once favorites change
    targets = [(w, resolve(tablo, favs, w, numbered)) for w in split_channels(tablo, favs, items, numbered)]
    changed = False
    for w, ch in targets:
        if not ch:
            print(f"  '{w}' not found")
        elif add:
            if favs.add(ch):
                print(f"  + {ch.display_name}"); changed = True
            else:
                print(f"  {ch.display_name} is already a favorite")
        else:
            if favs.remove(ch):
                print(f"  - {ch.display_name}"); changed = True
            else:
                print(f"  {ch.display_name} isn't a favorite")
    if changed:
        favs.save()
        print(f"Saved to {CONFIG_FILE.name}")


COLUMNS = 0  # 0 = fit the terminal width; set by --columns
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and enable_windows_ansi()  # --no-color turns off

# ANSI styles for the terminal list
LIGHT_BLUE = "\033[1;38;5;117m"  # bold light blue (256-color terminals, which Ubuntu's are)
FAV_NUM = LIGHT_BLUE        # favorites: numbers
FAV_NAME = LIGHT_BLUE       # favorites: channel + show
HEAD = "\033[1m"           # section headings: bold
FAV_HEAD = LIGHT_BLUE       # "★ Favorites" heading
WARN = "\033[1;33m"        # OFFLINE notice: bold yellow
DIM = "\033[2m"
RESET = "\033[0m"


def paint(text: str, style: str) -> str:
    return f"{style}{text}{RESET}" if USE_COLOR and style else text


def short_name(ch) -> str:
    """KWTV-DT -> KWTV for local channels; FAST channels keep their name."""
    return re.sub(r"-(DT|HD)\d*$", "", ch.call_sign) if ch.kind == "ota" else ch.call_sign


def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(1, width - 1)] + "…"


def print_columns(cells, now=None, fav=False) -> None:
    """Print (number, channel) cells down-then-across in as many columns as fit.
    With guide info, each cell also shows what's on now."""
    now = now or {}
    width = shutil.get_terminal_size((100, 24)).columns
    fixed = 5 + 7 + 2                     # " 12) " + "702.1  " + gap between columns
    min_name, max_name = (26, 44) if now else (14, 24)
    cols = COLUMNS or max(1, (width - 2) // (fixed + min_name))
    cols = min(cols, max(1, len(cells)))
    name_w = max(min_name, min(max_name, (width - 2) // cols - fixed))

    def label(ch, w):
        a = now.get(ch.identifier)
        if not a:
            return clip(ch.call_sign, w)
        return clip(f"{short_name(ch)} · {a['show']}", w)

    if cols == 1:                         # narrow window: one per line, with more detail
        for n, ch in cells:
            num = f"{ch.major}.{ch.minor}" if ch.major else ""
            a = now.get(ch.identifier)
            extra = f"{airing_line(a)}  ({time_left(a)})" if a and time_left(a) else airing_line(a) if a else ch.network
            text = clip(f"{num:<6} {clip(short_name(ch), 18):<18} {extra}", max(width, 40) - 8)
            print(f"  {paint(f'{n:>3})', FAV_NUM if fav else DIM)}  {paint(text, FAV_NAME if fav else '')}")
        return
    rows = -(-len(cells) // cols)          # ceiling division
    for r in range(rows):
        line = []
        for c in range(cols):
            i = c * rows + r
            if i >= len(cells):
                break
            n, ch = cells[i]
            num = f"{ch.major}.{ch.minor}" if ch.major else ""
            cell = f"{num:<6} {label(ch, name_w):<{name_w}}"
            line.append(paint(f"{n:>3})", FAV_NUM if fav else DIM) + " " + paint(cell, FAV_NAME if fav else ""))
        print("  " + "  ".join(line).rstrip())


def show_info(tablo, ch) -> None:
    """Details for one channel: what's on now, description, and what's next."""
    width = min(shutil.get_terminal_size((100, 24)).columns, 100)
    num = f"{ch.major}.{ch.minor} " if ch.major else ""
    print(f"\n{num}{ch.call_sign}" + (f"  ({ch.network})" if ch.network else "")
          + ("  · FAST (online)" if ch.kind != "ota" else "  · local over-the-air"))
    a = tablo.now_playing().get(ch.identifier)
    if a:
        print(f"\n  NOW  {a['start']}–{a['end']}  {airing_line(a)}" + (f"   [{time_left(a)}]" if time_left(a) else ""))
        meta = " · ".join(x for x in (a["kind"], a["genres"], a["rating"]) if x)
        if meta:
            print(f"       {meta}")
        if a["description"]:
            for line in textwrap.wrap(a["description"], width - 9):
                print(f"       {line}")
    else:
        print("\n  No guide info for what's on now.")
    nxt = [x for x in tablo.upcoming(ch.identifier) if not a or x["start"] != a["start"]][:5]
    if not nxt:
        print("\n  No upcoming listings" + (" (offline)." if not tablo.online else " available."))
    else:
        print("\n  COMING UP")
        for x in nxt:
            print(clip(f"  {x['start']:>8}  {airing_line(x)}" + (f"  · {x['genres']}" if x["genres"] else ""), width))
    print()


def print_channels(tablo, favs, show_streaming=True, guide=True):
    """Numbered list; returns {number: channel}."""
    numbered, n = {}, 0
    now = tablo.now_playing() if guide else {}
    tablo.shown_now = now  # VLC titles reuse exactly what this list showed
    if tablo.lan_only:
        print("\n" + paint("LAN-ONLY MODE (--offline): local channels from your saved list; "
                           "no internet used.", WARN))
    elif not tablo.online:
        print("\n" + paint("OFFLINE - using your saved channel list. Local channels still play; "
                           "FAST channels and the guide return when the internet does.", WARN))
    for title, chans in ordered(tablo, favs):
        if not chans:
            if title == "Favorites":
                print("\nFavorites\n  (none - add e.g. 'favorites = 4.1, 9.1' to tablo.ini)")
            continue
        hidden = title.startswith("FAST") and not show_streaming
        if hidden:
            print(f"\n{title}: {len(chans)} channel{'s' if len(chans) != 1 else ''} (type 's' to show)")
        else:
            heading = f"★ {title}" if title == "Favorites" else title
            print("\n" + paint(heading, FAV_HEAD if title == "Favorites" else HEAD))
        cells = []
        for ch in chans:
            n += 1
            numbered[n] = ch
            cells.append((n, ch))
        if not hidden:
            print_columns(cells, now, fav=title == "Favorites")
    if favs.missing:
        print(f"\n  Not found from tablo.ini favorites: {', '.join(favs.missing)}")
    return numbered


def play_direct(tablo, ch) -> bool:
    """Tune and hand VLC the Tablo's own stream URL (no server needed)."""
    try:
        url = tablo.watch_url(ch.identifier)
    except Exception as e:
        log.error(f"[{ch.display_name}] could not tune: {e}")
        return False
    log.info(f"[{ch.display_name}] Tablo stream URL: {url}")
    return launch_player(url, vlc_meta(ch, tablo.now_playing().get(ch.identifier)), detach=True)


def lan_ip(towards: str = "") -> str:
    """This PC's address on the home network (no packets are sent)."""
    host = urllib.parse.urlsplit(towards).hostname if towards else None
    for target in (host, "192.168.1.1", "10.255.255.255"):
        if not target:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                sk.connect((target, 9))
                ip = sk.getsockname()[0]
                if not ip.startswith("127."):
                    return ip
        except OSError:
            pass
    return "<this-PC's-IP>"


def start_server(tablo, favs, port, lan):
    bind = "0.0.0.0" if lan else "127.0.0.1"
    try:
        server = ThreadingHTTPServer((bind, port), make_handler(tablo, favs, port))
    except OSError as e:
        sys.exit(f"Port {port} is busy ({e.strerror}). Is it already running? Or use --port 9000.")
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.debug(f"Server listening on {bind}:{port}")
    return server


def menu(tablo, favs, port, web):
    base = f"http://127.0.0.1:{port}"
    show_streaming = not tablo.lan_only   # FAST can't play in --offline mode
    numbered = print_channels(tablo, favs, show_streaming)
    help_line = ("\nType a number or channel (9.1, KFOR) to watch.  i3 = what's on / details\n"
                 "+3 / -3 = add/remove favorite  "
                 "f=favorites  a=all  s=show/hide FAST  l=list" + ("  w=web page" if web else "") + "  q=quit\n"
                 "Keep this window open while watching.")
    print(help_line)
    while True:
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        c = choice.lower()
        if not c:
            continue
        if c in ("q", "quit", "exit"):
            return
        if c in ("l", "list", "?", "h", "help"):
            numbered = print_channels(tablo, favs, show_streaming)
            print(help_line)
            continue
        if c == "s":
            show_streaming = not show_streaming
            numbered = print_channels(tablo, favs, show_streaming)
            print(help_line)
            continue
        if c == "f":
            launch_player(f"{base}/favorites.m3u", playlist=True) and print(f"Opening favorites in {player_name()}")
            continue
        if c == "a":
            launch_player(f"{base}/playlist.m3u", playlist=True) and print(f"Opening all channels in {player_name()}")
            continue
        if c == "w" and web:
            open_url(base + "/")
            print(f"Opening {base}/")
            continue
        if c[0] == "i" and len(c) > 1 and (c[1:].strip()[:1].isdigit() or c[1] == " "):
            ch = resolve(tablo, favs, choice[1:].strip(), numbered)
            show_info(tablo, ch) if ch else print(f"'{choice[1:].strip()}' not found.")
            continue
        if c[0] in "+-" and len(c) > 1:
            change_favs(tablo, favs, [choice[1:]], add=c[0] == "+", numbered=numbered)
            numbered = print_channels(tablo, favs, show_streaming)
            print(help_line)
            continue
        ch = resolve(tablo, favs, choice, numbered)
        if not ch:
            print(f"'{choice}' not found. Type l to list channels.")
            continue
        if ch.kind != "ota" and not tablo.maybe_reconnect(min_gap=15):
            print("FAST (online) channels need the internet" +
                  (" (you're in --offline mode)." if tablo.lan_only else ", which isn't available right now.")
                  + " Local channels still work.")
            continue
        print(f"Tuning {ch.display_name}...")
        shown = getattr(tablo, "shown_now", {}) or {}
        if launch_player(f"{base}/ch/{ch.identifier}", vlc_meta(ch, shown.get(ch.identifier))):
            print(f"Playing {ch.display_name} in {player_name()}")


HELP_EPILOG = """\
examples:
  {RUN}                         channel menu; type a number to watch
  {RUN} --play 2.1              play one channel and exit
  {RUN} --play favorites        your favorites as a VLC playlist
  {RUN} --ffplay --play 2.1     use ffplay instead of VLC
  {RUN} --add-fav 9.1 KFOR      save favorites
  {RUN} --info 9.1              what's on now and next
  {RUN} --web                   channel page in your browser
  {RUN} --lan                   share with phones, tablets and TVs
  {RUN} --offline               home network only, no internet

computers that can run this script:
  Ubuntu / Pop!_OS / Linux   ./tablo-vlc.py      (VLC: apt, Snap or Flatpak)
  Windows 11                 py tablo-vlc.py     (Python 3.10+, VLC; ffmpeg via winget install Gyan.FFmpeg)
  Players: VLC, Flatpak VLC, ffplay, mpv (--player / player = in tablo.ini)

other devices (start the script with --lan; the PC needs ffmpeg for these):
  open http://<PC-IP>:8000/ (the startup lines show the exact address)

  iPhone / iPad      Safari: tap Play to watch in the browser. AirPlay to a TV from the player.
  Android phone      Chrome: tap Play to watch. Cast button sends it to a Google TV/Chromecast.
  Amazon Fire TV     Silk Browser -> the remote-friendly TV page (/tv). Or TiviMate.
  Samsung TV         Internet app -> TV page. Or AirPlay from an iPhone (2018+ Samsung TVs).
  onn / Google TV    Cast from Chrome on a phone or PC. Or TiviMate / a TV browser app.
  Apple TV           AirPlay from an iPhone or iPad. Or UHF / iPlayTV.
  IPTV apps          playlist http://<PC-IP>:8000/tv.m3u  +  guide http://<PC-IP>:8000/guide.xml
  VLC app (any)      http://<PC-IP>:8000/playlist.m3u   (original stream; no ffmpeg needed)

  TV page remote keys: arrows + OK pick, CH+/CH- or up/down change channel, Back returns.
  Quality: browser_quality (phones, 720) and tv_quality (TVs, 1080) in tablo.ini.
  Windows: allow Python through the firewall on Private networks when asked.
"""


def main():
    ap = argparse.ArgumentParser(
        description="Watch Tablo 4th Gen live TV on this PC, phones, tablets and TVs. "
                    "With no options, shows a channel menu.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG.replace("{RUN}", RUN))
    ap.add_argument("--play", metavar="CH",
                    help="play a channel (9.1, KFOR, a menu number) or 'favorites' / 'all'")
    ap.add_argument("--list", action="store_true", help="print favorites and all channels, then exit")
    ap.add_argument("--info", metavar="CH", help="show what's on a channel now and next (e.g. --info 9.1)")
    ap.add_argument("--add-fav", nargs="+", metavar="CH",
                    help="add channels to favorites in tablo.ini (e.g. --add-fav 9.1 KFOR 702.1)")
    ap.add_argument("--remove-fav", nargs="+", metavar="CH", help="remove channels from favorites")
    ap.add_argument("--web", "--page", "--pick", dest="web", action="store_true",
                    help="also serve a web page with Play buttons and open it")
    ap.add_argument("--lan", action="store_true",
                    help="let phones, tablets and TVs on your Wi-Fi watch (see 'other devices' below)")
    ap.add_argument("--setup", action="store_true", help="enter your Tablo email and password")
    ap.add_argument("--fetch-keys", action="store_true",
                    help="download the Tablo signing keys from GitHub into tablo.ini (done automatically if missing)")
    ap.add_argument("--favorites", action="store_true", help=argparse.SUPPRESS)  # old alias
    ap.add_argument("--no-vlc", action="store_true", help="just run the server (for other devices)")
    ap.add_argument("--player", metavar="NAME",
                    help="video player for this run: vlc, flatpak, ffplay, mpv, or a full command (overrides tablo.ini)")
    ap.add_argument("--ffplay", action="store_true", help="shortcut for --player ffplay")
    ap.add_argument("--buffer", type=int, metavar="MS",
                    help="VLC buffer in milliseconds for this run (default from tablo.ini or 3000)")
    ap.add_argument("--vlc", action="store_true", help="shortcut for --player vlc")
    ap.add_argument("--tune", metavar="CH", help="print a one-time VLC command for the Tablo's raw stream")
    ap.add_argument("--vlc-commands", action="store_true",
                    help="print a VLC command per local channel (script must be running)")
    ap.add_argument("--offline", "--local", dest="offline", action="store_true",
                    help="LAN-only mode: never use the internet; local channels from the saved list")
    ap.add_argument("--tablo-ip", metavar="ADDR",
                    help="your Tablo's address, e.g. 192.168.1.50 (if it changed, or with --offline)")
    ap.add_argument("--direct", action="store_true", help="never use ffmpeg; VLC plays the Tablo stream itself")
    ap.add_argument("--port", type=int, help="local port (default from tablo.ini or 8000)")
    ap.add_argument("--columns", type=int, metavar="N", help="channel list columns (default: fit the window)")
    ap.add_argument("--no-color", action="store_true", help="plain text channel list (no colors)")
    ap.add_argument("--debug", action="store_true", help="show detailed messages on screen too")
    args = ap.parse_args()
    if args.favorites and not args.play:
        args.play = "favorites"
    setup_logging(args.debug)
    global COLUMNS
    COLUMNS = max(0, args.columns or 0)
    global USE_COLOR
    if args.no_color:
        USE_COLOR = False
    log.debug(f"Started: {' '.join(sys.argv)}")

    global USE_FFMPEG
    USE_FFMPEG = bool(shutil.which("ffmpeg")) and not args.direct

    cfg = load_config()
    t = cfg["tablo"]
    if args.setup:
        cmd_setup(cfg)
        print("Add your favorites to tablo.ini (e.g. favorites = 4.1, 9.1), then run with no options.")
        return
    if not t.get("email") or not t.get("password"):
        if not CONFIG_FILE.exists():
            save_config(cfg)  # create a blank template to fill in
        sys.exit(f"Add your Tablo email and password to:\n  {CONFIG_FILE}\n"
                 f"then run this again. (Or run with --setup to be prompted.)")
    if args.offline and not args.fetch_keys and not load_keys(t):
        sys.exit(f"--offline needs the Tablo signing keys in {CONFIG_FILE.name}, and they can't be "
                 f"downloaded without the internet. Run once without --offline first.")
    if args.fetch_keys or not load_keys(t):
        if not args.fetch_keys:
            print(f"No Tablo signing keys in {CONFIG_FILE.name}; downloading them from GitHub...")
        if not (fetch_keys(cfg) and load_keys(t)):
            if "hash_key" not in t:
                save_config(cfg)  # add blank hash_key / device_key lines to fill in
            sys.exit(f"Couldn't get the Tablo signing keys automatically. Add these two lines to "
                     f"{CONFIG_FILE.name}:\n  hash_key = ...\n  device_key = ...\n"
                     f"(copy the values from tablo-api's tablo_api/auth.py on GitHub).")
        if args.fetch_keys:
            return

    try:
        tablo = Tablo(t["email"], t["password"], t.get("device", ""),
                      lan_only=args.offline, tablo_ip=args.tablo_ip or "")
    except NetworkError as e:
        if args.offline:
            sys.exit(f"--offline: {e}")
        log.error(f"Can't reach the Tablo cloud: {e}")
        sys.exit("Connect this PC to the internet and run it again. After one good start, "
                 "it keeps working offline for local channels.")
    except Exception as e:
        log.error(f"Could not connect to Tablo: {e}")
        sys.exit(f"See {LOG_FILE} for details.")
    if t.get("port", "").strip() == "8089":  # old default the script wrote itself
        t["port"] = str(DEFAULT_PORT)
        save_config(cfg)
        log.info(f"Changed port in {CONFIG_FILE.name} from 8089 to {DEFAULT_PORT}")
    port = args.port or int(t.get("port", DEFAULT_PORT))
    favs = Favorites(cfg, tablo)
    global PLAYER
    global VLC_BUFFER_MS, DEINTERLACE
    try:
        VLC_BUFFER_MS = max(300, int(args.buffer or t.get("vlc_buffer", "") or VLC_BUFFER_MS))
    except ValueError:
        log.warning(f"vlc_buffer in {CONFIG_FILE.name} should be a number of milliseconds; using {VLC_BUFFER_MS}")
    DEINTERLACE = (t.get("deinterlace", "") or DEINTERLACE).strip().lower()
    global BROWSER_QUALITY
    global TV_QUALITY
    for name in ("browser_quality", "tv_quality"):
        try:
            val = int(t.get(name, "") or 0)
            if val:
                val = min(QUALITIES, key=lambda q: abs(q - val))   # nearest supported height
                if name == "browser_quality":
                    BROWSER_QUALITY = val
                else:
                    TV_QUALITY = val
        except ValueError:
            log.warning(f"{name} in {CONFIG_FILE.name} should be a number like 720 or 1080")
    choice = args.player or ("ffplay" if args.ffplay else "vlc" if args.vlc else t.get("player", ""))
    PLAYER = resolve_player(choice)
    if PLAYER:
        log.debug(f"Player: {PLAYER['name']} ({' '.join(PLAYER['cmd'])})")
    else:
        log.warning("No video player found (VLC or ffplay). Channels will list, but can't play here. "
                    "Install one: sudo apt install vlc   (or: sudo apt install ffmpeg)")

    # ---- one-shot commands
    if args.add_fav or args.remove_fav:
        if args.add_fav:
            change_favs(tablo, favs, args.add_fav, add=True)
        if args.remove_fav:
            change_favs(tablo, favs, args.remove_fav, add=False)
        print("Favorites: " + (", ".join(c.display_name for c in favs.channels()) or "(none)"))
        return
    if args.list:
        print_channels(tablo, favs, show_streaming=True)
        return
    if args.info:
        ch = resolve(tablo, favs, args.info)
        if not ch:
            sys.exit(f"Channel '{args.info}' not found. Try --list.")
        show_info(tablo, ch)
        return
    if args.vlc_commands:
        print_vlc_commands(tablo, port)
        return
    if args.tune:
        ch = tablo.find(args.tune)
        if not ch:
            sys.exit(f"Channel '{args.tune}' not found. Try --list.")
        url = tablo.watch_url(ch.identifier)
        log.info(f"[{ch.display_name}] Tablo stream URL: {url}")
        print("\nPlay it straight from the Tablo (start within a minute or so; the")
        print("Tablo keeps it going while VLC is watching):\n")
        print(f'  vlc "{url}"\n')
        return

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    base = f"http://127.0.0.1:{port}"

    # ---- --play: one channel exits right away; a playlist needs the server
    if args.play and args.play.lower() not in ("all", "favorites", "favs", "fav"):
        want = args.play
        ch = resolve(tablo, favs, want)
        if not ch:
            sys.exit(f"Channel '{want}' not found. Try --list.")
        sys.exit(0 if play_direct(tablo, ch) else 1)

    server = start_server(tablo, favs, port, args.lan)
    log.info(f"Player: {player_name() if PLAYER else 'none found'}   Stream mode: "
             + ("ffmpeg relay" if USE_FFMPEG else "direct") + f"   Log: {LOG_FILE}")
    if args.web or args.lan:
        if not args.offline:
            threading.Thread(target=ensure_hls_js, daemon=True).start()
    if args.lan:
        ip = lan_ip(tablo.device_url)
        global LAN_BASE
        LAN_BASE = f"http://{ip}:{port}"
        log.info(f"Phones/tablets on your Wi-Fi: open {LAN_BASE}/ in Safari or Chrome"
                 + ("" if LiveTV.available() else "  (install ffmpeg on this PC for in-browser playback)"))
        if LiveTV.available():
            log.info(f"TVs (Fire TV Silk, Samsung Internet, Google TV browser): {LAN_BASE}/tv")
            log.info(f"IPTV apps (TiviMate, UHF, iPlayTV, Kodi): playlist {LAN_BASE}/tv.m3u  guide {LAN_BASE}/guide.xml")
        if IS_WINDOWS:
            log.info("If Windows asks, allow Python through the firewall on Private networks.")
    if args.web:
        log.info(f"Web page: {base}/")
        open_url(base + "/")

    if args.play:  # favorites / all playlist
        which = "playlist.m3u" if args.play.lower() == "all" else "favorites.m3u"
        launch_player(f"{base}/{which}", playlist=True)
        print("Keep this window open while watching. Ctrl+C to quit.")
    elif not args.no_vlc and sys.stdin.isatty():
        if CONSOLE and not args.debug:
            CONSOLE.setLevel(logging.WARNING)  # keep the menu tidy; everything still goes to the log
        menu(tablo, favs, port, args.web)
        shutdown(server)
        return
    else:
        print(f"Server running at {base}/  Ctrl+C to quit.")

    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        pass
    shutdown(server)


if __name__ == "__main__":
    main()
