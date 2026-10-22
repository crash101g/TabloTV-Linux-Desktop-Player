#!/usr/bin/env python3
"""tablo-vlc: watch live TV from a Tablo 4th Gen in VLC.

Needs nothing but Python 3 (built into Ubuntu) and VLC. No install.
Settings (email, password, favorites) live in tablo.ini next to this script;
activity is logged to tablo-vlc.log in the same folder. (Set the TABLO_VLC_DIR
environment variable to keep them in another folder, e.g. for Docker.)

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
  --encoder NAME   phone/TV video encoder: auto (graphics chip if possible), x264, ...
  --port N         use a different local port (default 8000)
  --debug          show detailed messages on screen

TV guide: refreshes by itself every 10 minutes (guide_refresh in tablo.ini).
  r in the menu, or the Refresh guide button on the web pages, refreshes it now;
  --refresh-guide does the same for a copy that's already running (e.g. headless).

Restart a stuck Tablo (the Tablo has no restart command, so this needs a physical
smart plug, e.g. a Shelly Plug US, between the Tablo and the wall; see readme.txt):
  --test-plug      check the smart plug answers (switches nothing)
  --restart-tablo  power-cycle the Tablo (add --yes to skip the question, for a schedule)
"""

# =============================================================================
# HOW THIS FILE IS ORGANIZED (top to bottom)
# Each part starts with a "SECTION n" banner: search for "SECTION 7" to jump there.
# =============================================================================
#  1. Platform helpers      Windows/Linux differences, colors, launching apps
#  2. Settings             the S object, tablo.ini, tablo-cache.json, the log
#  3. Log scrubbing         tokens/passwords replaced by [token:xxxx] in logs
#  4. Tablo API             cloud login, channel list, TV guide, tuning on your
#                           home network (signed requests), freeing tuners,
#                           offline fallback (class Tablo)
#  5. Favorites             the 'favorites =' line in tablo.ini
#  6. TV guide + playlists  /guide.xml (XMLTV, prebuilt in the background) and
#                           /tv.m3u for IPTV apps
#  7. Browser/TV playback   ffmpeg converts the Tablo stream to H.264 for phones,
#                           TVs and Cast (class LiveTV), on the graphics chip
#                           when possible (ENCODERS, choose_encoder)
#  8. Web pages             main page (phones/PC) and the /tv page (TV remotes)
#  9. Web server            every URL other devices use (make_handler)
# 10. Players + commands    --setup, VLC commands, finding VLC/Flatpak/ffplay/
#                           mpv; one reusable VLC window controlled through
#                           VLC's local web remote
# 11. Terminal menu         channel list in columns, colors, details view
# 12. main()                command-line options and startup, split into steps
#                           (load_login, connect_tablo, serve...); the
#                           password keyring and smart-plug restart
#                           (--restart-tablo) helpers live here too
# Settings for this run live in one object, S (class Settings).
# Tests: tests/ (python3 -m pytest), with a fake Tablo; no account needed.
#
# How a channel plays in VLC:  menu pick -> VLC opens http://127.0.0.1:8000/ch/<id>
#   -> our server asks the Tablo to tune (signed POST .../watch) -> the Tablo
#   returns a stream URL -> VLC is redirected there (or ffmpeg relays it).
# How it plays on a phone/TV:  /live/<id>/index.m3u8 -> ffmpeg converts the
#   stream to H.264/AAC HLS in a temp folder -> the device plays those segments.
#
# Security notes: tablo.ini holds your password (file is mode 0600); tokens are
# scrubbed from logs; with --lan, set access_key in tablo.ini to require a key.
#
# -----------------------------------------------------------------------------
# WORDS USED IN THIS FILE
# -----------------------------------------------------------------------------
#  OTA            Over-the-air: local broadcast channels from your antenna,
#                 tuned by the Tablo box. Numbered like 9.1 (major.minor).
#  FAST / OTT     Free ad-supported streaming channels (Pluto, Xumo...) that the
#                 Tablo app lists. They come from the internet, not the antenna,
#                 so they don't use a tuner. In code: Channel.kind == "ott".
#  Channel id     Tablo's own text identifier for a channel (not 9.1). Used
#                 in every URL: /ch/<id>, /live/<id>/...
#  Tuner          The Tablo has a few. Each OTA channel being watched (or
#                 recorded) holds one until its session is ended.
#  Session token  What the Tablo returns when it tunes. We send it back
#                 (DELETE /player/sessions/<token>) to free the tuner.
#  Cloud          Tablo's internet service (lighthousetv.ewscloud.com): login,
#                 channel list, TV guide. Tuning itself is done by the box on
#                 your home network (device_url).
#  Lighthouse/lh  Tablo's name for the token that picks your account + box in
#                 cloud requests (self.lh_token). "?lh" on local requests too.
#  Signing keys   hash_key/device_key: fixed values from the Tablo app, used to
#                 sign requests to the box (device_auth). Public, not secret.
#  HLS / .m3u8    HTTP Live Streaming: a small playlist file that lists 2-6 s
#                 video files (.ts segments). The Tablo, phones and TVs speak it.
#  .m3u           A playlist of channels (VLC, IPTV apps).
#  XMLTV          The standard TV-guide file format IPTV apps read (/guide.xml).
#  Converting     The Tablo sends the raw broadcast (usually MPEG-2 video, AC-3
#  (transcoding)  audio). VLC plays that, phone/TV browsers don't, so ffmpeg
#                 re-encodes it to H.264 + AAC for them (SECTION 7).
#  Viewer         A device watching through our server, known by its IP address.
#
# -----------------------------------------------------------------------------
# WHAT RUNS AT THE SAME TIME (threads)
# -----------------------------------------------------------------------------
#  Main thread      the terminal menu (or just waits, with --no-vlc).
#  Web server       one short-lived thread per web request (ThreadingHTTPServer),
#                   so many devices can be served at once.
#  LiveTV reaper    stops converters nobody has watched for 45 s.
#  Guide refresher  re-reads what's on now every guide_refresh minutes (10), and
#                   rebuilds /guide.xml every 30 min (--lan only).
#  Log readers      one per ffmpeg, copying its error output into the log.
#  Shared data is guarded by locks: Tablo.lock (cloud data), LiveTV.lock (the
#  converters), _xmltv_lock (the guide). Rule: never do slow work (tuning,
#  starting ffmpeg, network calls) while holding a lock, so one slow device
#  can't freeze the others.
#
# -----------------------------------------------------------------------------
# WHERE TO CHANGE COMMON THINGS
# -----------------------------------------------------------------------------
#  Default port 8000 ................ DEFAULT_PORT
#  Guide refresh (10 min) ........... guide_refresh in tablo.ini / start_guide_refresher
#  Default VLC buffer (3 s) ......... DEFAULT_VLC_BUFFER (or vlc_buffer in tablo.ini;
#                                     10000 = 10 s if Wi-Fi hiccups cause stutters)
#  A new tablo.ini setting .......... CONFIG_HEADER (document it), save_config
#                                     (default), apply_playback_settings (read
#                                     it into S), class Settings (store it)
#  A new command-line option ........ build_parser, then use args.<name>
#  A new web address (URL) .......... Handler.do_GET in make_handler
#  A new video player ............... resolve_player / launch_player
#  A new GPU encoder ................ ENCODERS and AUTO_ORDER
#  Another smart plug type .......... class SmartPlug (--restart-tablo)
#  Menu colors ...................... LIGHT_BLUE, HEAD, WARN, DIM (SECTION 11)
#  Green addresses on screen ........ ADDRESS (color) and ADDRESS_RE (what counts)
#  Phone/TV picture quality ......... browser_quality / tv_quality in tablo.ini
#  Web page look .................... PAGE and TV_PAGE (HTML/CSS/JS templates)
#
# -----------------------------------------------------------------------------
# CONVENTIONS
# -----------------------------------------------------------------------------
#  * Python standard library only, so there's nothing to pip install. Optional
#    extras (keyring, tablo-api) are imported inside try/except.
#  * Outside programs (ffmpeg, VLC, ffplay, mpv, secret-tool) are found with
#    shutil.which and started with subprocess; never through a shell, so
#    channel names can't inject commands.
#  * Messages go through log (screen + tablo-vlc.log). print() is only for the
#    menu and lists meant for the screen. Anything secret is registered with
#    register_secret() so the log shows [token:xxxx] instead.
#  * Network calls go through http(), which retries and raises NetworkError
#    (can't connect) or HTTPStatusError (the server said no), so callers can
#    tell "offline" apart from "wrong password".
#  * Web pages are plain strings with %%NAME%% placeholders filled by fill();
#    there's no template library.
#  * Works on Linux and Windows 11: platform differences are kept in small
#    helpers (IS_WINDOWS, spawn, clock, find_windows_vlc, find_flatpak_vlc).
# =============================================================================

# Standard library only (see CONVENTIONS above).
import argparse
import configparser
import getpass
import hashlib
import hmac
import html
from http.cookies import SimpleCookie
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


# =============================================================================
# SECTION 1: PLATFORM HELPERS
# Small differences between Linux and Windows, kept in one place.
# =============================================================================
__version__ = "1.3.0"        # shown by --version and at the top of the log

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


# =============================================================================
# SECTION 2: SETTINGS
# S (this run's settings), tablo.ini, and the log file.
# =============================================================================
DEFAULT_PORT = 8000
# VLC live buffer in ms (3 s): quick to start and change channels. If VLC stutters
# on Wi-Fi hiccups, raise it to 10000 (10 s) with vlc_buffer in tablo.ini or
# --buffer 10000, rather than changing it here.
DEFAULT_VLC_BUFFER = 3000


class Settings:
    """Everything the command line and tablo.ini decide, in one place.
    Set once at startup (main) and read everywhere as S.<name>."""
    def __init__(self):
        self.use_ffmpeg = False          # ffmpeg can relay streams to VLC (--direct turns off)
        self.player = None               # chosen video player: resolve_player()
        self.last_ffplay = None          # ffplay window we opened (closed when switching)
        self.last_ffplay2 = None         # ... and the second window (menu: w 4.1)
        self.vlc_buffer_ms = DEFAULT_VLC_BUFFER   # VLC live buffer, ms (tablo.ini vlc_buffer, --buffer)
        self.deinterlace = "yadif2x"     # VLC deinterlace mode; "off" = none
        self.browser_quality = 720       # phones/tablets picture height (browser_quality)
        self.tv_quality = 1080           # TVs / Cast / IPTV apps picture height (tv_quality)
        self.guide_refresh = 10          # minutes between automatic guide refreshes; 0 = off
        self.captions = False            # phone/TV pages start with closed captions showing
        self.encoder_setting = "auto"    # tablo.ini encoder: auto, x264, vaapi, nvenc, qsv, amf
        self.encoder = None              # encoder actually used (found on first use)
        self.lan_base = ""               # "http://<this PC's IP>:<port>" with --lan (for Cast)
        self.access_key = ""             # tablo.ini access_key: other devices need ?key=...
        self.columns = 0                 # menu columns; 0 = fit the window (--columns)
        self.max_channels = 0            # channels listed in the menu; 0 = all (--max-channels)
        self.use_color = (sys.stdout.isatty() and not os.environ.get("NO_COLOR")
                          and enable_windows_ansi())   # --no-color turns off
        self.console = None              # the on-screen log handler (quieted during the menu)
        self.hash_key = ""               # Tablo app signing keys (tablo.ini / downloaded)
        self.device_key = ""


S = Settings()


# Config lives next to this script: same folder as tablo-vlc.py. The environment
# variable TABLO_VLC_DIR can point somewhere else instead (Docker uses /data, so the
# settings survive updating the container; a system service can use it too).
DATA_DIR = Path(os.environ.get("TABLO_VLC_DIR") or Path(__file__).resolve().parent).expanduser()
if os.environ.get("TABLO_VLC_DIR"):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "tablo.ini"
LOG_FILE = DATA_DIR / "tablo-vlc.log"
# Saved copy of your Tablo's address + channel list (+ last guide info) so the
# script still starts and plays local channels when the internet is down.
CACHE_FILE = DATA_DIR / "tablo-cache.json"

CONFIG_HEADER = """\
# tablo-vlc settings. Edit with any text editor.
#   email / password : your Tablo account login. password = keyring keeps the real
#                      password in your desktop keyring instead (--setup offers it)
#   favorites        : channels to list first, e.g. 4.1, 9.1, KFOR (or use --add-fav / --remove-fav)
#   port             : local port for the playlist server
#   device           : (optional) Tablo name, if your account has more than one
#   vlc_buffer       : VLC buffer for live TV in milliseconds (default 3000 = 3 seconds).
#                      If VLC stutters on Wi-Fi hiccups, try 10000 (10 seconds): smoother,
#                      but channels take a few seconds longer to start and to change.
#   deinterlace      : VLC deinterlace mode for 1080i broadcasts: yadif2x (default), yadif, bob, off
#   browser_quality  : max picture height when watching in a phone/tablet browser: 480, 720 (default), 1080
#   tv_quality       : max picture height for TVs, Cast and IPTV apps: 720, 1080 (default)
#   max_channels     : list at most this many channels in the menu (0 = all, the default).
#                      Favorites always show. Same as --max-channels for one run
#   captions         : on = phone and TV web pages start with closed captions showing
#                      (default off; turn them on/off while watching: the player's CC
#                      button on phones, ◀ or ▶ on the TV page). VLC: Subtitle menu
#   guide_refresh    : minutes between automatic "what's on" refreshes for the menu and
#                      the web pages (default 10; 0 = off). Refresh by hand any time:
#                      r in the menu, the Refresh guide button, or --refresh-guide
#   encoder          : video encoder for phones/TVs: auto (default: uses your graphics
#                      chip if it can), x264 (software), vaapi, nvenc, qsv, amf
#   access_key       : (optional) with --lan, other devices must use this key, e.g.
#                      http://<PC-IP>:8000/?key=YOURKEY  (blank = anyone on your Wi-Fi)
#   player           : (optional) video player: vlc, flatpak (Flatpak VLC), ffplay, mpv,
#                      or a full command. Blank = VLC (regular, Snap or Flatpak), else ffplay.
#   smart_plug       : (optional) for --restart-tablo. The Tablo has no restart command,
#                      so this needs a PHYSICAL smart plug the Tablo is plugged into:
#                        shelly 192.168.1.60     Shelly Plug US Gen4 / Plus Plug US
#                        tasmota 192.168.1.61    plug with Tasmota (e.g. Athom PG03V3-US16A-TAS)
#                        url                     any plug with on/off web addresses: also set
#                                                smart_plug_off_url and smart_plug_on_url
#   smart_plug_password : (optional) the plug's own login password, if you set one
#   hash_key / device_key : Tablo app signing keys (needed to tune channels).
#                      Same for every Tablo owner. Filled in automatically from
#                      the tablo-api project on GitHub; refresh with --fetch-keys.
# No quotes needed around values. Keep this file private (don't commit it).

"""


# =============================================================================
# SECTION 3: LOGGING AND SECRET SCRUBBING
# Console + tablo-vlc.log next to the script (rotates at 1 MB, keeps 3 old
# files). Tokens, the password and the access key are replaced by [token:xxxx].
# =============================================================================
log = logging.getLogger("tablo-vlc")


# --- Secret scrubbing --------------------------------------------------------
# Logs get pasted into forums and GitHub issues, so tokens never reach the log
# file or screen. Each secret is replaced by a short fingerprint such as
# [token:a91f]: the same token always gets the same fingerprint (so you can
# still follow one session, or see when it changed), but it can't be reused.
# --debug-raw turns this off for hands-on troubleshooting.
_SECRETS: set = set()
_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)([A-Za-z0-9._~+/=-]{12,})"),
    re.compile(r"(tablo:[A-Za-z0-9]+:)([0-9a-f]{32})"),                     # signed device auth
    re.compile(r"([?&](?:token|auth|key|sig|signature|session|lh_token)[^=&\s]*=)([^&\s\"']+)", re.I),
    re.compile(r"(/player/sessions/)([^/?\s]+)"),                           # Tablo stream session
]


def fingerprint(secret: str) -> str:
    """Short, stable stand-in for a secret: same input, same 4 hex digits."""
    return "[token:" + hashlib.sha256(secret.encode()).hexdigest()[:4] + "]"


def register_secret(value) -> None:
    """Remember a secret (login token, session token, password) for scrubbing."""
    if value and isinstance(value, str) and len(value) >= 6:
        _SECRETS.add(value)


def scrub(text: str) -> str:
    """Replace every known secret, and anything token-shaped, with a fingerprint."""
    for secret in sorted(_SECRETS, key=len, reverse=True):     # longest first
        if secret in text:
            text = text.replace(secret, fingerprint(secret))
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(1) + (m.group(2) if m.group(2).startswith("[token:")
                                               else fingerprint(m.group(2))), text)
    return text


class ScrubFilter(logging.Filter):
    """Logging filter that scrubs secrets from every message before it's written."""
    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the message with secrets scrubbed; always lets the record through."""
        record.msg = scrub(record.getMessage())
        record.args = None
        return True


# Web addresses and IP addresses on screen are shown in light green, so the ones to type on a
# phone or TV stand out. Only on screen: tablo-vlc.log stays plain text.
ADDRESS_RE = re.compile(r"https?://[^\s,;)\"'<>]+|\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")


def color_addresses(text: str) -> str:
    """Color every URL / IP address in a line light green (if colors are on)."""
    return ADDRESS_RE.sub(lambda m: f"{ADDRESS}{m.group(0)}{RESET}", text) if S.use_color else text


class ScreenFormatter(logging.Formatter):
    """On-screen log lines: the plain message, with addresses in light green."""
    def format(self, record: logging.LogRecord) -> str:
        return color_addresses(super().format(record))


def setup_logging(debug: bool = False, raw: bool = False) -> None:
    """Log to the screen (INFO, or DEBUG with --debug) and to tablo-vlc.log
    (always DEBUG, rotated at 1 MB, 3 old copies). raw=True skips token scrubbing."""
    log.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    ch = S.console = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setFormatter(ScreenFormatter("%(message)s"))      # addresses in light green on screen
    log.addHandler(fh)
    log.addHandler(ch)
    if not raw:
        log.addFilter(ScrubFilter())


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
def load_config() -> configparser.ConfigParser:
    """Read tablo.ini (next to the script). Missing file = empty [tablo] section.
    interpolation=None so passwords may contain % signs."""
    cfg = configparser.ConfigParser(interpolation=None)  # allow % in passwords
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE, encoding="utf-8")
    if "tablo" not in cfg:
        cfg["tablo"] = {}
    return cfg


def save_config(cfg: configparser.ConfigParser) -> None:
    """Write tablo.ini with the explanatory header, readable only by you (0600).
    Fills in any missing setting names with their defaults so they're discoverable."""
    t = cfg["tablo"]
    for key, default in (("email", ""), ("password", ""), ("favorites", ""),
                         ("port", str(DEFAULT_PORT)), ("player", ""), ("vlc_buffer", str(DEFAULT_VLC_BUFFER)),
                         ("deinterlace", "yadif2x"), ("browser_quality", "720"), ("tv_quality", "1080"), ("encoder", "auto"), ("guide_refresh", "10"), ("captions", "off"), ("max_channels", "0"), ("access_key", ""), ("smart_plug", ""), ("hash_key", ""), ("device_key", "")):
        t.setdefault(key, default)
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(CONFIG_HEADER)
        cfg.write(f)
    os.chmod(CONFIG_FILE, 0o600)  # only you can read it


# =============================================================================
# SECTION 4: TABLO API (cloud + the box on your network)
# Login flow and HMAC-MD5 request signing as documented by the tablo-api
# (trevor-viljoen) and tablo2plex projects. The two signing keys are fixed
# values from the official Tablo app; they are NOT in this file. They're read
# from tablo.ini (hash_key / device_key), downloaded from GitHub on first run
# (fetch_keys), or taken from the tablo-api package if it's installed.
# 
# Request map:
#   cloud  POST /api/v2/login/                       email+password -> access token
#   cloud  GET  /api/v2/account/                     your Tablo box(es) + profile
#   cloud  POST /api/v2/account/select/              pick box+profile -> lh token
#   cloud  GET  /api/v2/account/<lh>/guide/channels/       channel list
#   cloud  GET  /api/v2/account/<lh>/guide/channels/live/  what's on now
#   cloud  GET  /api/v2/account/guide/channels/<id>/airings/<date>/  schedule
#   box    POST /guide/channels/<id>/watch?lh       tune (signed) -> stream URL
#   box    DELETE /player/sessions/<token>?lh       free the tuner (signed)
# =============================================================================
CLOUD = "https://lighthousetv.ewscloud.com"
CLOUD_UA = "Tablo-FAST/2.0.0 (Mobile; iPhone; iOS 16.6)"
LOCAL_UA = "Tablo-FAST/1.7.0 (Mobile; iPhone; iOS 18.4)"


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
    S.hash_key = t.get("hash_key", "").strip().strip('"')
    S.device_key = t.get("device_key", "").strip().strip('"')
    if not (S.hash_key and S.device_key):
        try:
            from tablo_api import auth as _ta  # optional: pip install tablo-api
            S.hash_key = S.hash_key or _ta._HASH_KEY
            S.device_key = S.device_key or _ta._DEVICE_KEY
            log.debug("Signing keys loaded from the tablo-api package")
        except Exception:
            pass
    return bool(S.hash_key and S.device_key)


class NetworkError(RuntimeError):
    """Couldn't reach a host: offline, DNS failure, timeout, connection refused/reset."""


class HTTPStatusError(RuntimeError):
    """The server answered, but with an HTTP error code (kept in .code)."""
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
    """Read tablo-cache.json (saved channel list etc.); {} if missing or broken."""
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(data: dict) -> None:
    """Write tablo-cache.json atomically (temp file + rename) so a crash can't
    leave a half-written cache. No passwords or login tokens are stored in it."""
    try:
        tmp = CACHE_FILE.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, CACHE_FILE)
    except Exception as e:
        log.debug(f"Couldn't save {CACHE_FILE.name}: {e}")


def device_auth(method, path, body=""):
    """Sign a request to the Tablo on your home network, the way the Tablo app does:
    HMAC-MD5 over method, path, MD5 of the body and the date, using the app's keys.
    Returns the (Authorization, Date) headers the Tablo expects."""
    date = format_datetime(datetime.now(timezone.utc), usegmt=True)
    md5 = hashlib.md5(body.encode()).hexdigest() if body else ""
    sig = hmac.new(S.hash_key.encode(), f"{method}\n{path}\n{md5}\n{date}".encode(), hashlib.md5).hexdigest()
    return f"tablo:{S.device_key}:{sig}", date


def pick_logo(logos) -> str:
    """Best channel logo URL from Tablo's list (prefers the large light version)."""
    logos = [l for l in (logos or []) if isinstance(l, dict) and l.get("url")]
    pref = next((l for l in logos if l.get("kind") == "lightLarge"), None)
    return (pref or (logos[0] if logos else {})).get("url", "")


def vlc_tuning() -> dict:
    """Per-item VLC options for smooth live TV.
    network-caching = how many milliseconds of video VLC holds before playing
    (3 s by default, quick to start and change channels). If VLC stutters on
    Wi-Fi or network hiccups, a 10 s buffer (vlc_buffer = 10000) rides them out,
    at the cost of a longer wait when starting or changing channels."""
    opts = {"network-caching": str(S.vlc_buffer_ms)}
    if S.deinterlace and S.deinterlace != "off":
        opts["deinterlace"] = "-1"            # automatic: only interlaced (e.g. 1080i) broadcasts
        opts["deinterlace-mode"] = S.deinterlace
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
    """'12 min left' for an airing that's on now; '' otherwise."""
    if not a or not a.get("_end"):
        return ""
    mins = int((a["_end"] - datetime.now(timezone.utc)).total_seconds() // 60)
    return f"{mins} min left" if 0 <= mins < 600 else ""


@dataclass
class Channel:
    """One channel. kind is 'ota' (local over-the-air) or 'ott' (FAST/streaming).
    identifier is Tablo's internal id (e.g. S122912_503_01), used in every API call."""
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
        """'9.1 KWTV' for local channels, just the name for FAST channels."""
        return f"{self.major}.{self.minor} {self.call_sign}" if self.major else self.call_sign


class Tablo:
    """Talks to the Tablo: the cloud (login, channel list, guide) and the box on
    your home network (tuning channels). Falls back to a saved channel list when
    the internet is down, and never touches the internet in --offline mode."""
    def __init__(self, email: str, password: str, device_name: str = "",
                 lan_only: bool = False, tablo_ip: str = ""):
        """Log in (or load the saved channel list when offline / --offline).
        tablo_ip overrides the Tablo's address (if it changed)."""
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
        self._login_lock = threading.Lock()         # one re-login at a time
        # Tuners this script holds: {session token: {"ident", "who", "since"}}.
        # Every tune goes through watch_session() and every release through
        # end_session(), so this is exact for this script (not for recordings or
        # the Tablo app on other TVs; see tuner_status()).
        self.active: dict[str, dict] = {}
        self._active_lock = threading.Lock()
        self.tuner_total = int(self.cache.get("tuners") or 0)    # from /server/info
        self.model = self.cache.get("model", "")
        self.tuner_states = None      # [True/False per tuner] if the Tablo reports it
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
        """Save the Tablo's address, channel list and our client id for offline use."""
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
        """Full cloud login: email+password -> account -> pick the Tablo -> device token
        -> channel list. Updates everything in place and saves the cache."""
        jhdr = {"User-Agent": CLOUD_UA, "Content-Type": "application/json"}
        to, rt = self._timeout, self._retries
        # Step 1: email + password -> access token (proves who you are)
        login = http("POST", f"{CLOUD}/api/v2/login/", jhdr,
                     json.dumps({"email": self.email, "password": self.password}).encode(),
                     timeout=to, retries=rt)
        self.access_token = login["access_token"]
        register_secret(self.access_token)
        register_secret(self.password)
        auth = {**jhdr, "Authorization": f"{login['token_type']} {self.access_token}"}

        # Step 2: the account -> your Tablo box(es); its 'url' is its home-network address
        acct = http("GET", f"{CLOUD}/api/v2/account/", auth, timeout=to, retries=rt)
        if not acct.get("devices"):
            sys.exit("No Tablo devices found on this account.")
        dev = acct["devices"][0]
        if self.device_name:
            dev = next((d for d in acct["devices"]
                        if d["name"].lower() == self.device_name.lower()), dev)
        self.device_url = dev["url"].rstrip("/")

        # Step 3: pick that box + your profile -> the "Lighthouse" token for guide requests
        sel = http("POST", f"{CLOUD}/api/v2/account/select/", auth,
                   json.dumps({"pid": acct["profiles"][0]["identifier"],
                               "sid": dev["serverId"]}).encode(), timeout=to, retries=rt)
        self.lh_token = sel["token"]
        register_secret(self.lh_token)

        # Step 4: the channel list (local OTA + FAST), turned into Channel objects
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
        # Local channels first, in channel-number order (2.1, 4.1, 9.1...), then FAST
        chans.sort(key=lambda c: (c.kind != "ota", c.major, c.minor, c.call_sign.lower()))
        self.channels = chans
        self.device_label = dev["name"]
        self.online = True
        self._save_cache()
        log.info(f"Connected to '{dev['name']}' at {self.device_url} "
              f"({len(self.ota)} local, {len(self.ott)} FAST channels)")

    @property
    def ota(self):
        """Local over-the-air channels."""
        return [c for c in self.channels if c.kind == "ota"]

    @property
    def ott(self):
        """FAST (free internet streaming) channels."""
        return [c for c in self.channels if c.kind != "ota"]

    # ---------------- guide (what's on) ----------------
    def _cloud_get(self, make_url, timeout=6, retries=1):
        """GET a Tablo cloud guide URL. If the cloud says our login has expired
        (401/403), which happens on long-running servers, log in again and retry
        once. make_url is a function because the URL can contain the token."""
        old_token = self.lh_token
        try:
            return http("GET", make_url(), self._guide_headers(), timeout=timeout, retries=retries)
        except HTTPStatusError as e:
            if e.code not in (401, 403):
                raise
        with self._login_lock:
            if self.lh_token == old_token:          # nobody else refreshed it meanwhile
                log.info("Tablo cloud login expired; logging in again")
                self.connect()
        return http("GET", make_url(), self._guide_headers(), timeout=timeout, retries=retries)

    def _guide_headers(self):
        """Headers for Tablo cloud guide requests (login token + device token)."""
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
            rows = self._cloud_get(lambda: f"{CLOUD}/api/v2/account/{self.lh_token}/guide/channels/live/") or []
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

    def refresh_guide(self) -> dict:
        """Manual refresh (menu 'r', web button, --refresh-guide): fetch what's on now
        right away and forget the cached 2-day listings so they're re-read when next
        needed. Repeated presses within 20 s reuse the fresh copy (it's a cloud call)."""
        if time.time() - getattr(self, "_forced_at", 0) < 20:
            return self.now_playing()
        self._forced_at = time.time()
        self._air_cache = {}
        _xmltv_cache["at"] = 0                    # /guide.xml rebuilds on its next request
        now = self.now_playing(max_age=0)
        log.info(f"Guide refreshed ({len(now)} channels with show info)")
        return now

    def guide_time(self) -> str:
        """When 'what's on' was last fetched, e.g. '5:02 PM' ('' if never)."""
        cache = getattr(self, "_now_cache", None)
        return clock(datetime.fromtimestamp(cache[0])) if cache else ""

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
                rows = self._cloud_get(
                    lambda: f"{CLOUD}/api/v2/account/guide/channels/{ident}/airings/{day.isoformat()}/") or []
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

    def remember_watched(self, ident: str) -> None:
        """Note a channel you just watched (the 5 most recent are kept in
        tablo-cache.json, so 'p' and the Recent line work across runs too)."""
        recent = [ident] + [x for x in self.cache.get("recent", []) if x != ident]
        self.cache["recent"] = recent[:5]
        save_cache(self.cache)

    def recent(self) -> list:
        """Recently watched channels, newest first."""
        return [ch for ch in (self.by_id(x) for x in self.cache.get("recent", [])) if ch]

    def upcoming(self, ident: str, count: int = 6) -> list:
        """Next few airings on one channel."""
        now = datetime.now(timezone.utc)
        return [a for a in self.airings(ident) if a["_end"] and a["_end"] > now][:count]

    def by_id(self, ident: str):
        """Channel for a Tablo identifier, or None."""
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
        """Ask the Tablo (on your network) to tune a channel. Returns the stream URL
        (HLS) and remembers the session token so the tuner can be freed later."""
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
        register_secret(self._last_token)
        return resp["playlist_url"]

    def end_session(self, token: str) -> None:
        """Tell the Tablo we're done with a stream so its tuner is freed right away
        (instead of when the session times out)."""
        if not token:
            return
        with self._active_lock:
            self.active.pop(token, None)
        path = f"/player/sessions/{token}"
        auth, date = device_auth("DELETE", path)
        try:
            http("DELETE", f"{self.device_url}{path}?lh", {
                "Authorization": auth, "Date": date, "Accept": "*/*", "User-Agent": LOCAL_UA},
                timeout=5, retries=0)
            log.debug(f"Ended Tablo session {token[:8]}…")
        except Exception as e:
            log.debug(f"Couldn't end Tablo session {token[:8]}…: {e}")

    def watch_session(self, ident: str, who: str = "") -> tuple[str, str]:
        """(stream URL, session token); token is '' for a FAST direct stream.
        who = what's watching (shown by the menu's tuner status)."""
        self._last_token = ""
        url = self.watch_url(ident)
        token = self._last_token
        if token:                                    # a tuner is now ours
            with self._active_lock:
                self.active[token] = {"ident": ident, "who": who, "since": time.time()}
        return url, token

    # --- Tuner status ---------------------------------------------------------
    def _device_get(self, path: str):
        """Signed, read-only GET to the Tablo on your home network."""
        auth, date = device_auth("GET", path)
        return http("GET", f"{self.device_url}{path}?lh", {
            "Authorization": auth, "Date": date, "Accept": "*/*", "User-Agent": LOCAL_UA},
            timeout=5, retries=0)

    def load_tuner_info(self) -> None:
        """Ask the Tablo how many tuners it has (/server/info, the same request the
        tablo-api and tablo2plex projects use). Also tries /server/tuners, which older
        Tablos used to report which tuners are busy; if the 4th Gen answers it, the
        menu can count recordings and other TVs too. Both are read-only; nothing is
        tuned. Runs in the background at startup; the count is saved for offline use."""
        try:
            info = self._device_get("/server/info") or {}
            model = info.get("model") or {}
            if model.get("tuners"):
                self.tuner_total = int(model["tuners"])
                self.model = model.get("name") or self.model
                self.cache.update(tuners=self.tuner_total, model=self.model)
                save_cache(self.cache)
                log.debug(f"Tablo model: {self.model}, {self.tuner_total} tuners")
        except Exception as e:
            log.debug(f"Couldn't read /server/info: {e}")
        try:
            raw = self._device_get("/server/tuners")
            log.debug(f"/server/tuners answered: {str(raw)[:300]}")
            if isinstance(raw, list) and raw and all(isinstance(t, dict) for t in raw):
                states = [bool(t.get("in_use", t.get("inUse", t.get("busy")))) for t in raw
                          if any(k in t for k in ("in_use", "inUse", "busy"))]
                if len(states) == len(raw):
                    self.tuner_states = states
                    self.tuner_total = self.tuner_total or len(states)
        except Exception as e:
            log.debug(f"/server/tuners not available: {e}")

    def tuner_status(self) -> dict:
        """{'total': 4 or 0 if unknown, 'ours': [(channel, who, since)], 'busy': n or None}.
        'busy' (all tuners in use, by anything) is only known if the Tablo reports it."""
        with self._active_lock:
            ours = [(self.by_id(a["ident"]), a["who"], a["since"]) for a in self.active.values()]
        busy = None
        if self.tuner_states is not None:
            try:
                fresh = self._device_get("/server/tuners")
                self.tuner_states = [bool(t.get("in_use", t.get("inUse", t.get("busy")))) for t in fresh]
            except Exception:
                pass
            busy = sum(self.tuner_states)
        # FAST channels come from the internet and don't use an antenna tuner
        return {"total": self.tuner_total, "ours": [o for o in ours if o[0] and o[0].kind == "ota"],
                "busy": busy}

    def tuner_summary(self) -> str:
        """One line, e.g. 'Tuners: 2 of 4 in use by this script (9.1 KNINE, 4.1 KFOUR)'."""
        st = self.tuner_status()
        n, total = len(st["ours"]), st["total"]
        names = ", ".join(f"{c.major}.{c.minor} {c.call_sign}" if c.major else c.call_sign for c, _, _ in st["ours"])
        of = f" of {total}" if total else ""
        if st["busy"] is not None:
            others = max(0, st["busy"] - n)
            line = f"Tuners: {st['busy']}{of} busy ({n} by this script" + (
                f", {others} recording or on other TVs)" if others else ")")
        else:
            line = f"Tuners: {n}{of} in use by this script"
        return line + (f" ({names})" if names else "")

    def busy_hint(self) -> str:
        """Extra words for a failed tune: who's holding tuners, as far as we know."""
        st = self.tuner_status()
        n, total = len(st["ours"]), st["total"]
        if total and n >= total:
            return (f" All {total} tuners are in use by this script; stop one "
                    f"(close a player or switch channels) and try again.")
        if total:
            return (f" This script is using {n} of {total} tuners; the others are probably "
                    f"recording or in use by the Tablo app on another TV.")
        return " All tuners may be busy (recording, or in use on another TV)."

    def watch_url(self, ident: str) -> str:
        """Tune a channel, recovering from common problems: FAST channels fall back to
        their direct stream; otherwise re-login (new address / fresh session) and retry."""
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


# =============================================================================
# SECTION 5: FAVORITES
# The 'favorites =' line in tablo.ini (channel numbers or call signs).
# =============================================================================
class Favorites:
    """The 'favorites' line in tablo.ini, e.g. favorites = 4.1, 9.1, KFOR.
    Edit it by hand, or use --add-fav / --remove-fav / +N / -N in the menu."""
    def __init__(self, cfg, tablo: Tablo):
        """Read the 'favorites' line from tablo.ini and match each entry to a channel.
        Entries that don't match are kept in .missing (shown to you, never deleted)."""
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
        """Favorite channels, in the order you listed them."""
        return list(self._chans)

    def others(self, chans):
        """chans minus favorites (so nothing is listed twice)."""
        return [c for c in chans if c.identifier not in self.ids]

    def add(self, ch) -> bool:
        """Add a favorite (not saved until save()). False if it was already one."""
        if ch.identifier in self.ids:
            return False
        self._chans.append(ch)
        self.ids.append(ch.identifier)
        return True

    def remove(self, ch) -> bool:
        """Remove a favorite (not saved until save()). False if it wasn't one."""
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


# =============================================================================
# SECTION 6: TV GUIDE + PLAYLISTS FOR IPTV APPS
# =============================================================================
# ----------------------------------------------------------------------
# TV guide (XMLTV) for IPTV apps: TiviMate, UHF, iPlayTV, Kodi, Televizo...
# Full listings for local channels and favorites; "now playing" for the rest.
# ----------------------------------------------------------------------
_xmltv_cache = {"at": 0.0, "xml": b""}
_xmltv_lock = threading.Lock()


GUIDE_REFRESH = 1800        # rebuild /guide.xml every 30 minutes


def xmltv(tablo, favs) -> bytes:
    """The XMLTV guide (/guide.xml) for IPTV apps. Building it takes one cloud
    request per channel per day, so with --lan it's built in the background at
    startup and kept fresh (start_guide_refresher). IPTV apps always get the
    ready copy at once; only the very first request ever waits for a build."""
    xml = _xmltv_cache["xml"]
    if xml:
        if time.time() - _xmltv_cache["at"] > GUIDE_REFRESH:      # stale: serve it, refresh behind
            threading.Thread(target=build_xmltv, args=(tablo, favs), daemon=True).start()
        return xml
    return build_xmltv(tablo, favs)


def build_xmltv(tablo, favs) -> bytes:
    """Build the guide now (one build at a time; others wait and reuse it)."""
    with _xmltv_lock:
        if _xmltv_cache["xml"] and time.time() - _xmltv_cache["at"] < 60:
            return _xmltv_cache["xml"]           # someone else just built it
        def esc(t):                     # make text safe inside XML
            return html.escape(str(t or ""), quote=True)
        def stamp(dt):                  # XMLTV time format: 20261019213000 +0000
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
    guide = f"http://{host}/guide.xml{kq()}"
    lines = [f'#EXTM3U url-tvg="{guide}" x-tvg-url="{guide}"']
    for group, chans in ordered(tablo, favs):
        if favorites_only and group != "Favorites":
            continue
        for c in chans:
            num = f"{c.major}.{c.minor}" if c.major else ""
            a = now.get(c.identifier)
            title = c.display_name + (f" · {a['show']}" if a else "")
            attrs = (f'tvg-id="{c.identifier}" tvg-name="{c.call_sign}"' + (f' tvg-chno="{num}"' if num else "")
                     + (f' tvg-logo="{c.logo}"' if c.logo else "") + f' group-title="{group}"')
            lines += [f"#EXTINF:-1 {attrs},{title}",
                      f"http://{host}/live/{urllib.parse.quote(c.identifier)}/{S.tv_quality}/index.m3u8{kq()}"]
    return ("\n".join(lines) + "\n").encode()


# =============================================================================
# SECTION 7: BROWSER/TV PLAYBACK (ffmpeg conversion)
# =============================================================================
# ----------------------------------------------------------------------
# Browser playback (iPhone Safari, Android Chrome, any browser)
# The Tablo 4th Gen sends the raw broadcast (usually MPEG-2 video), which phone
# browsers can't play. ffmpeg on this PC converts it on the fly to H.264/AAC HLS.
# ----------------------------------------------------------------------
HLS_JS_URL = "https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"
HLS_JS_FILE = DATA_DIR / "tablo-hls.min.js"


def kq(sep: str = "?") -> str:
    """'?key=…' to append to links we hand out, when an access key is set.
    IPTV apps, VLC and Chromecasts don't keep cookies, so links carry the key."""
    return f"{sep}key={urllib.parse.quote(S.access_key)}" if S.access_key else ""
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


# --- Video encoders for the phone/TV conversion -------------------------------
# Hardware encoders (your GPU) use far less CPU than software x264: better for
# laptops (battery, fans), headless boxes and several TVs at once. 'auto' tries
# the ones that fit this OS with a tiny test encode and uses the first that works;
# libx264 (software) always works as the fallback.
# Closed captions: broadcasts carry them inside the MPEG-2 video (ATSC A/53, "CC1").
# ffmpeg's decoder hands them to the encoder, and x264, NVENC, Quick Sync and VAAPI
# copy them into the H.264 video by default, so phones/TVs can show them. AMF
# (AMD on Windows) can't carry them: set encoder = x264 there if you need captions.
ENCODERS = {
    "x264": {"label": "software (libx264)", "pre": [], "vf_tail": "",
             "codec": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-maxrate", "5M",
                       "-bufsize", "10M", "-pix_fmt", "yuv420p", "-profile:v", "high", "-sc_threshold", "0"]},
    "vaapi": {"label": "VAAPI (Intel/AMD graphics)", "pre": ["-vaapi_device", "%DRI%"],
              "vf_tail": ",format=nv12,hwupload",
              "codec": ["-c:v", "h264_vaapi", "-qp", "23", "-profile:v", "high"]},
    "nvenc": {"label": "NVENC (NVIDIA graphics)", "pre": [], "vf_tail": "",
              "codec": ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                        "-maxrate", "6M", "-bufsize", "12M", "-pix_fmt", "yuv420p", "-profile:v", "high"]},
    "qsv": {"label": "Quick Sync (Intel graphics)", "pre": [], "vf_tail": ",format=nv12",
            "codec": ["-c:v", "h264_qsv", "-global_quality", "23", "-look_ahead", "0", "-profile:v", "high"]},
    "amf": {"label": "AMF (AMD graphics)", "pre": [], "vf_tail": "",
            "codec": ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "23", "-qp_p", "23",
                      "-pix_fmt", "yuv420p"]},
    "videotoolbox": {"label": "VideoToolbox (Mac)", "pre": [], "vf_tail": "",
                     "codec": ["-c:v", "h264_videotoolbox", "-b:v", "5M", "-pix_fmt", "yuv420p"]},
}
# What 'auto' tries, per OS, before falling back to x264.
AUTO_ORDER = (["nvenc", "qsv", "amf"] if IS_WINDOWS else
              ["videotoolbox"] if sys.platform == "darwin" else ["vaapi", "nvenc", "qsv"])


def render_node() -> str:
    """The Linux GPU device VAAPI uses (/dev/dri/renderD128 on most PCs)."""
    nodes = sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []
    return str(nodes[0]) if nodes else ""


def encoder_args(name: str, height: int):
    """(arguments before -i, the -vf filter chain, the video codec arguments)."""
    enc = ENCODERS[name]
    pre = [a.replace("%DRI%", render_node()) for a in enc["pre"]]
    vf = f"yadif=deint=interlaced,scale=-2:'min({height},ih)'" + enc["vf_tail"]
    return pre, vf, enc["codec"] + ["-g", "60", "-keyint_min", "60"]


def encoder_works(name: str) -> bool:
    """Try a half-second test encode with this encoder (takes ~1 s)."""
    if name == "vaapi" and not render_node():
        return False
    pre, vf, codec = encoder_args(name, 720)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *pre,
           "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "0.5",
           "-vf", vf, *codec, "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        if r.returncode != 0:
            log.debug(f"Encoder {name} not usable: {r.stderr.decode(errors='replace').strip()[-200:]}")
        return r.returncode == 0
    except Exception as e:
        log.debug(f"Encoder {name} test failed: {e}")
        return False


_encoder_lock = threading.Lock()


def choose_encoder() -> str:
    """Pick the encoder once (tablo.ini 'encoder', default auto) and remember it in S.encoder."""
    with _encoder_lock:
        if S.encoder:
            return S.encoder
        want = S.encoder_setting if S.encoder_setting in ENCODERS or S.encoder_setting == "auto" else "auto"
        if S.encoder_setting not in ENCODERS and S.encoder_setting != "auto":
            log.warning(f"encoder = {S.encoder_setting} isn't known; choose from auto, "
                        + ", ".join(ENCODERS) + ". Using auto.")
        tries = AUTO_ORDER if want == "auto" else [want]
        S.encoder = "x264"
        for name in tries:
            if name != "x264" and encoder_works(name):
                S.encoder = name
                break
            if want != "auto" and name != "x264":
                log.warning(f"encoder = {name} doesn't work on this PC; using software (libx264)")
        log.info(f"Video conversion for phones/TVs: {ENCODERS[S.encoder]['label']}")
        return S.encoder


class LiveJob:
    """One running ffmpeg converter: which channel/quality, its Tablo session token,
    its temp folder of video segments, and who's watching (viewer IP -> last seen)."""
    def __init__(self, ident, q, label, token, folder, proc, encoder="x264"):
        self.ident, self.q, self.label, self.token, self.folder, self.proc = ident, q, label, token, folder, proc
        self.encoder = encoder
        self.started = self.last = time.time()
        self.viewers: dict[str, float] = {}


class LiveTV:
    """One ffmpeg converter per channel, shared by everyone watching it in a browser.
    Stopped (and its Tablo tuner freed) ~45 s after the last viewer goes away, or
    as soon as that viewer switches channels."""
    IDLE = 45

    def __init__(self, tablo):
        """Start the idle-converter reaper and clean up folders from earlier crashes."""
        self.tablo = tablo
        self.jobs: dict[tuple, LiveJob] = {}       # (channel id, height) -> converter
        self.viewer_ch: dict[str, tuple] = {}       # viewer IP -> what they watch
        self.starting: dict[tuple, threading.Event] = {}   # tunes in progress
        self.lock = threading.Lock()
        self._clean_leftovers()
        threading.Thread(target=self._reaper, daemon=True).start()

    @staticmethod
    def _clean_leftovers(max_age: int = 3600) -> None:
        """Delete converter folders left behind if the script was killed hard.
        Only folders untouched for an hour, so another running copy is safe
        (an active converter rewrites its folder every 2 seconds)."""
        for d in Path(tempfile.gettempdir()).glob("tablo-live-*"):
            try:
                newest = max([d.stat().st_mtime] + [f.stat().st_mtime for f in d.iterdir()])
                if time.time() - newest > max_age:
                    shutil.rmtree(d, ignore_errors=True)
                    log.debug(f"Removed leftover {d}")
            except OSError:
                pass

    @staticmethod
    def available() -> bool:
        """Browser/TV playback needs ffmpeg on this PC."""
        return bool(shutil.which("ffmpeg"))

    def _start(self, ident, q) -> LiveJob:
        """(lock NOT held) Tune the Tablo and start ffmpeg converting that channel to
        H.264/AAC HLS in a temp folder: deinterlaced, scaled to q lines, 2-second segments."""
        ch = self.tablo.by_id(ident)
        url, token = self.tablo.watch_session(ident, who="phone/TV browser")
        folder = Path(tempfile.mkdtemp(prefix="tablo-live-"))
        height = max(240, min(int(q), 2160))
        encoder = choose_encoder()
        pre, vf, codec = encoder_args(encoder, height)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *pre,
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
               "-i", url, "-map", "0:v:0", "-map", "0:a:0?",
               "-vf", vf, *codec,                              # deinterlace + scale + H.264
               "-c:a", "aac", "-b:a", "160k", "-ac", "2",
               "-f", "hls", "-hls_time", "2", "-hls_list_size", "8",
               "-hls_flags", "delete_segments+independent_segments+omit_endlist",
               "-hls_segment_filename", str(folder / "seg%05d.ts"), str(folder / "index.m3u8")]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        label = ch.display_name if ch else ident
        threading.Thread(target=lambda: [log.warning(f"[{label}] browser ffmpeg: {l.decode(errors='replace').rstrip()}")
                                         for l in proc.stderr], daemon=True).start()
        log.info(f"[{label}] converting for browser playback ({height}p, {encoder})")
        return LiveJob(ident, q, label, token, folder, proc, encoder)

    # Locking rule: self.lock only guards the dictionaries. Anything slow
    # (tuning the Tablo, stopping ffmpeg, ending a Tablo session) happens with
    # the lock released, so one device starting a channel never makes other
    # devices' video requests wait.

    def _detach(self, key):
        """(lock held) Remove a converter from the table; returns it for _shutdown."""
        return self.jobs.pop(key, None)

    def _shutdown(self, job) -> None:
        """(lock NOT held) Stop ffmpeg, free the Tablo tuner, delete the temp folder."""
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
        key = (ident, int(q or S.browser_quality))
        to_stop = []
        while True:
            with self.lock:
                job = self.jobs.get(key)
                if job and job.proc.poll() is not None:     # ffmpeg died: restart it
                    if job.encoder != "x264" and time.time() - job.started < 20:
                        # A GPU encoder that passed the test but chokes on this broadcast
                        log.warning(f"{ENCODERS[job.encoder]['label']} failed on {job.label}; "
                                    f"switching to software encoding")
                        S.encoder = "x264"
                    to_stop.append(self._detach(key))
                    job = None
                if job:
                    break
                starting = self.starting.get(key)
                if not starting:                            # we are the one who tunes
                    starting = self.starting[key] = threading.Event()
                    break
            starting.wait(timeout=30)                       # someone else is tuning it
        if not job:
            try:
                new = self._start(*key)                     # slow: Tablo tune + ffmpeg
            finally:
                with self.lock:
                    self.starting.pop(key).set()
            with self.lock:
                job = self.jobs[key] = new
        with self.lock:
            now = time.time()
            job.last = now
            job.viewers[viewer] = now
            prev = self.viewer_ch.get(viewer)
            self.viewer_ch[viewer] = key
            if prev and prev != key and prev in self.jobs:  # viewer switched channels
                old = self.jobs[prev]
                old.viewers.pop(viewer, None)
                if not any(now - t < 15 for t in old.viewers.values()):
                    to_stop.append(self._detach(prev))
        for old_job in to_stop:
            self._shutdown(old_job)
        return job

    def get(self, ident, q=None):
        """The running converter for a channel/quality, if any."""
        return self.jobs.get((ident, int(q or S.browser_quality)))

    def _reaper(self) -> None:
        """Every 10 s: stop converters nobody has fetched video from for IDLE seconds."""
        while True:
            time.sleep(10)
            with self.lock:
                idle = [self._detach(k) for k, j in list(self.jobs.items())
                        if time.time() - j.last > self.IDLE]
            for job in idle:
                self._shutdown(job)

    def stop_all(self) -> None:
        """Stop every converter (used when the script quits)."""
        with self.lock:
            jobs = [self._detach(k) for k in list(self.jobs)]
        for job in jobs:
            self._shutdown(job)



# =============================================================================
# SECTION 8: WEB PAGES
# HTML/CSS/JavaScript for the pages, kept as strings with %%NAME%% blanks that
# fill() replaces. Braces { } are plain CSS/JS here, not Python formatting.
# =============================================================================
# ----------------------------------------------------------------------
# Channel page (/): phones, tablets and the PC
# ----------------------------------------------------------------------
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Tablo TV"><meta name="theme-color" content="#16181c">
<title>Tablo Channels</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mute:#666;--line:#e3e3e3;--acc:#e8590c}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#eee;--mute:#9a9a9a;--line:#2c2f36}}
body{background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;margin:0 auto;max-width:1600px;padding:16px}
h1{font-size:20px;margin:0 0 4px} h2{font-size:15px;margin:24px 0 6px;color:var(--mute);text-transform:uppercase;letter-spacing:.05em}
p{color:var(--mute);margin:0 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));column-gap:24px}
.row{display:flex;align-items:center;gap:10px;padding:6px 4px;border-bottom:1px solid var(--line);min-width:0}
.num{font-variant-numeric:tabular-nums;min-width:52px;font-weight:600}
.name{flex:1;min-width:0;display:flex;flex-direction:column} .net{color:var(--mute);font-size:13px}
.ch,.now{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.now{font-size:13px;color:var(--mute);cursor:help} .left{opacity:.8}
a{color:var(--acc);text-decoration:none}
.bar{position:sticky;bottom:0;background:var(--bg);padding:12px 0;display:flex;gap:12px;flex-wrap:wrap;align-items:center;border-top:1px solid var(--line)}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:15px;cursor:pointer}
#player{display:none;position:sticky;top:0;z-index:5;background:var(--bg);padding:0 0 10px;border-bottom:1px solid var(--line)}
#player video{width:100%;max-height:62vh;background:#000;border-radius:8px;display:block}
#player .ph{display:flex;align-items:center;gap:10px;padding:6px 0} #ptitle{flex:1;font-weight:600}
#pstate{color:var(--mute);font-size:13px} #pclose{padding:6px 12px;font-size:14px}
.asof{color:var(--mute);font-size:13px} button:disabled{opacity:.6}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:0 0 4px}
.top h1{margin:0} .gbar{display:flex;align-items:center;gap:10px;margin-left:auto}
.mv{font-size:14px;margin-right:6px} .gbar{flex-wrap:wrap;justify-content:flex-end}
.mv,.asof,#gref{white-space:nowrap}
#gref{padding:8px 14px;font-size:14px}
.saved{color:var(--acc)} .warn{border:1px solid var(--acc);border-radius:8px;padding:8px 12px;margin:8px 0;color:var(--fg)} .small{font-size:13px;margin-top:8px}
</style></head><body>
<div id="player"><div class="ph"><span id="ptitle"></span><span id="pstate"></span>
<google-cast-launcher id="castbtn" style="display:none;width:30px;height:30px;--connected-color:#e8590c;cursor:pointer"></google-cast-launcher>
<button id="pclose" type="button">Close</button></div><video id="video" controls playsinline autoplay x-webkit-airplay="allow"></video></div>
<div class="top"><h1>Tablo Channels</h1>
<div class="gbar">%%MULTI_LINK%%<span class="asof" id="asof">%%ASOF%%</span><button type="button" id="gref">↻ Refresh guide</button></div></div>
<p>Click Play to watch. Hover over a show for its description. Favorites come from the <b>favorites</b> line in tablo.ini.</p>
<div id="chans">
<h2>Favorites</h2>%%MISSING%%%%FAV%%
<h2>Local (OTA)</h2>%%OTA%%
<h2>FAST (online)</h2>%%OTT%%
</div>
<div class="bar">
<a href="#" class="play pconly" data-id="all">Open all (VLC)</a>
<a href="#" class="play pconly" data-id="favorites">Open favorites (VLC)</a>
<span class="saved" id="msg"></span></div>
<p class="small">%%PLAYER_NOTE%% VLC app on other devices: <a href="/playlist.m3u">all .m3u</a> &middot; <a href="/favorites.m3u">favorites .m3u</a></p>
<script>window.TABLO = %%TABLO_CFG%%;</script>
<script src="/hls.min.js"></script>
%%CAST_TAG%%<script>%%SCRIPT%%</script>
</body></html>"""

def fill(template: str, **values) -> str:
    """Fill %%NAME%% placeholders in a page template. Both web pages use this one
    style, so their CSS and JavaScript can use normal { } braces."""
    for name, value in values.items():
        template = template.replace(f"%%{name}%%", str(value))
    leftover = re.findall(r"%%[A-Z_]+%%", template)
    if leftover:
        raise ValueError(f"unfilled page placeholders: {leftover}")
    return template


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
.gbar{display:flex;align-items:center;gap:18px;margin:0 0 6px}
.gref{all:unset;box-sizing:border-box;background:#1b1f27;border-radius:12px;padding:10px 18px;font-size:19px;
  border:3px solid transparent;cursor:pointer} .gref:focus,.gref:hover{border-color:#87d7ff;background:#243041;outline:none}
.asof{color:#8b93a1;font-size:17px}
.warn{background:#3a2f12;color:#ffe08a;border-radius:10px;padding:12px 16px;margin:10px 0;font-size:19px}
#ov{display:none;position:fixed;inset:0;background:#000;z-index:10}
#ov video{width:100%;height:100%;background:#000}
#info{position:absolute;left:0;right:0;bottom:0;padding:28px 48px;background:linear-gradient(transparent,rgba(0,0,0,.85));
  transition:opacity .5s} #info.hide{opacity:0}
#ititle{font-size:32px;font-weight:700} #ishow{font-size:24px;color:#d5dbe6;margin-top:4px}
#istate{font-size:20px;color:#87d7ff;margin-top:6px} .keys{font-size:16px;color:#9aa3b2;margin-top:10px}
</style></head><body>
<h1>Tablo TV</h1><p class="sub">Use the arrow keys and OK to pick a channel. While watching: ▲▼ or CH+/− change channel, ◀ ▶ captions, OK shows info, Back returns here.</p>
<div class="gbar"><button id="gref" class="gref">↻ Refresh guide</button><span class="asof">%%ASOF%%</span></div>
%%NOTICE%%
%%SECTIONS%%
<div id="ov"><video id="v" playsinline autoplay x-webkit-airplay="allow"></video>
<div id="info"><div id="ititle"></div><div id="ishow"></div><div id="istate"></div>
<div class="keys">▲▼ / CH+ CH− change channel · ◀ ▶ captions on/off · OK info · Back close</div></div></div>
<script src="/hls.min.js"></script>
<script>
const TVQ = %%TVQ%%, KEY = %%KEY%%;   // KEY: access key, if one is set in tablo.ini
const GUIDE_MIN = %%GUIDE%%;          // tablo.ini guide_refresh (minutes; 0 = off)
let ccOn = %%CC%%;                     // closed captions showing? (tablo.ini captions; ◀ ▶ toggle)
const gref = document.getElementById("gref");
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
  const src = "/live/" + encodeURIComponent(t.dataset.id) + "/" + TVQ + "/index.m3u8" + (KEY ? "?key=" + encodeURIComponent(KEY) : "");
  if (v.canPlayType("application/vnd.apple.mpegurl")) { v.src = src; }
  else if (window.Hls && Hls.isSupported()) {
    hls = new Hls({liveSyncDurationCount: 3, manifestLoadingMaxRetry: 6, manifestLoadingRetryDelay: 1500,
                   enableCEA708Captions: true});      // the broadcast's closed captions
    hls.on(Hls.Events.ERROR, (e, d) => { if (d.fatal) { istate.textContent = "Playback error (" + d.details + ")"; showInfo(10000); } });
    hls.loadSource(src); hls.attachMedia(v);
  } else { istate.textContent = "This TV browser can't play live video."; return; }
  v.play().catch(() => { istate.textContent = "Press OK / Play to start"; });
}
function close() { stop(); ov.style.display = "none"; try { document.exitFullscreen && document.fullscreenElement && document.exitFullscreen(); } catch (e) {} if (cur >= 0) tiles[cur].focus(); }
// Closed captions: TV remotes have no CC button here, so ◀ or ▶ while watching turns them on/off.
function ccTracks() { return [...v.textTracks].filter(t => t.kind === "captions" || t.kind === "subtitles"); }
function applyCC() { ccTracks().forEach(t => t.mode = ccOn ? "showing" : "hidden"); }
v.textTracks.addEventListener("addtrack", applyCC);
function toggleCC() {
  ccOn = !ccOn; applyCC();
  istate.textContent = ccTracks().length ? (ccOn ? "Captions on" : "Captions off")
                                         : (ccOn ? "Captions on (none on this channel yet)" : "Captions off");
  showInfo(3000);
}
v.addEventListener("playing", () => { istate.textContent = ""; showInfo(4000); });
v.addEventListener("waiting", () => { istate.textContent = "Buffering…"; });
window.addEventListener("popstate", () => { if (ov.style.display === "block") close(); });
tiles.forEach((t, i) => t.addEventListener("click", () => play(i)));

// Arrow-key navigation between tiles (remote controls)
function move(dx, dy) {
  const nav = [gref, ...tiles];           // the Refresh guide button is reachable with ▲ too
  const a = document.activeElement, ar = nav.includes(a) ? a.getBoundingClientRect() : null;
  if (!ar) { tiles[0] && tiles[0].focus(); return; }
  const ax = ar.left + ar.width / 2, ay = ar.top + ar.height / 2;
  let best = null, bestScore = 1e9;
  for (const t of nav) {
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
    if (k === "ArrowLeft" || k === "ArrowRight" || k === "Subtitle" || k === "c") { e.preventDefault(); toggleCC(); return; }
    return;
  }
  const dirs = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
  if (dirs[k]) { e.preventDefault(); move(...dirs[k]); }
});
if (tiles.length) tiles[0].focus();
// Guide refresh: OK on the button asks the server to fetch what's on now, then reloads
// the list. The list also reloads every GUIDE_MIN minutes, but never while watching.
gref.addEventListener("click", async () => {
  gref.textContent = "Refreshing…";
  try { await fetch("/refresh-guide" + (KEY ? "?key=" + encodeURIComponent(KEY) : ""), {method: "POST"}); } catch (e) {}
  location.reload();
});
if (GUIDE_MIN > 0) setInterval(() => { if (ov.style.display !== "block") location.reload(); }, GUIDE_MIN * 60 * 1000);
</script></body></html>"""


def render_tv_page(tablo, favs) -> bytes:
    """The /tv page: one big focusable tile per channel, grouped like the menu."""
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
    return fill(TV_PAGE, SECTIONS="".join(sections), NOTICE=notice,
                TVQ=str(S.tv_quality), KEY=json.dumps(S.access_key),
                GUIDE=str(0 if tablo.lan_only else S.guide_refresh), CC=json.dumps(S.captions),
                ASOF=f"Guide as of {tablo.guide_time()}" if tablo.guide_time() else "").encode()


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
  const mi = new chrome.cast.media.MediaInfo(TABLO.lan + "/live/" + encodeURIComponent(c.id) + "/" + TABLO.tvq + "/index.m3u8"
                                             + (TABLO.key ? "?key=" + encodeURIComponent(TABLO.key) : ""),
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
  const src = "/live/" + encodeURIComponent(id) + "/index.m3u8" + (TABLO.key ? "?key=" + encodeURIComponent(TABLO.key) : "");
  if (video.canPlayType("application/vnd.apple.mpegurl")) {          // iPhone/iPad Safari
    video.src = src;
  } else if (window.Hls && Hls.isSupported()) {                        // Android Chrome, desktop
    hls = new Hls({liveSyncDurationCount: 3, manifestLoadingMaxRetry: 6, manifestLoadingRetryDelay: 1500,
                   enableCEA708Captions: true});      // the broadcast's closed captions
    hls.on(Hls.Events.ERROR, (e, d) => { if (d.fatal) pstate.textContent = "Playback error (" + d.details + ")"; });
    hls.loadSource(src); hls.attachMedia(video);
  } else {
    pstate.textContent = "This browser can't play live video. Try Safari or Chrome.";
    return;
  }
  video.play().catch(() => { pstate.textContent = "Tap ▶ to start"; });
}
// Closed captions show up as a text track once the video starts; the player's own
// CC button turns them on/off. captions = on in tablo.ini starts with them showing.
video.textTracks.addEventListener("addtrack", e => {
  if (e.track.kind === "captions" || e.track.kind === "subtitles") e.track.mode = TABLO.cc ? "showing" : "hidden";
});
video.addEventListener("playing", () => pstate.textContent = "");
video.addEventListener("waiting", () => pstate.textContent = "Buffering…");
document.getElementById("pclose").onclick = () => { stopVideo(); player.style.display = "none"; };

function bindPlay(root) { root.querySelectorAll("a.play").forEach(a => a.addEventListener("click", async e => {
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
})); }
bindPlay(document);

// ---- Guide refresh: the button asks the server to fetch what's on now; then (and
// every guide_refresh minutes on its own) the channel list is swapped in place, so
// a video that's playing keeps playing.
const chans = document.getElementById("chans"), gref = document.getElementById("gref");
const asof = document.getElementById("asof");
async function refreshGuide(force) {
  try {
    if (force) {
      gref.disabled = true; gref.textContent = "Refreshing…";
      const r = await fetch("/refresh-guide" + location.search, {method: "POST"});
      if (!r.ok) throw new Error(await r.text());
    }
    const page = await (await fetch(location.pathname + location.search, {cache: "no-store"})).text();
    const doc = new DOMParser().parseFromString(page, "text/html");
    const fresh = doc.getElementById("chans");
    if (!fresh) return;
    chans.innerHTML = fresh.innerHTML; bindPlay(chans);
    asof.textContent = doc.getElementById("asof").textContent;
  } catch (err) { if (force) msg.textContent = "Couldn't refresh the guide: " + err.message; }
  finally { gref.disabled = false; gref.textContent = "↻ Refresh guide"; }
}
gref.onclick = () => refreshGuide(true);
if (TABLO.guide > 0) setInterval(() => refreshGuide(false), TABLO.guide * 60000);
// ?play=<id> link (e.g. a bookmark) starts that channel
const want = new URLSearchParams(location.search).get("play");
if (want) { const a = document.querySelector('a.play[data-id="' + CSS.escape(want) + '"]'); if (a) watchHere(want, a.dataset.label); }
"""


# ----------------------------------------------------------------------
# Multi-view page (/multi): 2 or 4 live channels at once in a browser (PC, tablet,
# phone). Each player is its own converter + Tablo tuner; one has sound at a time.
# ----------------------------------------------------------------------
MULTI_PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Tablo Multi-view</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mute:#666;--line:#e3e3e3;--acc:#e8590c;--tile:#f3f3f3}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#eee;--mute:#9a9a9a;--line:#2c2f36;--tile:#1f2228}}
body{background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;margin:0;padding:12px}
.top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 10px}
h1{font-size:19px;margin:0 auto 0 0} a{color:var(--acc);text-decoration:none}
button{background:var(--tile);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:7px 12px;font-size:14px;cursor:pointer}
button.sel{background:var(--acc);border-color:var(--acc);color:#fff}
#grid{display:grid;gap:10px;grid-template-columns:1fr 1fr}
#grid:fullscreen{background:#000;padding:8px;gap:8px;align-content:center}
@media (max-width:700px) and (orientation:portrait){#grid.g2{grid-template-columns:1fr}}
.tile{background:var(--tile);border:3px solid transparent;border-radius:10px;padding:6px;min-width:0;display:flex;flex-direction:column;gap:6px}
.tile.on{border-color:var(--acc)}
.bar{display:flex;align-items:center;gap:8px} select{flex:1;min-width:0;font-size:14px;padding:5px;border-radius:6px;
  background:var(--bg);color:var(--fg);border:1px solid var(--line)}
.snd{font-size:13px;color:var(--mute);white-space:nowrap;cursor:pointer}
.tile.on .snd{color:var(--acc);font-weight:600}
.vid{position:relative} video{width:100%;aspect-ratio:16/9;background:#000;border-radius:6px;display:block;cursor:pointer}
.st{position:absolute;left:8px;bottom:8px;color:#fff;font-size:13px;background:rgba(0,0,0,.55);padding:2px 8px;border-radius:5px}
.st:empty{display:none} .note{color:var(--mute);font-size:13px;margin-top:10px}
</style></head><body>
<div class="top"><h1>Multi-view</h1>
<button id="n2" type="button">2 channels</button><button id="n4" type="button">4 channels</button>
<button id="fs" type="button">⛶ Full screen</button><a href="/">Channel list</a></div>
<div id="grid"></div>
<p class="note">Tap a picture to hear it (one channel has sound at a time). Each channel uses one of your Tablo's
tuners, so 2 channels need 2 free tuners and 4 need 4. They stop about a minute after you close this page.</p>
<script src="/hls.min.js"></script>
<script>
const CH = %%CHANNELS%%, KEY = %%KEY%%;
const grid = document.getElementById("grid");
let n = 2, audio = 0, tiles = [], picks = [];
try { const s = JSON.parse(localStorage.getItem("tablo-multi") || "{}"); n = s.n === 4 ? 4 : 2; picks = s.picks || []; } catch (e) {}
const q = new URLSearchParams(location.search);
if (q.get("n") === "4") n = 4; if (q.get("ch")) picks = q.get("ch").split(",");
function save() { try { localStorage.setItem("tablo-multi", JSON.stringify({n: n, picks: picks})); } catch (e) {} }
function src(id, slot) {             // 4 at once: smaller pictures (540p) to keep the PC's work down
  return "/live/" + encodeURIComponent(id) + (n > 2 ? "/540" : "") + "/index.m3u8?slot=" + slot
         + (KEY ? "&key=" + encodeURIComponent(KEY) : "");
}
function options(sel) {
  let group = null, og = null;
  sel.add(new Option("— pick a channel —", ""));
  for (const c of CH) {
    if (c.group !== group) { group = c.group; og = document.createElement("optgroup"); og.label = group; sel.appendChild(og); }
    og.appendChild(new Option(c.label + (c.show ? " · " + c.show : ""), c.id));
  }
}
function stopTile(t) { if (t.hls) { t.hls.destroy(); t.hls = null; } t.v.pause(); t.v.removeAttribute("src"); t.v.load(); }
function load(i) {
  const t = tiles[i]; stopTile(t); picks[i] = t.sel.value; save();
  if (!t.sel.value) { t.st.textContent = ""; return; }
  t.st.textContent = "Tuning…";
  const url = src(t.sel.value, i + 1);
  if (t.v.canPlayType("application/vnd.apple.mpegurl")) t.v.src = url;
  else if (window.Hls && Hls.isSupported()) {
    t.hls = new Hls({liveSyncDurationCount: 3, manifestLoadingMaxRetry: 6, manifestLoadingRetryDelay: 1500});
    t.hls.on(Hls.Events.ERROR, (e, d) => { if (d.fatal) t.st.textContent = "Error (" + d.details + ") – all tuners busy?"; });
    t.hls.loadSource(url); t.hls.attachMedia(t.v);
  } else { t.st.textContent = "This browser can't play live video."; return; }
  t.v.muted = i !== audio; t.v.play().catch(() => {});
}
function setAudio(i) {
  audio = i;
  tiles.forEach((t, j) => { t.v.muted = j !== i; t.el.classList.toggle("on", j === i);
                            t.snd.textContent = j === i ? "🔊 sound" : "🔇 tap for sound"; });
  tiles[i].v.play().catch(() => {});
}
function build() {
  tiles.forEach(stopTile); grid.innerHTML = ""; tiles = [];
  grid.className = "g" + n;
  document.getElementById("n2").classList.toggle("sel", n === 2);
  document.getElementById("n4").classList.toggle("sel", n === 4);
  const defaults = CH.map(c => c.id);
  for (let i = 0; i < n; i++) {
    const el = document.createElement("div"); el.className = "tile";
    el.innerHTML = '<div class="bar"><select></select><span class="snd"></span></div>'
                 + '<div class="vid"><video playsinline muted autoplay x-webkit-airplay="allow"></video><span class="st"></span></div>';
    grid.appendChild(el);
    const t = {el: el, sel: el.querySelector("select"), v: el.querySelector("video"), st: el.querySelector(".st"),
               snd: el.querySelector(".snd"), hls: null};
    options(t.sel);
    const want = picks[i] && CH.some(c => c.id === picks[i]) ? picks[i]
               : defaults.find(id => !picks.slice(0, i).includes(id) && !tiles.some(x => x.sel.value === id)) || "";
    t.sel.value = want;
    t.sel.onchange = () => load(tiles.indexOf(t));
    t.v.onclick = t.snd.onclick = () => setAudio(tiles.indexOf(t));
    t.v.addEventListener("playing", () => t.st.textContent = "");
    t.v.addEventListener("waiting", () => t.st.textContent = "Buffering…");
    tiles.push(t);
  }
  tiles.forEach((t, i) => load(i));
  setAudio(Math.min(audio, n - 1));
}
document.getElementById("n2").onclick = () => { n = 2; save(); build(); };
document.getElementById("n4").onclick = () => { n = 4; save(); build(); };
document.getElementById("fs").onclick = () => {
  const f = grid.requestFullscreen || grid.webkitRequestFullscreen; if (f) f.call(grid);
};
build();
</script></body></html>"""


def render_multi_page(tablo, favs) -> bytes:
    """/multi: the multi-view page, with every channel (favorites first) in each picker."""
    now = tablo.now_playing()
    chans = []
    for group, cs in ordered(tablo, favs):
        if group.startswith("FAST") and tablo.lan_only:
            continue
        for c in cs:
            a = now.get(c.identifier)
            chans.append({"id": c.identifier, "group": group,
                          "label": (f"{c.major}.{c.minor} " if c.major else "") + c.call_sign,
                          "show": a["show"] if a else ""})
    # json.dumps output is safe inside <script> once "</" is escaped
    data = json.dumps(chans).replace("</", "<\\/")
    return fill(MULTI_PAGE, CHANNELS=data, KEY=json.dumps(S.access_key)).encode()


def render_page(tablo: Tablo, favs: Favorites) -> bytes:
    """The main web page: channel list with Play buttons, the inline browser
    player (phones/tablets), Cast/AirPlay support and links for other devices."""
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
            + (" (and can Cast/AirPlay to a TV)." if S.lan_base else ".") if LiveTV.available()
            else "To watch in a phone/tablet browser, install ffmpeg on this PC.")
    if S.lan_base and LiveTV.available():
        k = html.escape(kq())          # so copied links work in IPTV apps too
        note += (f' TVs: <a href="/tv{k}">TV page</a> &middot; IPTV apps: <a href="/tv.m3u{k}">tv.m3u</a> + '
                 f'<a href="/guide.xml{k}">guide.xml</a>.')
    cfg = json.dumps({"lan": S.lan_base, "tvq": S.tv_quality, "key": S.access_key,
                      "guide": 0 if tablo.lan_only else S.guide_refresh, "cc": S.captions})
    cast_tag = ('<script src="https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1"></script>'
                if S.lan_base and LiveTV.available() and tablo.online else "")
    return fill(PAGE,
        SCRIPT=PAGE_SCRIPT, PLAYER_NOTE=note, TABLO_CFG=cfg, CAST_TAG=cast_tag,
        ASOF=f"Guide as of {tablo.guide_time()}" if tablo.guide_time() else "",
        MULTI_LINK='<a href="/multi" class="mv">▦ Multi-view</a>' if LiveTV.available() else "",
        MISSING=missing,
        FAV=section(favs.channels(), "None set. Add e.g. <code>favorites = 4.1, 9.1</code> to tablo.ini."),
        OTA=section(favs.others(tablo.ota), "None found."),
        OTT=section(favs.others(tablo.ott), "None found."),
    ).encode()


# =============================================================================
# SECTION 9: WEB SERVER
# Every URL VLC, browsers, TVs and IPTV apps use. make_handler() builds a
# Handler class around one Tablo + Favorites; each request runs in its own
# thread. Order for every request: check the access key -> route by path.
# =============================================================================
def make_handler(tablo: Tablo, favs: Favorites, port: int):
    """Build the web server's request handler. Everything other devices see goes
    through here: pages, playlists, the guide, VLC streams (/ch) and browser/TV
    video (/live). Also tracks which device watches what, to free tuners."""
    live = LiveTV(tablo)
    sessions: dict[str, str] = {}      # viewer IP -> Tablo session token
    sessions_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        """One instance per web request (ThreadingHTTPServer runs them in threads)."""
        def log_message(self, fmt, *args):
            """Silence the default per-request console logging."""
            pass  # keep the console quiet

        # ---- access key (optional, tablo.ini access_key) ----------------------
        def from_this_pc(self) -> bool:
            """Request came from this computer itself (always trusted)."""
            return self.client_address[0] in ("127.0.0.1", "::1")

        def query_value(self, name: str) -> str:
            """One ?name=value from the request's address ('' if absent)."""
            return (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get(name) or [""])[0]

        def given_key(self) -> str:
            """Key from ?key=… or from the tablo_key cookie set on an earlier visit."""
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("key")
            if q:
                return q[0]
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return cookie["tablo_key"].value if "tablo_key" in cookie else ""

        def authorized(self) -> bool:
            """The PC itself is always allowed; other devices need the key, if one is set."""
            if not S.access_key or self.from_this_pc():
                return True
            return hmac.compare_digest(self.given_key().encode(), S.access_key.encode())

        def deny(self):
            """401 for devices without the access key (the key itself is never logged)."""
            log.warning(f"Refused {self.client_address[0]} (missing or wrong access key)")
            self.send_body(b"This Tablo server needs an access key. Open the address shown when "
                           b"tablo-vlc starts (it ends with ?key=...).", "text/plain; charset=utf-8", 401)

        def send_body(self, body: bytes, ctype: str, code: int = 200, cors: bool = False):
            """Send a complete response. cors=True lets Cast receivers and IPTV apps read it."""
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
            # First visit with ?key=… on a page: remember it on that device for a year,
            # so the page's own video requests (and bookmarks) work without the key.
            if S.access_key and ctype.startswith("text/html") and "key=" in self.path and self.authorized():
                self.send_header("Set-Cookie", f"tablo_key={urllib.parse.quote(S.access_key)}; "
                                               f"Max-Age=31536000; Path=/; SameSite=Lax; HttpOnly")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            """Route a GET request. Main paths:
            /                 web page (phones/PC)      /tv            TV page
            /playlist.m3u     VLC playlist              /tv.m3u        IPTV playlist
            /guide.xml        XMLTV guide               /ch/<id>       stream for VLC
            /live/<id>/...    converted video for browsers, TVs and Cast"""
            path = self.path.split("?")[0]
            if path != "/hls.min.js" and not self.authorized():   # the player library is public
                return self.deny()
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
            if path in ("/multi", "/multi/"):
                if not LiveTV.available():
                    return self.send_body(b"Install ffmpeg on the PC for multi-view.", "text/plain", 501)
                return self.send_body(render_multi_page(tablo, favs), "text/html; charset=utf-8")
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
                q = S.browser_quality
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
            # Multi-view: each player on the /multi page adds ?slot=N, so one device can
            # watch several channels at once (otherwise starting a channel on a device
            # frees whatever that device was watching before).
            slot = self.query_value("slot")
            slot = slot if slot.isdigit() and 1 <= int(slot) <= 4 else ""
            viewer = self.client_address[0] + (f"#{slot}" if slot else "")
            # A player first asks for index.m3u8 (the list of segments), then fetches
            # each segNNNNN.ts it lists, and re-asks for index.m3u8 every few seconds
            # to learn about new ones. Each request also says "this viewer is still here".
            if name == "index.m3u8":
                if ch.kind != "ota" and not tablo.maybe_reconnect(min_gap=15):
                    return self.send_body(b"FAST channels need the internet.", "text/plain", 503)
                try:
                    job = live.touch(ident, viewer, q)
                except Exception as e:
                    log.error(f"[{ch.display_name}] could not tune for browser: {e}.{tablo.busy_hint()}")
                    return self.send_body(b"Tablo could not tune (all tuners busy?)", "text/plain", 503)
                playlist = job.folder / "index.m3u8"
                # Wait (up to ~25 s) for ffmpeg's first segment: answering with an empty
                # playlist makes some players give up instead of retrying.
                for _ in range(100):                      # first segments take a few seconds
                    if playlist.exists() and b".ts" in playlist.read_bytes():
                        break
                    if job.proc.poll() is not None:
                        return self.send_body(b"Converter stopped; check tablo-vlc.log", "text/plain", 503)
                    time.sleep(0.25)
                else:
                    return self.send_body(b"Still starting; try again", "text/plain", 503)
                body = playlist.read_bytes()
                # Players don't send cookies for segments, and the slot must come along
                # too: add ?key=…&slot=N to each segment line.
                extra = "&".join(x for x in (kq(""), f"slot={slot}" if slot else "") if x)
                if extra:
                    body = re.sub(rb"(?m)^(seg\d+\.ts)$", lambda m: m.group(1) + b"?" + extra.encode(), body)
                return self.send_body(body, "application/vnd.apple.mpegurl", cors=True)
            name = name.split("?")[0]
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
            """Link (and label) for a channel, 'all' or 'favorites', on the given host."""
            if ident == "all":
                return f"http://{host}/playlist.m3u{kq()}", "all channels"
            if ident == "favorites":
                return f"http://{host}/favorites.m3u{kq()}", "favorites"
            ch = tablo.by_id(ident)
            return (f"http://{host}/ch/{ident}{kq()}", ch.display_name) if ch else (None, None)

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
                why = ("ffplay can't open a channel list; pick a single channel." if S.player
                       else "No video player found. Install VLC or ffmpeg (ffplay).")
                return self.send_body(why.encode(), "text/plain", 500)
            self.send_body(f"Opening {label} in {player_name()}".encode(), "text/plain")

        def single_m3u(self, ident: str):
            """A one-channel playlist file (for devices that can't be told to open VLC)."""
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
            """POST /play/<id>: open a channel in the player on this PC (from the web page).
            POST /refresh-guide: fetch what's on now again (web button, --refresh-guide)."""
            if not self.authorized():
                return self.deny()
            if self.path.split("?")[0] == "/refresh-guide":    # Refresh guide button / --refresh-guide
                now = tablo.refresh_guide()
                return self.send_body(json.dumps({"updated": tablo.guide_time(), "channels": len(now)}).encode(),
                                      "application/json")
            if self.path.startswith("/play/"):
                return self.play(urllib.parse.unquote(self.path.split("?")[0][6:]))
            self.send_error(404)

        def playlist(self, all_channels: bool):
            """VLC playlist: favorites, local, FAST, with show titles and VLC tuning options."""
            host = self.headers.get("Host", f"127.0.0.1:{port}")
            lines = ["#EXTM3U"]

            now = tablo.now_playing()

            def add(ch, group):
                lines.extend(m3u_entry(ch, now.get(ch.identifier), f"http://{host}/ch/{ch.identifier}{kq()}", group))

            for ch in favs.channels():
                add(ch, "Favorites")
            if all_channels:
                for ch in favs.others(tablo.ota):
                    add(ch, "Local (OTA)")
                for ch in favs.others(tablo.ott):
                    add(ch, "FAST (online)")
            self.send_body(("\n".join(lines) + "\n").encode(), "audio/x-mpegurl")

        def stream(self, ident: str):
            """/ch/<id>: tune a channel for VLC. Without ffmpeg, redirect VLC straight to
            the Tablo's stream; with ffmpeg, relay it unchanged. Either way, switching
            channels on the same device first frees that device's previous tuner."""
            ch = tablo.by_id(ident)
            if not ch:
                return self.send_error(404, "Unknown channel")
            # ?w=2 = the menu's second player window: its own tuner, so it doesn't
            # replace what the first window is showing.
            win = self.query_value("w")
            win = win if win in ("2",) else ""
            viewer = self.client_address[0] + (f"#w{win}" if win else "")
            # Switching channels on the same device: free the old tuner first.
            with sessions_lock:
                old = sessions.pop(viewer, "")
            if old:
                tablo.end_session(old)
            try:
                here = self.client_address[0] in ("127.0.0.1", "::1")
                who = ("player window 2" if win else "VLC") + (" on this PC" if here else f" on {self.client_address[0]}")
                url, token = tablo.watch_session(ident, who=who)
            except Exception as e:
                log.error(f"[{ch.display_name}] could not tune: {e}.{tablo.busy_hint()}")
                return self.send_error(503, "Tablo could not tune (all tuners busy?)")
            with sessions_lock:
                sessions[viewer] = token

            log.debug(f"[{ch.display_name}] Tablo stream URL: {url}")
            if not S.use_ffmpeg:
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
        """Free every tuner we're holding (used when the script quits)."""
        live.stop_all()
        with sessions_lock:
            tokens = list(sessions.values())
            sessions.clear()
        for t in tokens:
            tablo.end_session(t)

    Handler.end_all_sessions = staticmethod(end_all)
    return Handler


# =============================================================================
# SECTION 10: PLAYERS + COMMANDS
# --setup, printed VLC commands, and finding/launching VLC, ffplay or mpv.
# =============================================================================
def cmd_setup(cfg):
    """--setup: ask for the Tablo email and password, save, and test the login."""
    t = cfg["tablo"]
    t["email"] = input(f"Tablo account email [{t.get('email', '')}]: ").strip() or t.get("email", "")
    pw = getpass.getpass("Tablo password (blank = keep current): ")
    if pw:
        t["password"] = pw
        if keyring_available():
            ans = input("Keep the password in your system keyring instead of tablo.ini? [y/N]: ")
            if ans.strip().lower().startswith("y"):
                if keyring_set(t["email"], pw):
                    t["password"] = "keyring"
                    print("Stored in your keyring; tablo.ini now says 'password = keyring'.")
                else:
                    print("Couldn't use the keyring; saving the password in tablo.ini instead.")
    t.setdefault("port", str(DEFAULT_PORT))
    save_config(cfg)
    try:
        Tablo(t["email"], get_password(t))  # verify login works
    except Exception as e:
        log.error(f"Saved your login, but could not connect to Tablo: {e}")
        sys.exit(1)
    print(f"\nSaved to {CONFIG_FILE}.")


def print_vlc_commands(tablo: "Tablo", port: int) -> None:
    """One VLC command per local OTA channel, via this script's server."""
    lines = ["VLC commands for local OTA channels (this script must be running):"]
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
    """Name of the chosen player for messages ('VLC', 'ffplay', ...)."""
    return S.player["name"] if S.player else "VLC"


class VLCControl:
    """Runs one VLC window for this script and switches channels in it through
    VLC's built-in web remote (localhost only, random password): the playlist is
    replaced, not added to. Falls back to closing and reopening VLC."""

    def __init__(self):
        """No VLC yet; it's started on the first channel you pick."""
        self.proc = None
        self.port = 0
        self.pw = ""

    def _alive(self) -> bool:
        """Our VLC window is still open."""
        return bool(self.proc and self.proc.poll() is None)

    def _cmd(self, path: str) -> bool:
        """Send one command to our VLC through its web remote (localhost + password)."""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/requests/status.json?{path}")
        req.add_header("Authorization", "Basic " + __import__("base64").b64encode(f":{self.pw}".encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status == 200
        except Exception as e:
            log.debug(f"VLC remote: {e}")
            return False

    def play(self, base_cmd: list, url: str, options: dict, title: str) -> bool:
        """Play url in our VLC window: replace what's playing if VLC is open,
        otherwise start VLC (with its web remote on a free local port)."""
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


VLC = VLCControl()          # the menu's player window
VLC2 = VLCControl()         # the second window (menu: w 4.1), for two channels at once


def launch_player(url: str, meta: dict | None = None, playlist: bool = False, detach: bool = False,
                  window: int = 1) -> bool:
    """Open url in the chosen player. meta = guide info (title/description).
    VLC: one window, channel replaced on each pick. ffplay: old window closed first.
    detach=True (--play): a plain VLC launch that outlives the script.
    window=2: the menu's second window (w 4.1), so two channels play side by side."""
    if not S.player:
        log.warning("No video player found. Install VLC (sudo apt install vlc, or "
                    "flatpak install flathub org.videolan.VLC) or ffmpeg, which includes ffplay "
                    "(sudo apt install ffmpeg), or set 'player =' in tablo.ini.")
        return False
    kind, title = S.player["kind"], (meta or {}).get("meta-title", "")
    if playlist and kind == "ffplay":
        log.warning("ffplay can't open a channel list; pick a single channel instead "
                    "(or install VLC for playlists).")
        return False
    log.info(f"Launching: {S.player['name']} {url}" + (f"  [{title}]" if title else ""))
    if kind == "vlc":
        options = {**(meta or {}), **vlc_tuning()}
        if not detach:
            return (VLC2 if window == 2 else VLC).play(S.player["cmd"], url, options, title)
        cmd = [*S.player["cmd"], "--one-instance", url, *[f":{k}={v}" for k, v in options.items()]]
    elif kind == "ffplay":
        cmd = [*S.player["cmd"], "-hide_banner", "-loglevel", "error",
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
               "-window_title", title or "Tablo", url]
        last = S.last_ffplay2 if window == 2 else S.last_ffplay
        if last and last.poll() is None:
            last.terminate()            # one window per slot, like VLC
    elif kind == "mpv":
        cmd = [*S.player["cmd"], *([f"--force-media-title={title}"] if title else []), url]
    else:
        cmd = [*S.player["cmd"], url]
    proc = spawn(cmd)
    if kind == "ffplay":
        if window == 2:
            S.last_ffplay2 = proc
        else:
            S.last_ffplay = proc
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
    for player in (VLC, VLC2):
        if player.proc and player.proc.poll() is None:
            player.proc.terminate()
    server.shutdown()
    log.info("Stopped.")


# =============================================================================
# SECTION 11: TERMINAL MENU
# The numbered channel list in columns, colors, the details view, and the
# menu loop (menu()). Channel numbers typed here map to Channel objects.
# =============================================================================
def ordered(tablo: "Tablo", favs: "Favorites"):
    """(section, channels) in display order: favorites, then the rest."""
    return [("Favorites", favs.channels()),
            ("Local (OTA)", favs.others(tablo.ota)),
            ("FAST (online)", favs.others(tablo.ott))]


def menu_numbers(tablo, favs):
    """{menu number: channel} in the same order the menu shows them."""
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



# ANSI styles for the terminal list
LIGHT_BLUE = "\033[1;38;5;117m"  # bold light blue (256-color terminals, which Ubuntu's are)
FAV_NUM = LIGHT_BLUE        # favorites: numbers
FAV_NAME = LIGHT_BLUE       # favorites: channel + show
HEAD = "\033[1m"           # section headings: bold
FAV_HEAD = LIGHT_BLUE       # "★ Favorites" heading
WARN = "\033[1;33m"        # OFFLINE notice: bold yellow
DIM = "\033[2m"
ADDRESS = "\033[1;38;5;120m"  # web/IP addresses in startup messages: bold light green
RESET = "\033[0m"


def paint(text: str, style: str) -> str:
    """Color text for the terminal (if colors are on)."""
    return f"{style}{text}{RESET}" if S.use_color and style else text


def short_name(ch) -> str:
    """KWTV-DT -> KWTV for local channels; FAST channels keep their name."""
    return re.sub(r"-(DT|HD)\d*$", "", ch.call_sign) if ch.kind == "ota" else ch.call_sign


def clip(text: str, width: int) -> str:
    """Shorten text to width, ending with an ellipsis if it was cut."""
    return text if len(text) <= width else text[:max(1, width - 1)] + "…"


def print_columns(cells, now=None, fav=False) -> None:
    """Print (number, channel) cells down-then-across in as many columns as fit.
    With guide info, each cell also shows what's on now."""
    now = now or {}
    width = shutil.get_terminal_size((100, 24)).columns
    fixed = 5 + 7 + 2                     # " 12) " + "702.1  " + gap between columns
    min_name, max_name = (26, 44) if now else (14, 24)
    cols = S.columns or max(1, (width - 2) // (fixed + min_name))
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


def when(a: dict) -> str:
    """'NOW', '8:00 PM' (today) or 'Tue 8:00 PM' (another day) for an airing."""
    start, end = a.get("_start"), a.get("_end")
    t = datetime.now(timezone.utc)
    if start and end and start <= t < end:
        return "NOW"
    if not start:
        return ""
    local = start.astimezone()
    return a["start"] if local.date() == datetime.now().date() else f"{local:%a} {a['start']}"


def search_guide(tablo, favs, query: str) -> list:
    """Find shows matching `query` (title, episode, genre or type, any case).
    What's on now is searched on every channel (one cloud request). Upcoming shows
    are searched on your favorites, plus any channel whose schedule is already
    loaded (fetching the full schedule of 100+ channels would take hundreds of
    requests). Returns [(channel, airing)] sorted by start time."""
    words = query.lower().split()
    def match(a):
        hay = " ".join((a["show"], a["episode"], a["genres"], a["kind"])).lower()
        return all(w in hay for w in words)
    found, seen = [], set()
    for ident, a in tablo.now_playing().items():
        ch = tablo.by_id(ident)
        if ch and match(a):
            found.append((ch, a))
            seen.add((ident, a["_start"]))
    cached = set(getattr(tablo, "_air_cache", {}) or {})
    for ch in tablo.channels:
        if ch.identifier not in cached and ch.identifier not in favs.ids:
            continue
        for a in tablo.upcoming(ch.identifier, count=50):
            if match(a) and (ch.identifier, a["_start"]) not in seen:
                found.append((ch, a))
                seen.add((ch.identifier, a["_start"]))
    far = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(found, key=lambda x: (x[1]["_start"] or far, x[0].major, x[0].minor))


def print_search(tablo, favs, query: str, numbered: dict | None = None) -> None:
    """Show guide search results with menu numbers you can type to watch."""
    if tablo.lan_only:
        print("No guide in --offline mode (it comes from the internet).")
        return
    hits = search_guide(tablo, favs, query)
    number_of = {ch.identifier: n for n, ch in (numbered or menu_numbers(tablo, favs)).items()}
    width = shutil.get_terminal_size((100, 24)).columns
    if not hits:
        print(f"\nNo shows matching '{query}' right now, or coming up on your favorites.")
        return
    print(f"\n{len(hits)} show{'s' if len(hits) != 1 else ''} matching '{query}':")
    for ch, a in hits[:40]:
        num = f"{ch.major}.{ch.minor}" if ch.major else ""
        n = number_of.get(ch.identifier)
        left = f"  ({time_left(a)})" if when(a) == "NOW" and time_left(a) else ""
        line = f"  {when(a):>12}  {f'{n})' if n else '':>5} {num:<6} {short_name(ch)} · {airing_line(a)}{left}"
        print(paint(clip(line, width), HEAD) if when(a) == "NOW" else clip(line, width))
    if len(hits) > 40:
        print(f"  ...and {len(hits) - 40} more. Try more words to narrow it down.")
    print("Type a number or channel to watch. (Coming-up shows are searched on your favorites.)")


def print_tuners(tablo, detail: bool = False) -> None:
    """The menu's tuner line; with detail=True (menu 't'), one line per tuner in use."""
    print(paint(tablo.tuner_summary() + ("" if detail else "   (t = details)"), DIM))
    if not detail:
        return
    st = tablo.tuner_status()
    head = f"\n{tablo.model}" if tablo.model else "\nYour Tablo"
    print(head + (f": {st['total']} tuners" if st["total"] else " (tuner count not known yet)"))
    for ch, who, since in sorted(st["ours"], key=lambda o: o[2]):
        mins = int((time.time() - since) // 60)
        num = f"{ch.major}.{ch.minor}" if ch.major else ""
        print(f"  {num:<6} {short_name(ch):<12} {who:<22} {mins} min")
    if not st["ours"]:
        print("  None in use by this script.")
    if st["busy"] is not None:
        others = max(0, st["busy"] - len(st["ours"]))
        print(f"  The Tablo reports {st['busy']} busy in all"
              + (f": {others} recording or on other TVs." if others else "."))
    else:
        free = st["total"] - len(st["ours"]) if st["total"] else None
        print((f"  {free} free, as far as this script can tell. " if free is not None else "  ")
              + "Recordings and the Tablo app on other TVs don't show up here.")
    if not S.use_ffmpeg:
        print("  (Direct mode: a channel stays listed until you switch channels or quit.)")


def print_recent(tablo) -> None:
    """'Recent: 9.1 KNINE · 4.1 KFOUR ...   (p = previous channel)' under the menu."""
    recent = tablo.recent()
    if recent:
        names = " · ".join(f"{c.major}.{c.minor} {short_name(c)}" if c.major else short_name(c) for c in recent)
        print(paint(f"\nRecent: {names}   (p = previous channel)", DIM))


def print_channels(tablo, favs, show_streaming=True, guide=True):
    """Numbered list; returns {number: channel}.
    With --max-channels N (or max_channels in tablo.ini) only the first N are
    printed: favorites always, then local, then FAST. Channels past the limit keep
    their numbers and can still be watched by number, channel (9.1) or name."""
    numbered, n = {}, 0
    limit, printed, cut = S.max_channels, 0, 0
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
        cells = []
        for ch in chans:
            n += 1
            numbered[n] = ch                 # numbered even when not printed
            cells.append((n, ch))
        if not hidden and limit and title != "Favorites":   # favorites always show in full
            room = max(0, limit - printed)
            cut += len(cells) - min(room, len(cells))
            cells = cells[:room]
            if not cells:
                print(f"\n{title}: {len(chans)} channel{'s' if len(chans) != 1 else ''} "
                      f"(not shown: over the {limit}-channel limit)")
                continue
        if hidden:
            print(f"\n{title}: {len(chans)} channel{'s' if len(chans) != 1 else ''} (type 's' to show)")
            continue
        heading = f"★ {title}" if title == "Favorites" else title
        print("\n" + paint(heading, FAV_HEAD if title == "Favorites" else HEAD))
        print_columns(cells, now, fav=title == "Favorites")
        printed += len(cells)
    if cut:
        print(paint(f"\n  {cut} more channel{'s' if cut != 1 else ''} not shown (limit {limit}). "
                    f"Type a channel like 9.1 or a name to watch any of them; "
                    f"--max-channels 0 shows all.", DIM))
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
    """Start the web server in a background thread. lan=True listens on every
    network interface (other devices), otherwise only on this PC."""
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
    """The interactive channel menu (the default when you just run the script)."""
    base = f"http://127.0.0.1:{port}"
    show_streaming = not tablo.lan_only   # FAST can't play in --offline mode
    numbered = print_channels(tablo, favs, show_streaming)
    help_line = ("\nType a number or channel (9.1, KFOR) to watch.  p = previous channel  i3 = details\n"
                 "/football = search the guide  +3 / -3 = add/remove favorite  r = refresh guide  "
                 "t = tuners\n"
                 "w 4.1 = 2nd window (two channels at once; w off closes it)  m = multi-view page\n"
                 "f=favorites  a=all  s=show/hide FAST  l=list" + ("  w=web page" if web else "")
                 + "  q=quit    Keep this window open while watching.")
    watched_now = None                     # channel watched in this session (for 'p')

    def show_menu():
        print_recent(tablo)
        print_tuners(tablo)
        print(help_line)
    show_menu()
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
            show_menu()
            continue
        if c.startswith("/") or c.startswith("find "):        # /football  or  find football
            q = choice[1:].strip() if c.startswith("/") else choice[5:].strip()
            if q:
                print_search(tablo, favs, q, numbered)
            else:
                print("Type / and a word, e.g. /football or /news")
            continue
        if c in ("t", "tuners"):                                # who's using the Tablo's tuners
            print_tuners(tablo, detail=True)
            continue
        if c in ("p", "prev", "previous", "last"):             # like a remote's Last button
            recent = tablo.recent()
            ch = (recent[1] if watched_now and len(recent) > 1 else recent[0] if recent and not watched_now else None)
            if not ch:
                print("No previous channel yet.")
                continue
            choice = str(ch.major) + "." + str(ch.minor) if ch.major else ch.call_sign
            c = choice.lower()
        if c in ("r", "refresh"):
            if tablo.lan_only:
                print("No guide in --offline mode (it comes from the internet).")
                continue
            print("Refreshing the guide...")
            tablo.refresh_guide()
            numbered = print_channels(tablo, favs, show_streaming)
            show_menu()
            print(f"Guide refreshed at {tablo.guide_time()}.")
            continue
        if c == "s":
            show_streaming = not show_streaming
            numbered = print_channels(tablo, favs, show_streaming)
            show_menu()
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
        if c[0] == "w" and len(c) > 1 and (c[1] == " " or c[1].isdigit()):   # w 4.1: second window
            arg = choice[1:].strip()
            if arg.lower() in ("off", "close", "x"):
                closed = False
                for proc in (VLC2.proc, S.last_ffplay2):
                    if proc and proc.poll() is None:
                        proc.terminate()
                        closed = True
                print("Closed the second window." if closed else "No second window is open.")
                continue
            ch = resolve(tablo, favs, arg, numbered)
            if not ch:
                print(f"'{arg}' not found. Type l to list channels.")
                continue
            if ch.kind != "ota" and not tablo.maybe_reconnect(min_gap=15):
                print("FAST (online) channels need the internet. Local channels still work.")
                continue
            print(f"Tuning {ch.display_name} in a second window (uses another tuner)...")
            shown = getattr(tablo, "shown_now", {}) or {}
            if launch_player(f"{base}/ch/{ch.identifier}?w=2", vlc_meta(ch, shown.get(ch.identifier)), window=2):
                print(f"Playing {ch.display_name} in window 2. Type w off to close it.")
            continue
        if c in ("m", "multi"):                               # multi-view page in the browser
            if not LiveTV.available():
                print("Multi-view needs ffmpeg on this PC (sudo apt install ffmpeg).")
                continue
            open_url(base + "/multi")
            print(color_addresses(f"Opening multi-view: {base}/multi"))
            if S.lan_base:                                   # started with --lan
                print(color_addresses(f"Other devices on your Wi-Fi: open {S.lan_base}/multi{kq()}"))
            else:
                print(color_addresses(f"For other devices on your Wi-Fi, restart with --lan, then open "
                                      f"http://{lan_ip(tablo.device_url)}:{port}/multi"))
            continue
        if c[0] == "i" and len(c) > 1 and (c[1:].strip()[:1].isdigit() or c[1] == " "):
            ch = resolve(tablo, favs, choice[1:].strip(), numbered)
            show_info(tablo, ch) if ch else print(f"'{choice[1:].strip()}' not found.")
            continue
        if c[0] in "+-" and len(c) > 1:
            change_favs(tablo, favs, [choice[1:]], add=c[0] == "+", numbered=numbered)
            numbered = print_channels(tablo, favs, show_streaming)
            show_menu()
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
            tablo.remember_watched(ch.identifier)
            watched_now = ch


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
  {RUN} --refresh-guide         refresh the guide in the copy that's already running
  {RUN} --search football       find a show (in the menu: /football; p = previous channel)
  two channels at once          in the menu: w 4.1 (second window) or m (multi-view page,
                                2 or 4 channels: http://127.0.0.1:8000/multi)
  {RUN} --max-channels          list only the first 50 channels (or --max-channels 30)

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

restarting a stuck Tablo (every few months, or on big-game nights):
  The Tablo has no restart command, so this needs a PHYSICAL smart plug between the
  Tablo's power adapter and the wall, one this PC can switch on your network:
  Shelly Plug US Gen4 (or Plus Plug US), or a Tasmota plug (e.g. Athom PG03V3-US16A-TAS).
  In tablo.ini:  smart_plug = shelly 192.168.1.60   (or: tasmota 192.168.1.61)
  {RUN} --test-plug          check the plug answers (switches nothing)
  {RUN} --restart-tablo      power off 30 s, back on, wait until the Tablo answers
  {RUN} --restart-tablo --yes   no question asked (for a scheduled restart)
"""


# =============================================================================
# SECTION 12: STARTUP (main)
# main() runs these steps in order; each is its own function below:
#   build_parser -> apply_display_options -> load_login -> connect_tablo ->
#   apply_playback_settings -> run_one_shot (--list, --play...) -> announce ->
#   serve (web server + menu). The keyring and guide-refresher helpers they
#   use come first. --restart-tablo and --test-plug skip the login entirely
#   (handled in load_login), so they work even when Tablo's cloud is down.
# =============================================================================

# --- Restarting the Tablo with a smart plug (--restart-tablo) ----------------
# The Tablo has NO remote-restart command: not in its app, not in its API. When it
# hangs (every few months, or on big-game nights), the only cure is cutting its
# power. So this needs a PHYSICAL SMART PLUG between the Tablo's power adapter and
# the wall, one that this PC can switch over your home network (no cloud):
#   * Shelly Plug US Gen4 (or Shelly Plus Plug US): smart_plug = shelly 192.168.1.60
#   * a plug with Tasmota pre-installed (e.g. Athom PG03V3-US16A-TAS):
#                                                   smart_plug = tasmota 192.168.1.61
#   * anything else with on/off web addresses (Home Assistant, ESPHome...):
#       smart_plug = url, smart_plug_off_url = ..., smart_plug_on_url = ...
# Shelly and Tasmota are told "off, then back on by yourself in 30 s", so the
# Tablo comes back even if this PC loses its connection mid-restart.
PLUG_OFF_SECONDS = 30          # Tablo's own advice: unplug, wait 30 seconds, plug back in
PLUG_WAIT_SECONDS = 300        # then wait up to 5 minutes for it to start up
PLUG_POLL_SECONDS = 5          # checking every 5 seconds


class PlugError(RuntimeError):
    """The smart plug isn't set up, can't be reached, or refused the command."""


def _plug_get(url: str, password: str = "", timeout: int = 8) -> bytes:
    """GET a plug's local web address. Shelly uses user 'admin' with basic (Gen1)
    or digest (Gen2+) login; Tasmota's password goes in the URL instead."""
    handlers = []
    if password:
        pw = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        pw.add_password(None, url, "admin", password)
        handlers = [urllib.request.HTTPBasicAuthHandler(pw), urllib.request.HTTPDigestAuthHandler(pw)]
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(url, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise PlugError(f"the plug answered {e.code}"
                        + (" (wrong or missing smart_plug_password?)" if e.code == 401 else "")) from None
    except (urllib.error.URLError, OSError) as e:
        raise PlugError(f"can't reach the plug at {url.split('/')[2]} "
                        f"({getattr(e, 'reason', e)})") from None


class SmartPlug:
    """The smart plug the Tablo is plugged into (tablo.ini smart_plug)."""
    def __init__(self, t):
        setting = t.get("smart_plug", "").strip()
        if not setting:
            raise PlugError("no smart plug is set up. Add one to tablo.ini, e.g.\n"
                            "  smart_plug = shelly 192.168.1.60\n(see 'Restart the Tablo' in readme.txt)")
        kind, _, host = setting.partition(" ")
        self.kind, self.host = kind.lower(), host.strip().replace("http://", "").rstrip("/")
        self.password = t.get("smart_plug_password", "").strip()
        register_secret(self.password)
        self.off_url = t.get("smart_plug_off_url", "").strip()
        self.on_url = t.get("smart_plug_on_url", "").strip()
        if self.kind not in ("shelly", "tasmota", "url"):
            raise PlugError(f"smart_plug = {setting!r}: start it with shelly, tasmota or url")
        if self.kind == "url" and not (self.off_url and self.on_url):
            raise PlugError("smart_plug = url needs smart_plug_off_url and smart_plug_on_url too")
        if self.kind != "url" and not self.host:
            raise PlugError(f"smart_plug = {setting!r}: add the plug's IP address after '{self.kind}'")
        self.gen = 0                                    # Shelly generation, found on first use

    def _tasmota(self, command: str) -> dict:
        q = {"cmnd": command}
        if self.password:
            q.update(user="admin", password=self.password)
        return json.loads(_plug_get(f"http://{self.host}/cm?" + urllib.parse.urlencode(q)) or b"{}")

    def _shelly_gen(self) -> int:
        if not self.gen:
            info = json.loads(_plug_get(f"http://{self.host}/shelly") or b"{}")   # needs no login
            self.gen = int(info.get("gen") or 1)
        return self.gen

    def is_on(self):
        """True/False, or None when the plug type can't report it (url plugs)."""
        if self.kind == "tasmota":
            return str(self._tasmota("Power").get("POWER", "")).upper() == "ON"
        if self.kind == "shelly":
            if self._shelly_gen() >= 2:
                st = json.loads(_plug_get(f"http://{self.host}/rpc/Switch.GetStatus?id=0", self.password))
                return bool(st.get("output"))
            return bool(json.loads(_plug_get(f"http://{self.host}/relay/0", self.password)).get("ison"))
        return None

    def describe(self) -> str:
        if self.kind == "shelly":
            return f"Shelly (Gen{self._shelly_gen()}) at {self.host}"
        return f"Tasmota plug at {self.host}" if self.kind == "tasmota" else "plug (custom web addresses)"

    def power_cycle(self, seconds: int = 0) -> None:
        """Turn the Tablo off, and back on after `seconds` (default PLUG_OFF_SECONDS).
        Shelly and Tasmota switch back on by themselves; url plugs are switched on by us."""
        seconds = seconds or PLUG_OFF_SECONDS
        if self.kind == "tasmota":   # Delay counts tenths of a second
            self._tasmota(f"Backlog Power Off; Delay {int(seconds * 10)}; Power On")
        elif self.kind == "shelly" and self._shelly_gen() >= 2:
            _plug_get(f"http://{self.host}/rpc/Switch.Set?id=0&on=false&toggle_after={seconds}", self.password)
        elif self.kind == "shelly":
            _plug_get(f"http://{self.host}/relay/0?turn=off&timer={seconds}", self.password)
        else:
            _plug_get(self.off_url, self.password)
            time.sleep(seconds)
            _plug_get(self.on_url, self.password)


def tablo_answers(device_url: str) -> bool:
    """Is the Tablo's own web service up? Any reply at all (even an error page) counts."""
    try:
        with urllib.request.urlopen(device_url + "/", timeout=3):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def saved_tablo_url(args) -> str:
    """The Tablo's address: --tablo-ip, else the one saved by the last connection."""
    if args.tablo_ip:
        addr = args.tablo_ip.strip().rstrip("/")
        return addr if addr.startswith("http") else "http://" + (addr if ":" in addr else addr + ":8885")
    return load_cache().get("device_url", "")


def cmd_test_plug(t) -> None:
    """--test-plug: check the smart plug answers, without switching anything."""
    try:
        plug = SmartPlug(t)
        state = plug.is_on()
        print(f"Smart plug OK: {plug.describe()}"
              + ("" if state is None else f", power is {'ON' if state else 'OFF'}"))
    except PlugError as e:
        sys.exit(f"Smart plug problem: {e}")


def cmd_restart_tablo(args, t) -> None:
    """--restart-tablo: power-cycle the Tablo through its smart plug, then wait for it.
    Asks first unless --yes (for a scheduled restart, e.g. cron at 4am)."""
    try:
        plug = SmartPlug(t)
        state = plug.is_on()
    except PlugError as e:
        sys.exit(f"Can't restart the Tablo: {e}")
    url = saved_tablo_url(args)
    print(f"Smart plug: {plug.describe()}" + (f"   Tablo: {url}" if url else ""))
    if not args.yes:
        print(f"This cuts the Tablo's power for {PLUG_OFF_SECONDS} seconds. Anything recording or being\n"
              f"watched right now will stop. Never do this during a Tablo firmware update.")
        if input("Restart the Tablo now? [y/N]: ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return
    if state is False:
        log.info("The plug was already OFF; turning it on.")
        if plug.kind == "tasmota":
            plug._tasmota("Power On")
        elif plug.kind == "shelly":
            _plug_get(f"http://{plug.host}/rpc/Switch.Set?id=0&on=true" if plug._shelly_gen() >= 2
                      else f"http://{plug.host}/relay/0?turn=on", plug.password)
    else:
        log.info(f"Restarting the Tablo: power off for {PLUG_OFF_SECONDS} s...")
        started = time.time()
        try:
            plug.power_cycle()
        except PlugError as e:
            sys.exit(f"The plug didn't take the command: {e}")
        if url:
            time.sleep(PLUG_OFF_SECONDS / 3)   # 10 s into the 30 s off-time
            if tablo_answers(url):          # still up with its power off? wrong outlet?
                log.warning("The Tablo still answers with the plug off. Is the Tablo's power adapter "
                            "really plugged into this smart plug?")
        time.sleep(max(0, PLUG_OFF_SECONDS - (time.time() - started)))
        log.info("Power is back on.")
    if not url:
        print("The Tablo usually takes 1-3 minutes to start up (its light goes solid).")
        return
    log.info("Waiting for the Tablo to start (usually 1-3 minutes)...")
    deadline = time.time() + PLUG_WAIT_SECONDS
    while time.time() < deadline:
        if tablo_answers(url):
            log.info(f"The Tablo is back online at {url}.")
            return
        time.sleep(PLUG_POLL_SECONDS)
    log.warning("The Tablo hasn't answered after 5 minutes. Check its light and network cable "
                "(it may also have a new address: try --tablo-ip).")


# --- Optional system keyring for the password --------------------------------
# 'password = keyring' in tablo.ini means: the real password is in your desktop's
# keyring (GNOME Keyring / KWallet via secret-tool or the Python 'keyring' module,
# Windows Credential Manager or macOS Keychain via 'keyring'). Nothing to install
# if you don't use it; a plain password in tablo.ini keeps working.
KEYRING_SERVICE = "tablo-vlc"


def _keyring_module():
    """The optional Python 'keyring' package, or None if it isn't installed."""
    try:
        import keyring          # optional: pip install keyring
        return keyring
    except Exception:
        return None


def keyring_available() -> bool:
    """True if some keyring can be used on this PC."""
    return bool(_keyring_module() or shutil.which("secret-tool"))


def keyring_get(email: str) -> str:
    """The Tablo password stored for this email, or '' (tries 'keyring', then secret-tool)."""
    kr = _keyring_module()
    if kr:
        try:
            return kr.get_password(KEYRING_SERVICE, email) or ""
        except Exception as e:
            log.debug(f"keyring lookup failed: {e}")
    if shutil.which("secret-tool"):
        r = subprocess.run(["secret-tool", "lookup", "service", KEYRING_SERVICE, "account", email],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return r.stdout.rstrip("\n")
    return ""


def keyring_set(email: str, password: str) -> bool:
    """Store the password; True if it worked. secret-tool reads it from stdin,
    so it never appears in a command line or the process list."""
    kr = _keyring_module()
    if kr:
        try:
            kr.set_password(KEYRING_SERVICE, email, password)
            return True
        except Exception as e:
            log.debug(f"keyring store failed: {e}")
    if shutil.which("secret-tool"):
        r = subprocess.run(["secret-tool", "store", "--label", "Tablo account (tablo-vlc)",
                            "service", KEYRING_SERVICE, "account", email],
                           input=password, capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    return False


def get_password(t) -> str:
    """The Tablo password: from tablo.ini, or the system keyring if it says 'keyring'."""
    pw = t.get("password", "")
    if pw.strip().lower() != "keyring":
        return pw
    pw = keyring_get(t["email"])
    if not pw:
        sys.exit("tablo.ini says 'password = keyring' but your keyring has no Tablo password\n"
                 f"for {t['email']}. Run: {RUN} --setup")
    register_secret(pw)
    return pw


def start_guide_refresher(tablo, favs, iptv: bool = True) -> None:
    """Keep the guide fresh in the background:
      * every guide_refresh minutes (tablo.ini, default 10): what's on now, for the
        menu and the web pages (they pick it up on their next redraw/refresh)
      * with iptv=True (--lan + ffmpeg): build /guide.xml now and rebuild it every
        30 minutes, so IPTV apps (TiviMate...) never wait on dozens of cloud calls."""
    if not iptv and (not S.guide_refresh or tablo.lan_only):
        return                                       # nothing to do

    def loop():
        next_full = 0.0
        while True:
            try:
                if iptv and time.time() >= next_full:
                    first = not _xmltv_cache["xml"]
                    build_xmltv(tablo, favs)
                    next_full = time.time() + GUIDE_REFRESH
                    if first:
                        log.info("TV guide ready for IPTV apps (/guide.xml)")
            except Exception as e:                   # offline / cloud down: keep the old copy
                log.debug(f"Guide refresh skipped: {e}")
            step = S.guide_refresh * 60 if S.guide_refresh else GUIDE_REFRESH
            time.sleep(min(step, max(5, next_full - time.time())) if iptv else step)
            if S.guide_refresh and not tablo.lan_only:
                try:
                    tablo.now_playing(max_age=0)         # what's on now
                    log.debug(f"Guide auto-refresh at {tablo.guide_time()}")
                except Exception as e:
                    log.debug(f"Guide auto-refresh skipped: {e}")
    threading.Thread(target=loop, name="guide-refresher", daemon=True).start()


def cmd_refresh_guide(args, t) -> None:
    """--refresh-guide: ask the copy of this script that's already running (menu,
    --web or --lan) to refresh its guide now. It's always fresh when it starts."""
    port = args.port or int(t.get("port", "") or DEFAULT_PORT)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/refresh-guide", data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            info = json.loads(r.read() or b"{}")
        print(f"Guide refreshed at {info.get('updated', '?')} "
              f"({info.get('channels', 0)} channels with show info).")
    except urllib.error.HTTPError as e:
        sys.exit(f"The running script refused: {e.code} {e.reason}")
    except (urllib.error.URLError, OSError):
        sys.exit(f"tablo-vlc isn't running on port {port}, so there's nothing to refresh.\n"
                 f"(Each time it starts, it loads the guide fresh.)")


def build_parser() -> argparse.ArgumentParser:
    """All command-line options (shown by --help, with HELP_EPILOG at the end)."""
    ap = argparse.ArgumentParser(
        description="Watch Tablo 4th Gen live TV on this PC, phones, tablets and TVs. "
                    "With no options, shows a channel menu.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG.replace("{RUN}", RUN))
    ap.add_argument("--version", action="version", version=f"tablo-vlc {__version__}")
    ap.add_argument("--play", metavar="CH",
                    help="play a channel (9.1, KFOR, a menu number) or 'favorites' / 'all'")
    ap.add_argument("--list", action="store_true", help="print favorites and all channels, then exit")
    ap.add_argument("--search", metavar="WORDS",
                    help="search the TV guide, e.g. --search football (now on every channel; "
                         "coming up on your favorites)")
    ap.add_argument("--info", metavar="CH", help="show what's on a channel now and next (e.g. --info 9.1)")
    ap.add_argument("--add-fav", nargs="+", metavar="CH",
                    help="add channels to favorites in tablo.ini (e.g. --add-fav 9.1 KFOR 702.1)")
    ap.add_argument("--remove-fav", nargs="+", metavar="CH", help="remove channels from favorites")
    ap.add_argument("--web", "--page", "--pick", dest="web", action="store_true",
                    help="also serve a web page with Play buttons and open it")
    ap.add_argument("--lan", action="store_true",
                    help="let phones, tablets and TVs on your Wi-Fi watch (see 'other devices' below)")
    ap.add_argument("--setup", action="store_true", help="enter your Tablo email and password")
    ap.add_argument("--refresh-guide", action="store_true",
                    help="refresh the TV guide now in the copy of this script that's already running")
    ap.add_argument("--guide-refresh", type=int, metavar="MIN",
                    help="minutes between automatic guide refreshes for this run (0 = off; default 10)")
    ap.add_argument("--restart-tablo", action="store_true",
                    help="restart a stuck Tablo by switching its power off and on. Needs a smart plug "
                         "(Shelly or Tasmota) on the Tablo, set in tablo.ini: smart_plug = shelly 192.168.1.60")
    ap.add_argument("--test-plug", action="store_true",
                    help="check the smart plug answers, without switching it")
    ap.add_argument("--yes", action="store_true", help="don't ask before --restart-tablo (for scheduled restarts)")
    ap.add_argument("--fetch-keys", action="store_true",
                    help="download the Tablo signing keys from GitHub into tablo.ini (done automatically if missing)")
    ap.add_argument("--favorites", action="store_true", help=argparse.SUPPRESS)  # old alias
    ap.add_argument("--no-vlc", action="store_true", help="just run the server (for other devices)")
    ap.add_argument("--player", metavar="NAME",
                    help="video player for this run: vlc, flatpak, ffplay, mpv, or a full command (overrides tablo.ini)")
    ap.add_argument("--ffplay", action="store_true", help="shortcut for --player ffplay")
    ap.add_argument("--encoder", metavar="NAME",
                    help="phone/TV video encoder for this run: auto, x264, vaapi, nvenc, qsv, amf")
    ap.add_argument("--buffer", type=int, metavar="MS",
                    help=f"VLC buffer in milliseconds for this run (default from tablo.ini or "
                         f"{DEFAULT_VLC_BUFFER}); try 10000 if Wi-Fi hiccups cause stutters")
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
    ap.add_argument("--max-channels", type=int, nargs="?", const=50, metavar="N",
                    help="list at most N channels in the menu and --list (default 50 if N is left out; "
                         "0 = all). Favorites always show; the rest can still be typed by number or name")
    ap.add_argument("--no-color", action="store_true", help="plain text channel list (no colors)")
    ap.add_argument("--debug", action="store_true", help="show detailed messages on screen too")
    ap.add_argument("--debug-raw", action="store_true",
                    help="like --debug but log tokens unredacted (don't share that log!)")
    return ap


def apply_display_options(args) -> None:
    """Step 1: logging, terminal columns/colors, and whether ffmpeg can relay streams."""
    if args.favorites and not args.play:
        args.play = "favorites"                      # old --favorites alias
    setup_logging(args.debug or args.debug_raw, raw=args.debug_raw)
    if args.debug_raw:
        log.warning("--debug-raw: tokens are written to the log unredacted. Don't share this log.")
    S.columns = max(0, args.columns or 0)
    if args.max_channels is not None:
        S.max_channels = max(0, args.max_channels)    # tablo.ini max_channels is read later
    if args.no_color:
        S.use_color = False
    S.use_ffmpeg = bool(shutil.which("ffmpeg")) and not args.direct
    log.debug(f"Started tablo-vlc {__version__} on Python {sys.version.split()[0]} "
              f"({sys.platform}): {' '.join(sys.argv)}")


def load_login(args):
    """Step 2: tablo.ini, your login and the Tablo signing keys.
    Returns (cfg, section), or None when a command like --setup has finished."""
    cfg = load_config()
    t = cfg["tablo"]
    if args.refresh_guide:                     # just talks to the running copy
        cmd_refresh_guide(args, t)
        return None
    if args.test_plug or args.restart_tablo:   # no login needed: just the plug and the Tablo
        cmd_test_plug(t) if args.test_plug else cmd_restart_tablo(args, t)
        return None
    if args.setup:
        cmd_setup(cfg)
        print("Add your favorites to tablo.ini (e.g. favorites = 4.1, 9.1), then run with no options.")
        return None
    if not t.get("email") or not t.get("password"):
        if not CONFIG_FILE.exists():
            save_config(cfg)  # create a blank template to fill in
        sys.exit(f"Add your Tablo email and password to:\n  {CONFIG_FILE}\n"
                 f"then run this again. (Or run with --setup to be prompted.)")
    # The signing keys come from tablo.ini, or are downloaded once from the
    # open-source tablo-api project (never in --offline mode).
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
            return None
    return cfg, t


def connect_tablo(args, t) -> "Tablo":
    """Step 3: log in to the Tablo cloud, or start from the saved channel list."""
    try:
        return Tablo(t["email"], get_password(t), t.get("device", ""),
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


def apply_playback_settings(args, cfg, t) -> int:
    """Step 4: port, VLC tuning, picture quality, access key and player. Returns the port."""
    if t.get("port", "").strip() == "8089":  # old default the script wrote itself
        t["port"] = str(DEFAULT_PORT)
        save_config(cfg)
        log.info(f"Changed port in {CONFIG_FILE.name} from 8089 to {DEFAULT_PORT}")
    try:
        S.vlc_buffer_ms = max(300, int(args.buffer or t.get("vlc_buffer", "") or S.vlc_buffer_ms))
    except ValueError:
        log.warning(f"vlc_buffer in {CONFIG_FILE.name} should be a number of milliseconds; using {S.vlc_buffer_ms}")
    S.deinterlace = (t.get("deinterlace", "") or S.deinterlace).strip().lower()
    for name in ("browser_quality", "tv_quality"):
        try:
            val = int(t.get(name, "") or 0)
            if val:
                val = min(QUALITIES, key=lambda q: abs(q - val))   # nearest supported height
                setattr(S, name, val)
        except ValueError:
            log.warning(f"{name} in {CONFIG_FILE.name} should be a number like 720 or 1080")
    S.encoder_setting = (args.encoder or t.get("encoder", "") or "auto").strip().lower()
    S.captions = t.get("captions", "").strip().lower() in ("on", "yes", "true", "1")
    if args.max_channels is None:              # --max-channels wins over tablo.ini
        try:
            S.max_channels = max(0, int(t.get("max_channels", "") or 0))
        except ValueError:
            log.warning(f"max_channels in {CONFIG_FILE.name} should be a number; showing all channels")
    try:                                       # minutes; 0 = no automatic refresh, else at least 2
        g = int(args.guide_refresh if args.guide_refresh is not None else (t.get("guide_refresh", "") or 10))
        S.guide_refresh = 0 if g <= 0 else max(2, g)
    except ValueError:
        log.warning(f"guide_refresh in {CONFIG_FILE.name} should be a number of minutes; using {S.guide_refresh}")
    S.access_key = t.get("access_key", "").strip()
    register_secret(S.access_key)          # never show the key in the log
    choice = args.player or ("ffplay" if args.ffplay else "vlc" if args.vlc else t.get("player", ""))
    S.player = resolve_player(choice)
    if S.player:
        log.debug(f"Player: {S.player['name']} ({' '.join(S.player['cmd'])})")
    else:
        log.warning("No video player found (VLC or ffplay). Channels will list, but can't play here. "
                    "Install one: sudo apt install vlc   (or: sudo apt install ffmpeg)")
    return args.port or int(t.get("port", DEFAULT_PORT))


def run_one_shot(args, tablo, favs, port) -> bool:
    """Step 5: commands that do one thing and exit. True if one ran."""
    if args.add_fav or args.remove_fav:
        if args.add_fav:
            change_favs(tablo, favs, args.add_fav, add=True)
        if args.remove_fav:
            change_favs(tablo, favs, args.remove_fav, add=False)
        print("Favorites: " + (", ".join(c.display_name for c in favs.channels()) or "(none)"))
        return True
    if args.list:
        print_channels(tablo, favs, show_streaming=True)
        return True
    if args.search:
        print_search(tablo, favs, args.search)
        return True
    if args.info:
        ch = resolve(tablo, favs, args.info)
        if not ch:
            sys.exit(f"Channel '{args.info}' not found. Try --list.")
        show_info(tablo, ch)
        return True
    if args.vlc_commands:
        print_vlc_commands(tablo, port)
        return True
    if args.tune:
        ch = tablo.find(args.tune)
        if not ch:
            sys.exit(f"Channel '{args.tune}' not found. Try --list.")
        url = tablo.watch_url(ch.identifier)
        log.info(f"[{ch.display_name}] Tablo stream URL: {url}")
        print("\nPlay it straight from the Tablo (start within a minute or so; the")
        print("Tablo keeps it going while VLC is watching):\n")
        print(f'  vlc "{url}"\n')       # the real URL on screen; the log gets a fingerprint
        return True
    # --play <one channel>: tune, hand it to the player, exit (no server needed)
    if args.play and args.play.lower() not in ("all", "favorites", "favs", "fav"):
        ch = resolve(tablo, favs, args.play)
        if not ch:
            sys.exit(f"Channel '{args.play}' not found. Try --list.")
        sys.exit(0 if play_direct(tablo, ch) else 1)
    return False


def announce(args, tablo, port) -> None:
    """Step 6: tell you where things are (and get the phone/TV extras ready)."""
    base = f"http://127.0.0.1:{port}"
    log.info(f"Player: {player_name() if S.player else 'none found'}   Stream mode: "
             + ("ffmpeg relay" if S.use_ffmpeg else "direct") + f"   Log: {LOG_FILE}")
    if (args.web or args.lan) and not args.offline:
        threading.Thread(target=ensure_hls_js, daemon=True).start()
    # This PC's address on the home network (the one that reaches the Tablo),
    # so you know what to type on a phone/TV, or which IP to reserve in your router.
    my_ip = lan_ip(tablo.device_url)
    tablo_host = urllib.parse.urlsplit(tablo.device_url).hostname if tablo.device_url else ""
    log.info(f"This PC's local IP: {my_ip}"
             + (f"   Tablo: {tablo_host}" if tablo_host else "")
             + ("" if args.lan else f"   (other devices: start with --lan, then open http://{my_ip}:{port}/)"))
    if args.lan:
        S.lan_base = f"http://{my_ip}:{port}"
        log.info(f"Phones/tablets on your Wi-Fi: open {S.lan_base}/{kq()} in Safari or Chrome"
                 + ("" if LiveTV.available() else "  (install ffmpeg on this PC for in-browser playback)"))
        if LiveTV.available():
            log.info(f"TVs (Fire TV Silk, Samsung Internet, Google TV browser): {S.lan_base}/tv{kq()}")
            log.info(f"Multi-view, 2 or 4 channels at once (PC, tablet): {S.lan_base}/multi{kq()}")
            log.info(f"IPTV apps (TiviMate, UHF, iPlayTV, Kodi): playlist {S.lan_base}/tv.m3u{kq()}  "
                     f"guide {S.lan_base}/guide.xml{kq()}")
        if not S.access_key:
            log.info("Anyone on your Wi-Fi can watch. To require a key, set access_key = <something> in tablo.ini.")
        if IS_WINDOWS:
            log.info("If Windows asks, allow Python through the firewall on Private networks.")
    if args.web:
        log.info(f"Web page: {base}/")
        open_url(base + "/")


def serve(args, tablo, favs, port) -> None:
    """Steps 7-8: run the web server, plus the menu (normal run in a terminal),
    a playlist (--play favorites/all), or nothing (--no-vlc / headless)."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    base = f"http://127.0.0.1:{port}"
    server = start_server(tablo, favs, port, args.lan)
    announce(args, tablo, port)
    if args.lan and LiveTV.available():
        threading.Thread(target=choose_encoder, daemon=True).start()   # ready before the first phone/TV
    threading.Thread(target=tablo.load_tuner_info, daemon=True).start()   # tuner count for the menu
    # what's on now every guide_refresh minutes; + /guide.xml for IPTV apps with --lan
    start_guide_refresher(tablo, favs, iptv=args.lan and LiveTV.available())
    if args.play:  # favorites / all playlist
        launch_player(f"{base}/{'playlist.m3u' if args.play.lower() == 'all' else 'favorites.m3u'}",
                      playlist=True)
        print("Keep this window open while watching. Ctrl+C to quit.")
    elif not args.no_vlc and sys.stdin.isatty():
        if S.console and not args.debug:
            S.console.setLevel(logging.WARNING)      # keep the menu tidy; everything still goes to the log
        menu(tablo, favs, port, args.web)
        shutdown(server)
        return
    else:
        print(color_addresses(f"Server running at {base}/  Ctrl+C to quit."))
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        pass
    shutdown(server)


def main():
    """Command-line entry point. Each step is its own function above."""
    args = build_parser().parse_args()
    apply_display_options(args)                      # 1. logging, colors
    loaded = load_login(args)                        # 2. tablo.ini, login, keys
    if not loaded:
        return
    cfg, t = loaded
    tablo = connect_tablo(args, t)                   # 3. Tablo cloud / saved list
    port = apply_playback_settings(args, cfg, t)     # 4. player, quality, access key
    favs = Favorites(cfg, tablo)
    if run_one_shot(args, tablo, favs, port):        # 5. --list, --play 2.1, ...
        return
    serve(args, tablo, favs, port)                   # 6-8. server + menu


if __name__ == "__main__":
    main()
