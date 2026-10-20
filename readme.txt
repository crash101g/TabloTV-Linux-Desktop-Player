===============================================================================
 tablo-vlc  -  Tablo 4th Gen live TV on your Linux laptop or desktop
===============================================================================

The Tablo TV 4th Gen only streams to its own certified support apps on
phones, certain streaming sticks, and newer Samsung TVs.

There's no Tablo app for Linux, so I built a way to watch on my laptop. If
your desktop runs Ubuntu, Pop!_OS or another Linux distro, you're left out.
tablo-vlc is a single Python file that brings your Tablo's live TV to your
Linux laptop or desktop. It started out built around VLC, but VLC is no
longer required: use ffplay, mpv or another media player, and on some
platforms a plain web browser is all you need.


  A NOTE BEFORE YOU START: I don't plan on supporting this. It's shared
  as-is, simply because it worked for me and might help someone else. Feel
  free to use it, change it, or fork it, but please don't expect fixes,
  updates or answers to support requests.


HOW IT STARTED
--------------
I couldn't sleep, so at 3am I went looking for a way to watch my Tablo on
my Linux laptop. On the Tablo Community forums and on Reddit I found
someone's Linux app for it: a 150 MB AppImage. It might have been fine, but
a 150 MB bundle isn't something you can easily audit at 3am, and I wasn't
going to run code I hadn't looked at. That would have to wait until later in
the day when I had more time.

  The AppImage I found (for reference):
    Size:    about 150 MB
    SHA256:  13bb96fc4c5a61ff45a2150a8673d03c6d1b332b116ed7e12b7af0aa1b230ec7

All I really wanted was to watch a few local channels in VLC. So instead I
vibe coded my own with Claude: one small, readable Python file that I could
review line by line. I described what I wanted, tested it on my own Tablo
and laptop, and kept asking for "one more thing". A few hours and about $10
in usage credits later, that simple idea had grown into everything below.

Along the way I also removed the VLC dependency. VLC is still the best
experience on the desktop, but the script can just as easily use ffplay,
mpv or another player, and phones, tablets and TVs can watch in an ordinary
web browser with no app at all. It can even run headless on a Linux server,
container or VM, feeding every other screen in the house.


QUICK START
-----------
  chmod +x tablo-vlc.py
  ./tablo-vlc.py            first run creates tablo.ini: add your Tablo
                            email and password, then run it again
  ./tablo-vlc.py            channel menu: type a number to watch
  ./tablo-vlc.py --lan      share with phones, tablets and TVs on your Wi-Fi
  ./tablo-vlc.py --help     everything else


MADE FOR THE LINUX LAPTOP AND DESKTOP
-------------------------------------
  * Runs in any terminal on GNOME, COSMIC, KDE and others. Tested on
    Ubuntu 26 and Pop!_OS COSMIC, with and without VLC installed.
  * Laptop-friendly: works over Wi-Fi, keeps playing local channels when the
    internet drops, and picks up again when you reconnect.
  * Finds VLC however you installed it: apt, Snap or Flatpak (Pop!_OS's
    default). Falls back to ffplay or mpv, or pick one with --player.
  * No install, no packages: uses only the Python your distro already ships.
  * Everything lives next to the script: your login, favorites and settings
    in a plain tablo.ini, activity in tablo-vlc.log. Easy to back up, copy to
    another PC, or edit by hand.


YOUR CHOICE OF PLAYER (VLC NOT REQUIRED)
----------------------------------------
  * VLC (apt, Snap or Flatpak) is used automatically when it's installed:
    best experience, same-window channel switching, show titles.
  * No VLC? It falls back to ffplay (comes with ffmpeg).
  * Prefer something else? --player mpv, --ffplay, or set player = in
    tablo.ini to any media player command.
  * No player at all? Use the built-in web page: on a PC without a player,
    and on phones, tablets and TV browsers, Play watches right in the browser
    (needs ffmpeg on the PC doing the converting).
  * Even with no player installed, the menu, guide, favorites and playlists
    all still work.


WATCHING TV, THE TERMINAL WAY
-----------------------------
  * A fast channel menu: favorites first (in color), local over-the-air
    channels, then free FAST channels, laid out in columns to fit your window.
  * See what's on: current show, episode and time left for every channel.
    i3 shows the description and what's coming up next.
  * Type a number and it plays in VLC. Pick another and it switches in the
    same VLC window, without piling up a playlist.
  * One-liners: --play 2.1, --play favorites, --info 9.1, --add-fav 9.1 KFOR
  * VLC shows the channel and program name in its title bar, and the
    description in Media Information.


TUNED FOR LIVE TV IN VLC
------------------------
  * 3-second live buffer: smooth playback with quick channel changes.
    If VLC stutters on Wi-Fi hiccups, raise it to 10 seconds: set
    vlc_buffer = 10000 in tablo.ini, or try it for one run with
    --buffer 10000. The trade-off is a few more seconds to start a channel
    or switch.
  * Automatic yadif2x deinterlacing for 1080i stations.
  * Plays the Tablo's original stream: full quality, no re-encoding, light on
    the CPU.
  * Frees the Tablo's tuner the moment you switch or quit, so recordings
    aren't blocked.


KEEPS WORKING WHEN THINGS GO WRONG
---------------------------------
  * Internet down? Local channels keep playing from a saved channel list,
    and it reconnects on its own when the internet returns.
  * --offline mode: home network only, zero internet traffic.
  * Retries brief hiccups and finds the Tablo again if its address changes
    (or set it with --tablo-ip).
  * Logs back in on its own when the Tablo cloud login expires, so a box left
    running for days keeps its TV guide.
  * One device starting a channel never makes other devices' video stall.
  * Cleans up leftover temp files if it was ever killed mid-stream.
  * Plain-English error messages instead of tracebacks (for example, a DNS
    filter blocking Tablo's servers).


PRIVACY AND SECURITY
--------------------
  * One readable Python file, commented throughout, with a map of how it's
    organized at the top. No binaries, no bundled libraries: you can read
    exactly what it sends and where before you run it.
  * Your password lives only in tablo.ini, which is readable only by you.
    Or keep it in your desktop's keyring instead: --setup offers it when
    GNOME Keyring/KWallet (secret-tool) or Python's keyring module is there,
    and tablo.ini then just says  password = keyring.
  * Logs are safe to share: login tokens, stream session tokens, your
    password and access key are replaced with short fingerprints like
    [token:a91f]. Use --debug-raw only if you really need the full values
    (and don't share that log).
  * Optional access key for --lan: set  access_key = something  in tablo.ini
    and other devices must open  http://<PC-IP>:8000/?key=something  once
    (a cookie remembers it). The PC itself never needs the key, and the
    playlist and Cast links carry it automatically. Without a key, anyone on
    your Wi-Fi can watch, which is fine at home but not on shared Wi-Fi.
  * Uses your own Tablo account. What it connects to:
      - Tablo's cloud (login, channel list, guide) and your Tablo itself
      - GitHub, once, for the public signing keys (if they're not in
        tablo.ini)
      - jsDelivr, once, for the small player library phones/TVs use
      - FAST channel providers, when you watch a FAST channel
      - Google's Cast library, loaded by a phone/PC browser when Cast is used
    With --offline it connects to nothing but your Tablo.


BONUS: I ADDED SUPPORT FOR MORE PLATFORMS
-----------------------------------------
Once it worked on Linux, I kept going. The same single file now also covers:

  Windows 11
    Run it with:  py tablo-vlc.py
    Finds VLC in its normal Windows location; colors, the web page and the
    guide all work.

  Every screen in the house, with --lan
    Your PC becomes a Tablo hub for other devices on your Wi-Fi.
    Open the address shown at startup, e.g. http://192.168.1.20:8000/
    (every startup prints this PC's local IP and the Tablo's, even without
    --lan, so you know what to type, or which IP to reserve in your router)
    (set access_key in tablo.ini to require a key; see Privacy and Security)

    HOW TO WATCH ON EACH DEVICE (no VLC required)
    Replace 192.168.1.20 with the address shown when the script starts.

    Phones and tablets
    ------------------
    Apple iPhone / iPad
      - Open http://192.168.1.20:8000/ in Safari or Chrome and tap Play.
      - Optional: AirPlay to a TV.

    Android phone
      - Open http://192.168.1.20:8000/ in Google Chrome and tap Play.
      - Optional: Cast to a TV.

    TVs and streaming sticks
    ------------------------
    Samsung TV
      - AirPlay from an iPhone (tested), or
      - open http://192.168.1.20:8000/tv in the TV's Internet app.

    Amazon Fire TV
      - Open http://192.168.1.20:8000/tv in the Silk browser.
        The page is made for the TV remote.

    Google TV / Walmart onn
      - Cast from a phone or PC.

    Apple TV
      - AirPlay from an iPhone or iPad.

    Roku
      - Easiest: Tablo's own Roku app works with the 4th Gen.
      - Or AirPlay from an iPhone or iPad to a Roku that supports
        AirPlay (most 4K models).
      - Roku has no web browser and doesn't support Cast, so the
        web pages can't be opened on the Roku itself.

    Apps
    ----
    IPTV apps (TiviMate, UHF, iPlayTV, Kodi)
      - Playlist:  http://192.168.1.20:8000/tv.m3u
      - TV guide:  http://192.168.1.20:8000/guide.xml

    VLC app on any device
      - Playlist:  http://192.168.1.20:8000/playlist.m3u
        (the original stream, not converted)

  Run it headless (Linux server, container or VM)
    No desktop, screen or media player needed. If you're watching on another
    streaming-capable device (phone, tablet, TV, IPTV app), the script can run
    on an always-on box: a home server, a Proxmox/VirtualBox VM, an LXC or
    Docker container, even a Raspberry Pi.

      ./tablo-vlc.py --lan --no-vlc

    That runs only the server part: the phone/TV web pages, the TV playlist,
    the XMLTV guide and the ffmpeg conversion. It needs Python 3.10+ and
    ffmpeg. Notes:
      * It must be able to reach the Tablo on your home network. Give a VM a
        bridged network adapter; run Docker with --network host (or publish
        port 8000), so phones and TVs can reach it.
      * Ctrl+C or a normal service stop shuts it down cleanly and frees any
        Tablo tuners in use.
      * Conversion uses the graphics chip when it can (see below); in
        software, budget roughly one modern CPU core per channel being
        watched at 1080p (less at 720p).

  Behind the scenes, ffmpeg on the PC converts the Tablo's raw broadcast to
  H.264 (720p for phones, 1080p for TVs), so every device plays it smoothly.
  Several devices on the same channel share one tuner.

  It uses your graphics chip for that when it can (Intel/AMD VAAPI, NVIDIA
  NVENC, Intel Quick Sync, AMD AMF), which keeps a laptop cool and quiet. It
  checks with a one-second test at startup and falls back to software
  (x264) if the chip isn't usable, or if it stumbles on a real broadcast.
  The log says which one it picked. To force one, set  encoder = x264  (or
  vaapi, nvenc, qsv, amf) in tablo.ini, or use --encoder for one run.

  The TV guide for IPTV apps (/guide.xml) is built in the background at
  startup and refreshed every 30 minutes, so apps never wait for it.

  TV page remote keys: arrows + OK pick a channel, CH+/CH- or up/down change
  channel while watching, Back returns to the list.


REQUIREMENTS
------------
  Linux:       Python 3.10+ (included), plus a media player: VLC (apt, Snap
               or Flatpak), ffplay or mpv. Or just a web browser with ffmpeg.
  Windows 11:  Python 3.10+ (python.org or Microsoft Store), plus VLC,
               ffplay or mpv. Or just a web browser with ffmpeg.
  Phones/TVs:  ffmpeg on the PC running the script
               (Linux: sudo apt install ffmpeg
                Windows: winget install Gyan.FFmpeg)


FILES IT USES (all next to the script)
---------------------------------------
  tablo.ini          your Tablo login, favorites, settings and optional
                     access key  (keep private)
  tablo-cache.json   saved channel list for offline use
  tablo-vlc.log      activity log for troubleshooting (tokens scrubbed,
                     safe to share)
  tablo-hls.min.js   player library for phone/TV browsers (downloaded once)

  Also in the download (not needed to run it):
  tests/             automated tests with a fake Tablo:  python3 -m pytest
  .github/           runs those tests on Linux and Windows on every GitHub push
  .gitignore         keeps tablo.ini, the cache and logs out of GitHub

  If you publish this on GitHub, add a .gitignore with:
      tablo.ini
      tablo-cache.json
      *.log*
  so nobody publishes their password or home network address. (A ready-made
  .gitignore is included.)


TESTING STATUS
--------------
Tested by me on a real Tablo 4th Gen:
  * Ubuntu 26        with VLC and without VLC installed
  * Pop!_OS COSMIC   with VLC (Flatpak) and without VLC installed
  * Apple iPhone     Safari browser, watching through the web page
  * Samsung TV       AirPlay from the iPhone's Safari player
  That covers the core: Tablo login, the channel list and guide, favorites,
  and live TV actually playing on the Linux desktop, on the iPhone, and on a
  Samsung TV via AirPlay (which also means the ffmpeg conversion works with
  real Tablo broadcasts).

Also tested in simulation (a mock Tablo, plus a real VLC where noted).
28 of these checks ship as an automated test suite in tests/:
  Linux desktop
  * Menu, columns, colors, favorites; --play, --info, --list, --tune
  * Request signing against the Tablo's local tuning endpoint
  * Same-window channel switching in a real VLC
  * Buffer (3 s default, adjustable) and deinterlacing options passed to VLC
  * Finding regular, Snap and Flatpak VLC; ffplay and mpv fallbacks;
    --player / --ffplay; playback with no player installed
  * Offline cache, --offline (zero cloud requests), auto-reconnect, retries
  * Tuner release: correctly signed "end session" calls
  * Log scrubbing: no tokens, password or access key in tablo-vlc.log
  * Automatic re-login when the cloud login expires
  * A slow tune on one device doesn't stall another device's video
  * Leftover temp folders cleaned up; folders still in use left alone
  * Password from the system keyring (password = keyring)
  Bonus platforms
  * Windows code paths (checked on Linux)
  * Phone/TV conversion: 1080i MPEG-2 in, 720p/1080p H.264 + AAC out
  * Phone page and TV page (with remote keys) in phone- and TV-size browsers
  * TV playlist and a valid XMLTV guide, built in the background
  * Encoder choice: auto picks a working graphics encoder, falls back to
    software when none works or the setting is wrong
  * Headless server mode (--lan --no-vlc, no terminal or player attached)
  * Access key: other devices refused without it, admitted with it, cookie
    remembered, key carried in playlist, guide, video and Cast links

Not tested on real hardware (built and simulation-tested only):
  * Windows 11
  * Android phones, Fire TV, Google TV/onn, Apple TV and Roku (AirPlay)
  * The TV page in a Samsung TV's own Internet browser (AirPlay to Samsung
    is tested)
  * Cast to Google TV/onn/Chromecast, and AirPlay to Apple TV
  * IPTV apps (TiviMate, UHF, iPlayTV, Kodi) with the playlist and guide
  * Whether the Tablo honors the tuner-release call, and allows local tuning
    with no internet at all
  * Running inside an actual container or VM (and the CPU-per-channel
    estimate above)
  * Graphics-chip (hardware) encoding: the test machine had no GPU, so only
    the detection and the software fallback were tested
  * The system keyring on a real desktop (tested with a stand-in)
  * The GitHub Actions workflow (the same tests pass locally on Python
    3.11 and 3.13)


-------------------------------------------------------------------------------
Shared as-is, with no support or warranty: this is what worked for me, not a
maintained project. Tablo can change its service at any time, which may stop
this from working.
Unofficial; not affiliated with Tablo or Nuvyyo. Uses your own Tablo account.
Builds on the open-source tablo-api and tablo2plex projects.
Vibe coded with Claude (Anthropic) at 3am for about $10 in usage credits.
-------------------------------------------------------------------------------
