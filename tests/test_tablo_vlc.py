"""Tests for tablo-vlc.py. Run from the project folder:  python -m pytest -q

They use a fake Tablo on 127.0.0.1 (see conftest.py); no account, Tablo, VLC or
internet needed. Tests that need ffmpeg are skipped when it isn't installed.
"""
import configparser
import os
import http.client
import json
import logging
import shutil
import socket
import sys
import xml.etree.ElementTree as ET

import pytest

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def favs_for(tv, tablo, line=""):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg["tablo"] = {"favorites": line}
    return tv.Favorites(cfg, tablo)


# ---------------------------------------------------------------- privacy ----
def test_secrets_are_scrubbed_from_log_lines(tv):
    tv.register_secret("LHTOKEN-1-abcdef")
    out = tv.scrub("GET /api/v2/account/LHTOKEN-1-abcdef/guide")
    assert "LHTOKEN" not in out
    assert tv.fingerprint("LHTOKEN-1-abcdef") in out          # same secret -> same [token:xxxx]


def test_scrub_filter_cleans_log_records(tv):
    tv.register_secret("Hunter2Key")
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "key is %s", ("Hunter2Key",), None)
    tv.ScrubFilter().filter(rec)
    assert "Hunter2Key" not in rec.getMessage()


def test_login_password_and_tokens_never_logged(tv, tablo, caplog):
    caplog.set_level(logging.DEBUG)
    tv.log.addFilter(tv.ScrubFilter())
    tv.log.debug(f"pw={tablo.password} lh={tablo.lh_token} at={tablo.access_token}")
    text = caplog.text
    assert "Sup3rSecretPass" not in text and "LHTOKEN" not in text and "ACCESS-" not in text


# ----------------------------------------------------------------- config ----
def test_save_config_adds_defaults_and_keeps_password_with_percent(tv):
    cfg = tv.load_config()
    cfg["tablo"]["password"] = "100%sure"
    tv.save_config(cfg)
    again = tv.load_config()["tablo"]
    assert again["password"] == "100%sure"
    for key in ("favorites", "port", "player", "encoder", "access_key", "tv_quality"):
        assert key in again
    assert again["encoder"] == "auto"


def test_vlc_buffer_default_and_override(tv):
    cfg = tv.load_config()
    tv.save_config(cfg)
    assert tv.load_config()["tablo"]["vlc_buffer"] == "3000"     # 3 s default
    tv.apply_playback_settings(tv.build_parser().parse_args([]), cfg, cfg["tablo"])
    assert tv.vlc_tuning()["network-caching"] == "3000"
    cfg["tablo"]["vlc_buffer"] = "10000"                         # the Wi-Fi-hiccup setting
    tv.apply_playback_settings(tv.build_parser().parse_args([]), cfg, cfg["tablo"])
    assert tv.S.vlc_buffer_ms == 10000 and tv.vlc_tuning()["network-caching"] == "10000"
    tv.apply_playback_settings(tv.build_parser().parse_args(["--buffer", "5000"]), cfg, cfg["tablo"])
    assert tv.S.vlc_buffer_ms == 5000                            # --buffer wins for one run


@pytest.mark.skipif(sys.platform.startswith("win"), reason="file modes are a Unix thing")
def test_config_file_is_private(tv):
    tv.save_config(tv.load_config())
    assert (tv.CONFIG_FILE.stat().st_mode & 0o777) == 0o600


def test_password_from_keyring(tv, monkeypatch):
    monkeypatch.setattr(tv, "keyring_get", lambda email: "fromKeyring" if email == "a@b.c" else "")
    assert tv.get_password({"email": "a@b.c", "password": "keyring"}) == "fromKeyring"
    assert tv.get_password({"email": "a@b.c", "password": "plainText"}) == "plainText"
    with pytest.raises(SystemExit):
        tv.get_password({"email": "x@y.z", "password": "keyring"})


# ---------------------------------------------------------- cloud + Tablo ----
def test_login_finds_channels(tv, tablo, fake_tablo):
    assert fake_tablo.logins == 1
    assert [c.identifier for c in tablo.ota] == ["S4", "S9"]          # sorted by channel number
    assert [c.identifier for c in tablo.ott] == ["O1"]
    assert tablo.find("9.1").identifier == "S9"
    assert tablo.find("kfor").identifier == "S4"
    assert tablo.find("700.1").identifier == "O1"


def test_favorites_matching_and_missing(tv, tablo):
    favs = favs_for(tv, tablo, "4.1 9.1, NOPE")
    assert favs.ids == ["S4", "S9"]
    assert favs.missing == ["NOPE"]


def test_now_playing(tv, tablo):
    now = tablo.now_playing()
    assert now["S9"]["show"] == "Jeopardy!"


def test_relogin_after_expired_token(tv, tablo, fake_tablo):
    fake_tablo.guide_401 = 1
    now = tablo.now_playing(max_age=0)
    assert fake_tablo.logins == 2 and "S9" in now


def test_tuning_is_signed_and_tuner_is_released(tv, tablo, fake_tablo):
    url, token = tablo.watch_session("S9")
    assert url.endswith("/stream/S9.m3u8") and token == "SESSION-S9-zz99"
    assert fake_tablo.watches == [("S9", True)]
    tablo.end_session(token)
    assert fake_tablo.ended == [("SESSION-S9-zz99", True)]


def test_offline_mode_uses_saved_list_without_internet(tv, tablo, fake_tablo):
    hits = fake_tablo.cloud_hits
    offline = tv.Tablo("person@example.com", "x", lan_only=True)
    assert [c.identifier for c in offline.ota] == ["S4", "S9"]
    offline.now_playing()
    assert fake_tablo.cloud_hits == hits                     # not one cloud request


def test_offline_mode_without_saved_list_explains(tv):
    with pytest.raises(tv.NetworkError):
        tv.Tablo("person@example.com", "x", lan_only=True)


def test_cloud_down_falls_back_to_saved_list(tv, tablo):
    tv.CLOUD = "http://127.0.0.1:9"                          # nothing listens there
    again = tv.Tablo("person@example.com", "x")
    assert not again.online and len(again.channels) == 3


# ------------------------------------------------------- guide + playlists ----
def test_xmltv_is_valid_and_escaped(tv, tablo):
    favs = favs_for(tv, tablo, "9.1")
    root = ET.fromstring(tv.xmltv(tablo, favs))
    assert len(root.findall("channel")) == 3
    titles = [p.findtext("title") for p in root.findall("programme")]
    assert "Game Night & More" in titles                     # '&' survived escaping


def test_guide_refresher_builds_in_background(tv, tablo):
    import time
    favs = favs_for(tv, tablo)
    tv.start_guide_refresher(tablo, favs)
    for _ in range(50):
        if tv._xmltv_cache["xml"]:
            break
        time.sleep(0.1)
    assert tv._xmltv_cache["xml"].startswith(b"<?xml")


def test_tv_playlist_links_carry_access_key(tv, tablo):
    tv.S.access_key = "Hunter2Key"
    lines = tv.tv_m3u(tablo, favs_for(tv, tablo, "9.1"), "192.0.2.2:8000").decode().splitlines()
    assert lines[0].startswith("#EXTM3U") and "guide.xml?key=Hunter2Key" in lines[0]
    assert lines[2].startswith("http://192.0.2.2:8000/live/S9/1080/index.m3u8?key=Hunter2Key")


# ------------------------------------------------------------- templates ----
def test_fill_replaces_and_refuses_leftovers(tv):
    assert tv.fill("a %%X%% b {css}", X="1") == "a 1 b {css}"
    with pytest.raises(Exception):
        tv.fill("%%X%% %%Y%%", X="1")


# --------------------------------------------------------------- encoders ----
def test_encoder_auto_falls_back_to_software(tv, monkeypatch):
    monkeypatch.setattr(tv, "encoder_works", lambda name: False)
    tv.S.encoder, tv.S.encoder_setting = None, "auto"
    assert tv.choose_encoder() == "x264"


def test_encoder_auto_prefers_working_hardware(tv, monkeypatch):
    first = tv.AUTO_ORDER[0]
    monkeypatch.setattr(tv, "encoder_works", lambda name: name == first)
    tv.S.encoder, tv.S.encoder_setting = None, "auto"
    assert tv.choose_encoder() == first


def test_unknown_or_broken_encoder_setting_uses_software(tv, monkeypatch):
    monkeypatch.setattr(tv, "encoder_works", lambda name: False)
    for setting in ("bogus", "nvenc"):
        tv.S.encoder, tv.S.encoder_setting = None, setting
        assert tv.choose_encoder() == "x264"


def test_encoder_arguments(tv):
    for name in tv.ENCODERS:
        pre, vf, codec = tv.encoder_args(name, 720)
        assert vf.startswith("yadif") and "min(720,ih)" in vf
        assert "-c:v" in codec and "-g" in codec


@needs_ffmpeg
def test_software_encoder_really_works(tv):
    assert tv.encoder_works("x264")


# ------------------------------------------------------------- web server ----
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def web(tv, tablo):
    port = free_port()
    server = tv.start_server(tablo, favs_for(tv, tablo, "9.1"), port, lan=False)
    yield port
    server.RequestHandlerClass.end_all_sessions()    # stop converters, free tuners
    server.shutdown()
    server.server_close()


def get(port, path, source="127.0.0.1"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20, source_address=(source, 0))
    conn.request("GET", path)
    r = conn.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def test_pages_and_playlists(web):
    status, _, body = get(web, "/")
    assert status == 200 and b"KWTV" in body
    status, _, body = get(web, "/playlist.m3u")
    assert status == 200 and body.startswith(b"#EXTM3U")
    status, _, body = get(web, "/guide.xml")
    assert status == 200 and b"<tv" in body
    status, _, body = get(web, "/tv")
    assert status == 200 and b"%%" not in body              # every placeholder filled


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs 127.0.0.x loopback aliases (Linux)")
def test_access_key_for_other_devices(tv, web):
    tv.S.access_key = "Hunter2Key"
    other = "127.0.0.5"                                       # looks like another device
    assert get(web, "/", other)[0] == 401
    assert get(web, "/?key=wrong", other)[0] == 401
    status, headers, _ = get(web, "/?key=Hunter2Key", other)
    assert status == 200 and "tablo_key=" in headers.get("Set-Cookie", "")
    assert get(web, "/")[0] == 200                            # this PC is always trusted


# --------------------------------------------------- phone/TV conversion ----
@needs_ffmpeg
def test_browser_conversion_produces_h264_hls(tv, tablo, tmp_path, monkeypatch):
    """Make a tiny MPEG-2 'broadcast', let LiveTV convert it, check the HLS output."""
    import subprocess
    import time
    src = tmp_path / "broadcast.ts"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30", "-f", "lavfi", "-i", "sine", "-t", "8",
                    "-c:v", "mpeg2video", "-c:a", "ac3", str(src)], check=True)
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    quiet = type("Quiet", (SimpleHTTPRequestHandler,), {"log_message": lambda *a: None})
    files = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(quiet, directory=str(tmp_path)))
    threading.Thread(target=files.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{files.server_address[1]}/broadcast.ts"
    monkeypatch.setattr(tablo, "watch_session", lambda ident, who="": (url, ""))
    tv.S.encoder = "x264"
    live = tv.LiveTV(tablo)
    try:
        job = live.touch("S9", "viewer", 360)
        playlist = job.folder / "index.m3u8"
        for _ in range(100):
            if playlist.exists() and ".ts" in playlist.read_text():
                break
            time.sleep(0.2)
        assert ".ts" in playlist.read_text()
        seg = next(job.folder.glob("seg*.ts"))
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=codec_name", "-of", "json", str(seg)], capture_output=True, text=True)
        assert json.loads(probe.stdout)["streams"][0]["codec_name"] == "h264"
    finally:
        live.stop_all()
        files.shutdown()
    assert not any(p.name.startswith("tablo-live-") and p == job.folder for p in job.folder.parent.iterdir())


def test_script_has_no_syntax_errors_on_this_python():
    from conftest import SCRIPT
    compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec")


# ------------------------------------------- smart plug (--restart-tablo) ----
@pytest.fixture()
def quick_plug(tv, monkeypatch):
    """Shrink the 30 s off-time / 5 min wait so the tests take seconds."""
    monkeypatch.setattr(tv, "PLUG_OFF_SECONDS", 1.5)
    monkeypatch.setattr(tv, "PLUG_WAIT_SECONDS", 15)
    monkeypatch.setattr(tv, "PLUG_POLL_SECONDS", 0.2)
    return tv


def restart_args(tv, fake_tablo, *extra):
    host = fake_tablo.url.replace("http://", "")
    return tv.build_parser().parse_args(["--restart-tablo", "--yes", "--tablo-ip", host, *extra])


@pytest.mark.parametrize("kind", ["shelly1", "shelly2", "tasmota"])
def test_restart_tablo_power_cycles_and_waits(quick_plug, fake_tablo, make_plug, kind, caplog):
    tv = quick_plug
    caplog.set_level(logging.INFO)
    plug = make_plug(kind, password="plugpw" if kind != "shelly2" else "")
    t = {"smart_plug": f"{kind.rstrip('12')} {plug.host}", "smart_plug_password": plug.password}
    seen_off = []
    real_answers = tv.tablo_answers
    tv.tablo_answers = lambda url: (seen_off.append(not fake_tablo.powered), real_answers(url))[1]
    tv.cmd_restart_tablo(restart_args(tv, fake_tablo), t)
    assert any(seen_off), "the Tablo should have been off at some point"
    assert plug.on and fake_tablo.powered                      # switched itself back on
    assert "back online" in caplog.text
    assert "plugpw" not in caplog.text


def test_restart_tablo_with_plain_url_plug(quick_plug, fake_tablo, make_plug):
    tv = quick_plug
    plug = make_plug("url")
    t = {"smart_plug": "url", "smart_plug_off_url": f"http://{plug.host}/off",
         "smart_plug_on_url": f"http://{plug.host}/on"}
    tv.cmd_restart_tablo(restart_args(tv, fake_tablo), t)
    assert [r for r in plug.requests] == ["/off", "/on"] and fake_tablo.powered


def test_restart_asks_first_and_can_be_cancelled(quick_plug, fake_tablo, make_plug, monkeypatch):
    tv = quick_plug
    plug = make_plug("shelly2")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    args = tv.build_parser().parse_args(["--restart-tablo"])
    tv.cmd_restart_tablo(args, {"smart_plug": f"shelly {plug.host}"})
    assert not any("Switch.Set" in r for r in plug.requests)    # nothing switched


def test_test_plug_reports_state(tv, make_plug, capsys):
    plug = make_plug("tasmota")
    tv.cmd_test_plug({"smart_plug": f"tasmota {plug.host}"})
    assert "power is ON" in capsys.readouterr().out
    assert plug.on                                              # nothing switched


def test_plug_setup_mistakes_are_explained(tv):
    for t, words in (({}, "no smart plug"), ({"smart_plug": "kasa 1.2.3.4"}, "shelly, tasmota or url"),
                     ({"smart_plug": "shelly"}, "IP address"), ({"smart_plug": "url"}, "smart_plug_off_url")):
        with pytest.raises(tv.PlugError, match=words):
            tv.SmartPlug(t)
    with pytest.raises(tv.PlugError, match="can't reach"):
        tv.SmartPlug({"smart_plug": "shelly 127.0.0.1:9"}).is_on()


def test_wrong_plug_password_is_explained(tv, make_plug):
    plug = make_plug("shelly1", password="right")
    with pytest.raises(tv.PlugError, match="password"):
        tv.SmartPlug({"smart_plug": f"shelly {plug.host}", "smart_plug_password": "wrong"}).is_on()


# ------------------------------------------------------------ guide refresh ----
def test_manual_refresh_fetches_again_but_not_twice_in_a_row(tv, tablo, fake_tablo):
    tablo.now_playing()
    hits = fake_tablo.cloud_hits
    tablo.now_playing()                                      # cached: no new request
    assert fake_tablo.cloud_hits == hits
    tablo.refresh_guide()                                    # forced: one new request
    assert fake_tablo.cloud_hits == hits + 1
    tablo.refresh_guide()                                    # pressed again right away: reused
    assert fake_tablo.cloud_hits == hits + 1
    assert tablo.guide_time()                                # e.g. "5:02 PM"


def test_guide_refresh_setting(tv):
    cfg = tv.load_config()
    for value, expect in (("10", 10), ("0", 0), ("1", 2), ("abc", 2)):
        cfg["tablo"]["guide_refresh"] = value
        tv.apply_playback_settings(tv.build_parser().parse_args([]), cfg, cfg["tablo"])
        assert tv.S.guide_refresh == expect
    tv.apply_playback_settings(tv.build_parser().parse_args(["--guide-refresh", "15"]), cfg, cfg["tablo"])
    assert tv.S.guide_refresh == 15


def test_background_refresh_updates_whats_on(tv, tablo, fake_tablo):
    import time
    tablo.now_playing()
    hits = fake_tablo.cloud_hits
    tv.S.guide_refresh = 0.02                                # ~1 second, for the test
    tv.start_guide_refresher(tablo, favs_for(tv, tablo), iptv=False)
    for _ in range(50):
        if fake_tablo.cloud_hits > hits:
            break
        time.sleep(0.1)
    assert fake_tablo.cloud_hits > hits


def test_refresh_guide_web_button_and_cli(tv, tablo, web, fake_tablo, capsys):
    status, _, body = get(web, "/")
    assert status == 200 and b'id="gref"' in body and b"Guide as of" in body
    assert b'id="gref"' in get(web, "/tv")[2]
    conn = http.client.HTTPConnection("127.0.0.1", web, timeout=20)
    conn.request("POST", "/refresh-guide")
    r = conn.getresponse()
    info = json.loads(r.read())
    assert r.status == 200 and info["channels"] >= 1 and info["updated"]
    tablo._forced_at = 0                                     # allow another forced refresh
    args = tv.build_parser().parse_args(["--refresh-guide", "--port", str(web)])
    tv.cmd_refresh_guide(args, {})
    assert "Guide refreshed at" in capsys.readouterr().out


def test_refresh_guide_cli_when_nothing_is_running(tv):
    args = tv.build_parser().parse_args(["--refresh-guide", "--port", str(free_port())])
    with pytest.raises(SystemExit, match="isn't running"):
        tv.cmd_refresh_guide(args, {})


# ------------------------------------------------------- green addresses ---
def test_addresses_green_on_screen_but_plain_in_log(tv, capsys):
    tv.setup_logging()
    tv.S.use_color = True
    tv.log.info("This PC's local IP: 192.168.1.27   Tablo: http://192.168.1.84:8887")
    screen = capsys.readouterr().out
    assert f"{tv.ADDRESS}192.168.1.27{tv.RESET}" in screen
    assert f"{tv.ADDRESS}http://192.168.1.84:8887{tv.RESET}" in screen
    for h in tv.log.handlers:
        h.flush()
    assert "\033[" not in tv.LOG_FILE.read_text(encoding="utf-8")   # log file stays plain
    tv.S.use_color = False
    assert tv.color_addresses("at 10.0.0.5") == "at 10.0.0.5"          # --no-color / NO_COLOR
    for h in list(tv.log.handlers):
        tv.log.removeHandler(h)
        h.close()


# ------------------------------------------------------- --max-channels ----
def test_max_channels_limits_the_list_but_not_watching(tv, tablo, capsys):
    tablo.channels += [tv.Channel(identifier=f"X{i}", call_sign=f"KX{i:02d}", major=20 + i, minor=1,
                                  network="", kind="ota", stream_url="", logo="") for i in range(60)]
    favs = favs_for(tv, tablo, "9.1")
    tv.S.use_color = False
    tv.S.max_channels = 50
    numbered = tv.print_channels(tablo, favs, guide=False)
    out = capsys.readouterr().out
    assert "KX47" in out and "KX48" not in out               # 1 favorite + 49 more = 50 shown
    assert "more channels not shown (limit 50)" in out
    assert len(numbered) == 63                               # everything still has a number
    assert tv.resolve(tablo, favs, "79.1", numbered).call_sign == "KX59"   # hidden one still playable
    tv.S.max_channels = 0
    tv.print_channels(tablo, favs, guide=False)
    assert "KX59" in capsys.readouterr().out                 # 0 = show all


def test_max_channels_switch_and_setting(tv):
    assert tv.build_parser().parse_args(["--max-channels"]).max_channels == 50
    assert tv.build_parser().parse_args(["--max-channels", "30"]).max_channels == 30
    cfg = tv.load_config()
    cfg["tablo"]["max_channels"] = "40"
    tv.apply_playback_settings(tv.build_parser().parse_args([]), cfg, cfg["tablo"])
    assert tv.S.max_channels == 40
    args = tv.build_parser().parse_args(["--max-channels", "25"])
    tv.apply_display_options(args)
    tv.apply_playback_settings(args, cfg, cfg["tablo"])
    assert tv.S.max_channels == 25                           # the switch wins
    for h in list(tv.log.handlers):
        tv.log.removeHandler(h)
        h.close()


# --------------------------------------------- TABLO_VLC_DIR and --version ----
def test_settings_folder_can_be_moved(tmp_path):
    """TABLO_VLC_DIR (used by the Docker image) keeps tablo.ini and the log elsewhere."""
    import subprocess
    from conftest import SCRIPT
    data = tmp_path / "data" / "nested"
    env = {**os.environ, "TABLO_VLC_DIR": str(data), "NO_COLOR": "1"}
    r = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode != 0 and str(data / "tablo.ini") in r.stderr + r.stdout
    assert (data / "tablo.ini").exists() and (data / "tablo-vlc.log").exists()
    assert not (SCRIPT.parent / "data").exists()


def test_version(tv):
    from conftest import SCRIPT
    import subprocess
    r = subprocess.run([sys.executable, str(SCRIPT), "--version"], capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == f"tablo-vlc {tv.__version__}"


# -------------------------------------------------------- closed captions ----
@needs_ffmpeg
def test_captions_survive_conversion_for_phones_and_tvs(tv, tablo, tmp_path, monkeypatch):
    """A Tablo broadcast carries CEA-608 captions inside the MPEG-2 video; after the
    conversion to H.264 for phones/TVs they must still be there."""
    import functools
    import threading
    import time
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from conftest import make_captioned_broadcast, captions_in
    src = make_captioned_broadcast(tmp_path, "HELLO FROM TABLO")
    assert "HELLO FROM TABLO" in captions_in(src)                 # the test file itself is right
    quiet = type("Quiet", (SimpleHTTPRequestHandler,), {"log_message": lambda *a: None})
    files = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(quiet, directory=str(tmp_path)))
    threading.Thread(target=files.serve_forever, daemon=True).start()
    monkeypatch.setattr(tablo, "watch_session",
                        lambda ident, who="": (f"http://127.0.0.1:{files.server_address[1]}/{src.name}", ""))
    tv.S.encoder = "x264"
    live = tv.LiveTV(tablo)
    try:
        job = live.touch("S9", "viewer", 720)
        for _ in range(150):
            segs = sorted(job.folder.glob("seg*.ts"))
            if len(segs) >= 2:
                break
            time.sleep(0.2)
        assert "HELLO FROM TABLO" in captions_in(segs[0])
    finally:
        live.stop_all()
        files.shutdown()


def test_pages_turn_on_captions(tv, tablo, web):
    phone, tvpage = get(web, "/")[2], get(web, "/tv")[2]
    assert b"enableCEA708Captions: true" in phone and b"enableCEA708Captions: true" in tvpage
    assert b"toggleCC" in tvpage and b'"cc": false' in phone


# ------------------------------------------- recent channels + guide search ----
def test_recently_watched_is_remembered(tv, tablo):
    for ident in ("S9", "S4", "S9", "O1"):
        tablo.remember_watched(ident)
    assert [c.identifier for c in tablo.recent()] == ["O1", "S9", "S4"]
    again = tv.Tablo("person@example.com", "x", lan_only=True)       # next run: from the saved cache
    assert [c.identifier for c in again.recent()] == ["O1", "S9", "S4"]


def test_guide_search(tv, tablo, capsys):
    favs = favs_for(tv, tablo, "9.1")
    hits = tv.search_guide(tablo, favs, "jeopardy")                 # on now
    assert [(c.identifier, tv.when(a)) for c, a in hits] == [("S9", "NOW")]
    hits = tv.search_guide(tablo, favs, "game night")               # coming up on a favorite
    assert hits and hits[0][0].identifier == "S9" and tv.when(hits[0][1]) != "NOW"
    assert tv.search_guide(tablo, favs, "nothing like this") == []
    tv.S.use_color = False
    tv.print_search(tablo, favs, "jeopardy")
    out = capsys.readouterr().out
    assert "NOW" in out and "9.1" in out and "Jeopardy!" in out


# ------------------------------------------------------------ tuner status ----
def test_tuner_status_counts_what_this_script_holds(tv, tablo, fake_tablo):
    tablo.load_tuner_info()
    assert (tablo.tuner_total, tablo.model) == (4, "Tablo 4G QUAD")
    assert tablo.tuner_summary() == "Tuners: 0 of 4 in use by this script"
    _, t1 = tablo.watch_session("S9", who="VLC on this PC")
    _, t2 = tablo.watch_session("S4", who="phone/TV browser")
    assert tablo.tuner_summary() == "Tuners: 2 of 4 in use by this script (9.1 KWTV, 4.1 KFOR)"
    assert "using 2 of 4 tuners" in tablo.busy_hint()
    tablo.end_session(t1)
    assert tablo.tuner_summary() == "Tuners: 1 of 4 in use by this script (4.1 KFOR)"
    tablo.watch_session("O1")                                 # FAST: no tuner used
    assert len(tablo.tuner_status()["ours"]) == 1
    again = tv.Tablo("person@example.com", "x", lan_only=True)   # count saved for offline use
    assert again.tuner_total == 4


def test_tuner_status_when_the_tablo_reports_busy_tuners(tv, tablo, fake_tablo):
    """The 4th Gen's /server/tuners: in_use, plus the channel or recording on each tuner."""
    t = lambda **kw: {"in_use": True, "channel": None, "recording": None, "channel_identifier": None, **kw}
    fake_tablo.tuner_list = [t(channel_identifier="S9"),                      # this script
                             t(channel="/guide/channels/S4", channel_identifier="S4"),   # another TV
                             t(recording="/recordings/123", channel_identifier="S4"),    # recording
                             {"in_use": False, "channel": None, "recording": None, "channel_identifier": None}]
    tablo.load_tuner_info()
    tablo.watch_session("S9", who="VLC on this PC")
    assert tablo.tuner_summary() == ("Tuners: 3 of 4 busy (1 by this script: 9.1 KWTV; 1 recording; "
                                     "1 on other TVs or apps)")
    assert tablo.tuner_report()["free"] == 1


def test_tuners_busy_on_no_channel_look_stuck(tv, tablo, fake_tablo, capsys):
    """What a stuck 4th Gen reported: every tuner in use, none on a channel or recording."""
    fake_tablo.tuner_list = [{"in_use": True, "channel": None, "recording": None, "channel_identifier": None}] * 4
    tablo.load_tuner_info()
    assert tablo.tuner_summary() == "Tuners: 4 of 4 busy (0 by this script; 4 busy with no channel – Tablo may be stuck)"
    assert tablo.tuner_report()["stuck"] == 4
    assert "restart of the Tablo" in tablo.busy_hint()
    tv.S.use_color = False
    tv.print_tuners(tablo, detail=True)
    assert "restart the Tablo" in capsys.readouterr().out


def test_menu_tuner_details(tv, tablo, monkeypatch, capsys):
    import builtins
    tv.S.use_color = False
    tablo.load_tuner_info()
    tablo.watch_session("S9", who="VLC on this PC")
    answers = iter(["t", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    tv.menu(tablo, favs_for(tv, tablo, "9.1"), 8000, False)
    out = capsys.readouterr().out
    assert "Tuners: 1 of 4 in use by this script (9.1 KWTV)   (t = details)" in out
    assert "Tablo 4G QUAD: 4 tuners" in out and "VLC on this PC" in out and "3 free" in out


# --------------------------------------------------------------- multi-view ----
def test_second_vlc_window_keeps_the_first_channel(tv, tablo, web, fake_tablo):
    """/ch/<id>?w=2 (menu: w 4.1) gets its own tuner; the first window keeps its channel."""
    assert get(web, "/ch/S9")[0] == 302                       # window 1 (direct mode: redirect)
    assert get(web, "/ch/S4?w=2")[0] == 302                   # window 2
    assert fake_tablo.ended == []                             # nothing was freed
    assert len(tablo.tuner_status()["ours"]) == 2
    who = sorted(w for _, w, _ in tablo.tuner_status()["ours"])
    assert who == ["VLC on this PC", "player window 2 on this PC"]
    assert get(web, "/ch/S4")[0] == 302                       # window 1 switches channel...
    assert [t for t, _ in fake_tablo.ended] == ["SESSION-S9-zz99"]   # ...so only its old tuner is freed


def test_launch_second_window_uses_its_own_vlc(tv, monkeypatch):
    used = []
    monkeypatch.setattr(tv.VLCControl, "play", lambda self, *a: used.append(self) or True)
    tv.S.player = {"name": "VLC", "cmd": ["vlc"], "kind": "vlc"}
    tv.launch_player("http://x/ch/1")
    tv.launch_player("http://x/ch/2?w=2", window=2)
    assert used == [tv.VLC, tv.VLC2]


class _FakeProc:
    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_one_device_can_watch_several_channels_in_slots(tv, tablo, tmp_path, monkeypatch):
    """Multi-view: viewer '1.2.3.4#1' and '1.2.3.4#2' are separate players; switching
    one slot frees only that slot's old channel."""
    stopped = []
    live = tv.LiveTV(tablo)
    monkeypatch.setattr(live, "_start", lambda ident, q: tv.LiveJob(ident, q, ident, "", tmp_path, _FakeProc()))
    monkeypatch.setattr(live, "_shutdown", lambda job: stopped.append(job.ident) if job else None)
    live.touch("S9", "1.2.3.4#1", 720)
    live.touch("S4", "1.2.3.4#2", 720)
    assert set(k[0] for k in live.jobs) == {"S9", "S4"} and stopped == []
    live.touch("O1", "1.2.3.4#1", 720)                       # slot 1 switches channel
    assert stopped == ["S9"] and set(k[0] for k in live.jobs) == {"S4", "O1"}
    live.touch("S4", "1.2.3.4", 720)                         # (no slot: the single-player page)
    live.touch("S9", "1.2.3.4", 720)
    assert "S4" not in stopped                               # slot 2 is still watching S4


@needs_ffmpeg
def test_multi_view_page(tv, tablo, web):
    status, _, body = get(web, "/multi")
    assert status == 200 and b"%%" not in body
    assert b'"label": "9.1 KWTV"' in body and b'"group": "Favorites"' in body
    assert b"slot=" in body and b'id="n4"' in body
    assert b'id="all"' in body and b'id="none"' in body and b"/multi/stop" in body   # start/stop
    assert b'href="/multi"' in get(web, "/")[2]               # linked from the main page


@needs_ffmpeg
def test_multi_view_two_players_from_one_device(tv, tablo, web, tmp_path, monkeypatch):
    """Real conversion: one device plays two channels (slot 1 and 2) at once, and the
    segment links carry the slot so both players stay counted."""
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from conftest import make_captioned_broadcast
    src = make_captioned_broadcast(tmp_path, "MULTI", seconds=20)
    quiet = type("Quiet", (SimpleHTTPRequestHandler,), {"log_message": lambda *a: None})
    files = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(quiet, directory=str(tmp_path)))
    threading.Thread(target=files.serve_forever, daemon=True).start()
    tuned, ended = [], []
    monkeypatch.setattr(tablo, "watch_session", lambda ident, who="": (
        tuned.append(ident) or f"http://127.0.0.1:{files.server_address[1]}/{src.name}", f"tok-{ident}"))
    monkeypatch.setattr(tablo, "end_session", lambda token: ended.append(token))
    tv.S.encoder = "x264"
    try:
        s1, _, p1 = get(web, "/live/S9/index.m3u8?slot=1")
        s2, _, p2 = get(web, "/live/S4/index.m3u8?slot=2")
        assert s1 == s2 == 200 and sorted(tuned) == ["S4", "S9"]
        assert b"seg00000.ts?slot=1" in p1 and b"seg00000.ts?slot=2" in p2
        assert get(web, "/live/S9/seg00000.ts?slot=1")[0] == 200
        assert ended == []                                        # both still playing
    finally:
        files.shutdown()


def test_menu_second_window_and_multi_view(tv, tablo, monkeypatch, capsys):
    import builtins
    tv.S.use_color = False
    calls, opened = [], []
    monkeypatch.setattr(tv, "launch_player", lambda url, meta=None, playlist=False, detach=False, window=1:
                        calls.append((url, window)) or True)
    monkeypatch.setattr(tv, "open_url", lambda url: opened.append(url))
    monkeypatch.setattr(tv.LiveTV, "available", staticmethod(lambda: True))
    answers = iter(["9.1", "w 4.1", "w off", "m", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    tv.menu(tablo, favs_for(tv, tablo, "9.1"), 8000, False)
    out = capsys.readouterr().out
    assert calls == [("http://127.0.0.1:8000/ch/S9", 1), ("http://127.0.0.1:8000/ch/S4?w=2", 2)]
    assert "Playing 4.1 KFOR in window 2" in out and "No second window is open." in out
    assert opened == ["http://127.0.0.1:8000/multi"]
    assert "restart with --lan, then open http://" in out and ":8000/multi" in out


# ----------------------------------------- the menu keeps working while watching ----
@needs_ffmpeg
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc (Linux)")
def test_vlc_relay_never_reads_the_terminal(tv, tablo, web, tmp_path, monkeypatch):
    """Bug fixed in 1.3.1: the ffmpeg relay for VLC inherited the terminal and
    swallowed what you typed in the menu (e.g. 'w 4.1') until VLC closed."""
    import os
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from conftest import make_captioned_broadcast
    data = make_captioned_broadcast(tmp_path, "X", seconds=20).read_bytes()

    class Slow(BaseHTTPRequestHandler):            # trickles like a live broadcast
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            try:
                for i in range(0, len(data), 188 * 1000):
                    self.wfile.write(data[i:i + 188 * 1000])
                    time.sleep(0.1)
            except OSError:
                pass
    src = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=src.serve_forever, daemon=True).start()
    monkeypatch.setattr(tablo, "watch_session", lambda ident, who="": (f"http://127.0.0.1:{src.server_address[1]}/", ""))
    tv.S.use_ffmpeg = True
    conn = http.client.HTTPConnection("127.0.0.1", web, timeout=20)
    conn.request("GET", "/ch/S9")
    resp = conn.getresponse()
    resp.read(188 * 10)                                         # the relay is running
    relays = []
    for pid in os.listdir("/proc"):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
            if cmd and cmd[0].endswith(b"ffmpeg") and b"pipe:1" in cmd:
                relays.append((cmd, os.readlink(f"/proc/{pid}/fd/0")))
        except OSError:
            pass
    conn.close()
    src.shutdown()
    assert relays, "relay ffmpeg not found"
    for cmd, stdin in relays:
        assert b"-nostdin" in cmd and stdin == "/dev/null"


def test_multi_view_stop_frees_the_tuner_right_away(tv, tablo, tmp_path, monkeypatch):
    stopped = []
    live = tv.LiveTV(tablo)
    monkeypatch.setattr(live, "_start", lambda ident, q: tv.LiveJob(ident, q, ident, "", tmp_path, _FakeProc()))
    monkeypatch.setattr(live, "_shutdown", lambda job: stopped.append(job.ident) if job else None)
    live.touch("S9", "1.2.3.4#1", 540)
    live.touch("S4", "1.2.3.4#2", 540)
    live.touch("S4", "5.6.7.8", 540)                        # another device also watches S4
    live.release("1.2.3.4#1")                                # ■ on slot 1
    assert stopped == ["S9"]
    live.release("1.2.3.4#2")                                # ■ on slot 2: S4 keeps going
    assert stopped == ["S9"] and ("S4", 540) in live.jobs
    live.release("1.2.3.4#3")                                # nothing there: no harm
    assert stopped == ["S9"]


def test_multi_view_stop_endpoint(tv, tablo, web):
    conn = http.client.HTTPConnection("127.0.0.1", web, timeout=20)
    conn.request("POST", "/multi/stop?slot=all")
    r = conn.getresponse()
    assert r.status == 200 and r.read() == b"ok"


def test_tuner_status_for_web_pages(tv, tablo, web, fake_tablo):
    fake_tablo.tuner_list = [{"in_use": True}] * 4
    tablo.load_tuner_info()
    status, _, body = get(web, "/tuners.json")
    t = json.loads(body)
    assert status == 200 and (t["total"], t["busy"], t["free"]) == (4, 4, 0)
    assert t["summary"] == "Tuners: 4 of 4 busy (0 by this script; 4 busy with no channel – Tablo may be stuck)"
    assert t["stuck"] == 4
    assert t["model"] == "Tablo 4G QUAD"


def test_slow_or_silent_conversion_is_explained_in_the_log(tv, tablo, tmp_path, caplog):
    import time
    caplog.set_level(logging.WARNING)
    live = tv.LiveTV(tablo)
    slow = tv.LiveJob("S9", 720, "9.1 KWTV", "", tmp_path, _FakeProc())
    slow.started = time.time() - 60                         # 60 s in: ~25 segments expected
    for i in range(4):
        (tmp_path / f"seg{i:05d}.ts").write_bytes(b"x")
    live._check_speed(slow)
    assert "converting slower than real time (4 of ~25" in caplog.text
    empty = tmp_path / "empty"
    empty.mkdir()
    silent = tv.LiveJob("S4", 720, "4.1 KFOR", "", empty, _FakeProc())
    silent.started = time.time() - 60
    live._check_speed(silent)
    assert "[4.1 KFOR] no video from the Tablo after 60 s" in caplog.text
    fine = tmp_path / "fine"
    fine.mkdir()
    ok = tv.LiveJob("S4", 720, "4.1 OK", "", fine, _FakeProc())
    ok.started = time.time() - 60
    for i in range(25):
        (fine / f"seg{i:05d}.ts").write_bytes(b"x")
    live._check_speed(ok)
    assert "4.1 OK" not in caplog.text


@needs_ffmpeg
def test_conversion_rides_out_a_tablo_outage(tv, tablo, monkeypatch):
    """The Tablo (or Wi-Fi) drops out for a few seconds mid-stream: ffmpeg is restarted
    in the same folder and the playlist carries on numbering where it left off, so
    players keep going instead of freezing on a playlist that started over."""
    import time
    from conftest import live_tablo_stream
    url = live_tablo_stream(down=(8, 14))
    monkeypatch.setattr(tablo, "watch_session", lambda ident, who="": (url, "tok"))
    monkeypatch.setattr(tablo, "end_session", lambda token: None)
    tv.S.encoder = "x264"
    live = tv.LiveTV(tablo)
    try:
        job = live.touch("S9", "viewer", 360)
        folder = job.folder

        def newest():
            segs = [int(p.stem[3:]) for p in folder.glob("seg*.ts")]
            return max(segs) if segs else -1
        time.sleep(7)
        before = newest()
        time.sleep(20)                                        # through the outage and after
        assert job.restarts >= 1 and not job.gave_up
        assert job.folder == folder and newest() > before + 3      # kept numbering upward
        assert "#EXT-X-DISCONTINUITY" in (folder / "index.m3u8").read_text() or newest() > before + 6
    finally:
        live.stop_all()


@needs_ffmpeg
def test_diagnose_one_channel(tv, tablo, monkeypatch, capsys):
    from conftest import live_tablo_stream
    url = live_tablo_stream()
    ended = []
    monkeypatch.setattr(tablo, "watch_session", lambda ident, who="": (url, "tok-diag"))
    monkeypatch.setattr(tablo, "end_session", lambda token: ended.append(token))
    tv.S.encoder = "x264"
    tv.cmd_diagnose(tablo, tablo.by_id("S9"))
    out = capsys.readouterr().out
    for step in ("1. Tune: OK", "2. Stream address: http://127.0.0.1", "3. Tablo playlist: OK",
                 "4. Tablo video segment: OK", "5. Broadcast: video mpeg2video",
                 "6. Test conversion (software (libx264))", "7. Converted video: video h264 High", "8. Picture: not black"):
        assert step in out, step
    assert ended == ["tok-diag"]                                  # the tuner is freed


@needs_ffmpeg
def test_diagnose_plays_two_channels_like_multi_view(tv, tablo, monkeypatch):
    from conftest import live_tablo_stream
    urls = {"S9": live_tablo_stream(), "S4": live_tablo_stream()}
    monkeypatch.setattr(tablo, "watch_session", lambda ident, who="": (urls[ident], f"tok-{ident}"))
    monkeypatch.setattr(tablo, "end_session", lambda token: None)
    tv.S.encoder = "x264"
    lines = tv.simulate_players(tablo, favs_for(tv, tablo), [tablo.by_id("S9"), tablo.by_id("S4")], seconds=16)
    text = "\n".join(lines)
    assert "9.1 KWTV: first video after" in text and "4.1 KFOR: first video after" in text, text
    assert "NO VIDEO" not in text and "too little video" not in text, text
