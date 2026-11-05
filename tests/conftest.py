"""Shared test setup for tablo-vlc.

The script is one file with a hyphen in its name (tablo-vlc.py), so it can't be
imported normally. Each test session copies it into a temporary folder and loads
it from there, so tablo.ini, tablo-cache.json and the log land in that folder
and never touch your real files.

A fake Tablo (cloud + the box on your network) runs on 127.0.0.1 using only the
standard library. Nothing here contacts the internet.
"""
import hashlib
import hmac
import importlib.util
import json
import shutil
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tablo-vlc.py"
HASH_KEY = "test-hash-key"          # made-up signing keys for the fake Tablo
DEVICE_KEY = "test-device-key"


class FakeTablo:
    """Records what the script asked for, and lets tests break things on purpose."""
    def __init__(self):
        self.logins = 0
        self.cloud_hits = 0
        self.guide_401 = 0          # answer the next N guide requests with 401
        self.watches = []           # (channel id, signature ok)
        self.ended = []             # (session token, signature ok)
        self.url = ""
        self.powered = True         # switched by the fake smart plug
        self.tuner_list = None      # set to a list to answer /server/tuners

    def channels(self):
        return [
            {"identifier": "S9", "kind": "ota", "ota": {"major": 9, "minor": 1, "callSign": "KWTV", "network": "CBS"}},
            {"identifier": "S4", "kind": "ota", "ota": {"major": 4, "minor": 1, "callSign": "KFOR", "network": "NBC"}},
            {"identifier": "O1", "kind": "ott", "name": "Pluto News",
             "ott": {"major": 700, "minor": 1, "callSign": "PLUTONEWS", "network": "Pluto News",
                     "provider": "Pluto TV", "streamUrl": "http://fast.example/news.m3u8"}},
        ]

    def live(self):
        t0 = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%MZ")
        return [{"channel": {"identifier": "S9"},
                 "airing": {"title": "Teachers Tournament", "show": {"title": "Jeopardy!"}, "datetime": t0,
                            "duration": 1800, "kind": "episode", "genres": ["Game show"],
                            "description": "Three teachers compete.", "episode": {"rating": "TV-G"}}}]

    def airings(self):
        b = datetime.now(timezone.utc) - timedelta(minutes=20)
        return [{"title": t, "show": {"title": t}, "datetime": (b + timedelta(minutes=30 * i)).strftime("%Y-%m-%dT%H:%MZ"),
                 "duration": 1800, "kind": "episode", "genres": ["News"], "episode": {}}
                for i, t in enumerate(["Evening News", "Game Night & More", "Late Show"])]


def _signed_ok(method, path, body, headers):
    md5 = hashlib.md5(body.encode()).hexdigest() if body else ""
    sig = hmac.new(HASH_KEY.encode(), f"{method}\n{path}\n{md5}\n{headers['Date']}".encode(),
                   hashlib.md5).hexdigest()
    return headers.get("Authorization") == f"tablo:{DEVICE_KEY}:{sig}"


def _handler(fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_json(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            fake.cloud_hits += self.path.startswith("/api/")
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            if self.path == "/api/v2/login/":
                fake.logins += 1
                return self.send_json({"token_type": "Bearer", "access_token": f"ACCESS-{fake.logins}"})
            if self.path == "/api/v2/account/select/":
                return self.send_json({"token": f"LHTOKEN-{fake.logins}-abcdef"})
            if self.path.startswith("/guide/channels/") and self.path.endswith("/watch?lh"):
                path = self.path.split("?")[0]
                ok = _signed_ok("POST", path, body, self.headers)
                ident = path.split("/")[3]
                fake.watches.append((ident, ok))
                if not ok:
                    return self.send_json({}, 401)
                return self.send_json({"playlist_url": f"{fake.url}/stream/{ident}.m3u8",
                                       "token": f"SESSION-{ident}-zz99", "keepalive": 120})
            self.send_json({}, 404)

        def do_DELETE(self):
            path = self.path.split("?")[0]
            fake.ended.append((path.rsplit("/", 1)[1], _signed_ok("DELETE", path, "", self.headers)))
            self.send_json({})

        def do_GET(self):
            if not fake.powered:        # "unplugged": hang up without answering
                self.close_connection = True
                return
            fake.cloud_hits += self.path.startswith("/api/")
            if self.path in ("/server/info?lh", "/server/tuners?lh"):     # read-only, signed
                if not _signed_ok("GET", self.path.split("?")[0], "", self.headers):
                    return self.send_json({}, 401)
                if self.path.startswith("/server/info"):
                    return self.send_json({"model": {"name": "Tablo 4G QUAD", "tuners": 4}})
                return self.send_json(fake.tuner_list) if fake.tuner_list is not None else self.send_json({}, 404)
            if self.path == "/api/v2/account/":
                return self.send_json({"profiles": [{"identifier": "P1"}],
                                       "devices": [{"name": "Den Tablo", "serverId": "SID_1", "url": fake.url}]})
            if self.path.endswith("/guide/channels/live/"):
                if fake.guide_401:
                    fake.guide_401 -= 1
                    return self.send_json({}, 401)
                return self.send_json(fake.live())
            if self.path.endswith("/guide/channels/"):
                return self.send_json(fake.channels())
            if "/airings/" in self.path:
                return self.send_json(fake.airings())
            self.send_json({}, 404)
    return Handler


@pytest.fixture()
def fake_tablo():
    fake = FakeTablo()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield fake
    server.shutdown()
    server.server_close()


@pytest.fixture()
def tv(tmp_path, monkeypatch, fake_tablo):
    """A fresh copy of the script, loaded from a temp folder and pointed at the fake Tablo."""
    copy = tmp_path / "tablo-vlc.py"
    shutil.copy(SCRIPT, copy)
    spec = importlib.util.spec_from_file_location("tablo_vlc_under_test", copy)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CLOUD = fake_tablo.url
    mod.S.hash_key, mod.S.device_key = HASH_KEY, DEVICE_KEY
    monkeypatch.setattr(mod, "HLS_JS_URL", "http://127.0.0.1:9/none.js")    # never download
    return mod


@pytest.fixture()
def tablo(tv):
    return tv.Tablo("person@example.com", "Sup3rSecretPass")


class FakePlug:
    """A smart plug on 127.0.0.1: Shelly Gen1, Shelly Gen2+, Tasmota or plain URLs.
    It powers the fake Tablo, and (like the real ones) can switch itself back on."""
    def __init__(self, kind, tablo, password=""):
        self.kind, self.tablo, self.password = kind, tablo, password
        self.on, self.requests = True, []

    def set(self, on, after=0):
        self.on = self.tablo.powered = on
        if after:
            threading.Timer(after, self.set, args=(not on,)).start()


def _plug_handler(plug):
    import base64
    from urllib.parse import urlsplit, parse_qs

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_json(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            plug.requests.append(self.path)
            if u.path == "/shelly":
                return self.send_json({"gen": 2} if plug.kind == "shelly2" else {"type": "SHPLG-U1"})
            if plug.password and plug.kind == "shelly1":
                want = "Basic " + base64.b64encode(f"admin:{plug.password}".encode()).decode()
                if self.headers.get("Authorization") != want:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="shelly"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            if u.path == "/relay/0":                                    # Shelly Gen1
                if "turn" in q:
                    plug.set(q["turn"] == "on", float(q.get("timer", 0)))
                return self.send_json({"ison": plug.on})
            if u.path == "/rpc/Switch.GetStatus":                       # Shelly Gen2+
                return self.send_json({"id": 0, "output": plug.on})
            if u.path == "/rpc/Switch.Set":
                was = plug.on
                plug.set(q["on"] == "true", float(q.get("toggle_after", 0)))
                return self.send_json({"was_on": was})
            if u.path == "/cm":                                         # Tasmota
                cmd = q["cmnd"]
                if plug.password and q.get("password") != plug.password:
                    return self.send_json({"WARNING": "Need user=<username>&password=<password>"}, 401)
                if cmd.startswith("Backlog Power Off"):
                    tenths = int(cmd.split("Delay ")[1].split(";")[0])
                    plug.set(False, tenths / 10)
                elif cmd == "Power On":
                    plug.set(True)
                return self.send_json({"POWER": "ON" if plug.on else "OFF"})
            if u.path in ("/off", "/on"):                               # plain URL plug
                plug.set(u.path == "/on")
                return self.send_json({"ok": True})
            self.send_json({}, 404)
    return Handler


@pytest.fixture()
def make_plug(fake_tablo):
    servers = []

    def make(kind, password=""):
        plug = FakePlug(kind, fake_tablo, password)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _plug_handler(plug))
        plug.host = f"127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return plug
    yield make
    for server in servers:
        server.shutdown()
        server.server_close()


def make_captioned_broadcast(folder, text="HELLO FROM TABLO", seconds=8):
    """A short broadcast-style .ts like a Tablo OTA stream: interlaced MPEG-2 video
    carrying real CEA-608 closed captions (ATSC A/53 'GA94' user data), and AC-3
    audio. Needs ffmpeg. Returns the file path."""
    import subprocess
    from pathlib import Path
    folder = Path(folder)
    raw, cc_raw, out = folder / "v.m2v", folder / "v_cc.m2v", folder / "captioned.ts"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=704x480:rate=30000/1001", "-t", str(seconds), "-c:v", "mpeg2video",
                    "-b:v", "4M", "-g", "15", "-bf", "0", "-f", "mpeg2video", str(raw)], check=True)

    def parity(b):                       # CEA-608 bytes carry odd parity in bit 7
        b &= 0x7F
        return b | (0x80 if bin(b).count("1") % 2 == 0 else 0)
    pairs = []
    for a, b in ((0x14, 0x25), (0x14, 0x2D), (0x14, 0x70)):   # roll-up 2 rows, CR, row 15
        pairs += [(a, b), (a, b)]                                # control codes go twice
    chars = text.encode() + (b" " if len(text) % 2 else b"")
    pairs += [(chars[i], chars[i + 1]) for i in range(0, len(chars), 2)]
    pairs += [(0x14, 0x2D), (0x14, 0x2D)]                       # CR: the line shows
    data = raw.read_bytes()
    starts = [j for j in range(len(data) - 3) if data[j:j + 3] == b"\0\0\1"] + [len(data)]
    out_b, sent = bytearray(), 0
    for k in range(len(starts) - 1):
        chunk = data[starts[k]:starts[k + 1]]
        out_b += chunk
        nxt = data[starts[k + 1] + 3] if starts[k + 1] < len(data) else None
        if chunk[3] in (0x00, 0xB5) and nxt is not None and 0x01 <= nxt <= 0xAF:   # last header before slices
            a, b = pairs[sent % len(pairs)] if sent < len(pairs) * 3 else (0x80, 0x80)
            sent += 1
            out_b += b"\0\0\1\xB2GA94\x03" + bytes([0x41, 0xFF, 0xFC, parity(a), parity(b), 0xFF])
    cc_raw.write_bytes(bytes(out_b))
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-r", "30000/1001", "-f", "mpegvideo",
                    "-i", str(cc_raw), "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds),
                    "-c:v", "mpeg2video", "-b:v", "6M", "-flags", "+ilme+ildct", "-top", "1",
                    "-c:a", "ac3", "-f", "mpegts", str(out)], check=True)
    return out


def captions_in(path) -> str:
    """The closed-caption text ffmpeg finds in a video file ('' if none)."""
    import subprocess
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"movie={path}[out+subcc]", "-map", "0:1", "-f", "srt", "-"],
                       capture_output=True, text=True, timeout=60)
    return r.stdout


def live_tablo_stream(down=(None, None)):
    """A live-like Tablo HLS stream (needs ffmpeg): a sliding playlist of 2-second MPEG-2
    segments that advances in real time. down=(from, to) seconds: an outage where every
    connection is dropped, like a Wi-Fi blip or a stuck Tablo. Returns its URL."""
    import subprocess
    import tempfile
    import time
    from pathlib import Path
    folder = Path(tempfile.mkdtemp(prefix="tablo-test-src-"))
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=704x480:rate=30000/1001", "-f", "lavfi", "-i", "sine", "-t", "20",
                    "-c:v", "mpeg2video", "-b:v", "3M", "-g", "15", "-c:a", "ac3", "-f", "hls",
                    "-hls_time", "2", "-hls_list_size", "0", "-hls_segment_filename", str(folder / "t%d.ts"),
                    str(folder / "t.m3u8")], check=True)
    count = len(list(folder.glob("t*.ts")))
    t0 = time.time()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            elapsed = time.time() - t0
            if down[0] is not None and down[0] <= elapsed < down[1]:
                self.close_connection = True
                return
            if self.path.startswith("/live.m3u8"):
                now = int(elapsed // 2)
                first = max(0, now - 4)
                lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{first}"]
                for i in range(first, now + 1):
                    lines += ["#EXTINF:2.0,", f"seg{i}.ts"]
                data = ("\n".join(lines) + "\n").encode()
            elif self.path.startswith("/seg"):
                data = (folder / f"t{int(self.path[4:].split('.')[0]) % count}.ts").read_bytes()
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/live.m3u8"
