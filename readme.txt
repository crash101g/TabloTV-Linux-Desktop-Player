===============================================================================
 tablo-vlc  -  Tablo 4th Gen live TV on your Linux laptop or desktop
===============================================================================

There's no Tablo app for Linux, so I built a way to watch on my laptop.

The Tablo 4th Gen only streams to its own apps on phones, smart TVs and
streaming sticks. If your desktop runs Ubuntu, Pop!_OS or another Linux
distro, you're left out. tablo-vlc is a single Python file that brings your
Tablo's live TV to your Linux laptop or desktop. It started out built around
VLC, but VLC is no longer required: use ffplay, mpv or another media player,
and on some platforms a plain web browser is all you need.


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
  * 3-second live buffer for smooth playback (adjustable).
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
  * Plain-English error messages instead of tracebacks (for example, a DNS
    filter blocking Tablo's servers).


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

    Device                        How it watches (no VLC needed)
    ----------------------------  ---------------------------------------------
    iPhone / iPad                 Safari: tap Play. AirPlay to a TV.
    Android phone                 Chrome: tap Play. Cast to a TV.
    Amazon Fire TV                Remote-friendly TV page in the Silk browser
    Samsung TV                    AirPlay from an iPhone (tested), or the TV
                                    page in the Internet app
    onn / Google TV               Cast from a phone or PC
    Apple TV                      AirPlay from an iPhone or iPad
    IPTV apps (TiviMate, UHF,     Playlist /tv.m3u plus a full guide grid
      iPlayTV, Kodi)              /guide.xml (XMLTV)
    VLC app on any device         /playlist.m3u (original stream)

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
      * Conversion is CPU work: budget roughly one modern CPU core per
        channel being watched at 1080p (less at 720p).

  Behind the scenes, ffmpeg on the PC converts the Tablo's raw broadcast to
  H.264 (720p for phones, 1080p for TVs), so every device plays it smoothly.
  Several devices on the same channel share one tuner.

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
  tablo.ini          your Tablo login, favorites and settings  (keep private)
  tablo-cache.json   saved channel list for offline use
  tablo-vlc.log      activity log for troubleshooting
  tablo-hls.min.js   player library for phone/TV browsers (downloaded once)

  If you publish this on GitHub, add a .gitignore with:
      tablo.ini
      tablo-cache.json
      *.log*
  so nobody publishes their password or home network address.


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

Also tested in simulation (a mock Tablo, plus a real VLC where noted):
  Linux desktop
  * Menu, columns, colors, favorites; --play, --info, --list, --tune
  * Request signing against the Tablo's local tuning endpoint
  * Same-window channel switching in a real VLC
  * Buffer and deinterlacing options passed to VLC
  * Finding regular, Snap and Flatpak VLC; ffplay and mpv fallbacks;
    --player / --ffplay; playback with no player installed
  * Offline cache, --offline (zero cloud requests), auto-reconnect, retries
  * Tuner release: correctly signed "end session" calls
  Bonus platforms
  * Windows code paths (checked on Linux)
  * Phone/TV conversion: 1080i MPEG-2 in, 720p/1080p H.264 + AAC out
  * Phone page and TV page (with remote keys) in phone- and TV-size browsers
  * TV playlist and a valid XMLTV guide
  * Headless server mode (--lan --no-vlc, no terminal or player attached)

Not tested on real hardware (built and simulation-tested only):
  * Windows 11
  * Android phones, Fire TV, Google TV/onn and Apple TV
  * The TV page in a Samsung TV's own Internet browser (AirPlay to Samsung
    is tested)
  * Cast to Google TV/onn/Chromecast, and AirPlay to Apple TV
  * IPTV apps (TiviMate, UHF, iPlayTV, Kodi) with the playlist and guide
  * Whether the Tablo honors the tuner-release call, and allows local tuning
    with no internet at all
  * Running inside an actual container or VM (and the CPU-per-channel
    estimate above)


-------------------------------------------------------------------------------
Shared as-is, with no support or warranty: this is what worked for me, not a
maintained project. Tablo can change its service at any time, which may stop
this from working.
Unofficial; not affiliated with Tablo or Nuvyyo. Uses your own Tablo account.
Builds on the open-source tablo-api and tablo2plex projects.
Vibe coded with Claude (Anthropic) at 3am for about $10 in usage credits.
-------------------------------------------------------------------------------
