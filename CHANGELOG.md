# Changelog

Run `./tablo-vlc.py --version` to see which version you have. The version is
also written at the top of `tablo-vlc.log` each time the script starts.

## 1.5.4 (2026-10-22)

- Fixed black pictures in multi-view (found with `/multi?debug` on a real PC):
  - Chrome blocks video that starts with sound until you tap the page, so the
    first picture had video loaded but sat paused. All pictures now start
    muted (and play); tap one to hear it
  - Newer Chrome claims it can play the stream itself, so the page used
    Chrome's own player, which stalled. The pages now use hls.js everywhere
    except Safari (iPhone, iPad, Mac), which needs its own player for AirPlay
  - A picture whose video is ready but paused is started automatically, or
    says "Tap the picture to start"

## 1.5.3 (2026-10-22)

- Multi-view `?debug` (open `/multi?debug`): a player log under the pictures
  with the browser, the player library, every player event and each video's
  state every 3 seconds, plus a Copy log button, for bug reports
- `--diagnose` also checks that converted segments start with the stream
  headers and a keyframe (needed for a browser joining a live stream)

## 1.5.2 (2026-10-22)

- `--diagnose 2.1 11.1`: give two or more channels to also play them at the same
  time through the script, exactly as the multi-view page does, with a
  stand-in player. Reports per channel how long the first video took, how
  much video arrived, and the longest wait for new video

## 1.5.1 (2026-10-22)

- `--diagnose 2.1`: checks one channel's whole path to a browser, step by step
  (tune, the Tablo's stream address, playlist and video, what the broadcast is,
  a 20-second test conversion, and whether the converted picture is black),
  then frees the tuner. The results also go in the log

## 1.5.0 (2026-10-22)

Phone, TV and multi-view playback is sturdier, using lessons from TabloView's
notes on the Tablo 4th Gen:
- When the Tablo stops sending for a moment (a Wi-Fi blip, "No route to host"),
  the conversion restarts in place and the playlist carries on where it left
  off. Before, it started over, which left players frozen on a black picture
- Switching channels frees the old tuner before tuning the new one, so a Tablo
  with few free tuners doesn't count both and refuse the new channel
- Damaged parts of the broadcast are dropped instead of decoded into garbage;
  audio is stretched over gaps; every 2-second segment starts on a keyframe
- Players get the stream once 2 segments are ready, for a smoother start
- A graphics-chip encoder that fails mid-stream switches to software encoding

## 1.4.3 (2026-10-22)

- Multi-view: a picture that won't start now always says why, right away,
  instead of sitting on "Tuning…" or going black: "No video from the Tablo
  yet", "The conversion stopped", or "Couldn't tune (all tuners busy?)". It
  retries 3 times on its own, and no longer holds up the other channels
- Log: plain-English notes when the PC loses contact with the Tablo ("No route
  to host"), when no video arrives after tuning, and when a conversion runs
  slower than real time (PC too slow for that many channels)

## 1.4.2 (2026-10-21)

- Tuner status now reads what each tuner is doing, using the 4th Gen's own
  report (channel and recording per tuner): "Tuners: 3 of 4 busy (1 by this
  script: 9.1 KNINE; 1 recording; 1 on other TVs or apps)"
- Spots stuck tuners: busy but on no channel and not recording (seen on a real
  Tablo). The menu, the multi-view bar and "couldn't tune" messages then
  suggest a Tablo restart (or `--restart-tablo` with a smart plug)

## 1.4.1 (2026-10-21)

- Multi-view: tuner status bar at the top, the same as `t` in the menu, e.g.
  "Tablo 4G QUAD · Tuners: 4 of 4 busy (0 by this script; 4 recording or on
  other TVs)". It turns red when no tuner is free and updates every 10 seconds
- Multi-view: when every tuner is busy, channels wait ("Waiting for a free
  Tablo tuner…") and start by themselves when one frees up, instead of failing
- Tuner summary wording: "2 of 4 busy (1 by this script: 9.1 KNINE; 1 recording
  or on other TVs)"
- New `/tuners.json` (tuner status for web pages)

## 1.4.0 (2026-10-21)

- Multi-view: ■ / ▶ button on each picture. ■ stops that channel and frees its
  Tablo tuner immediately (no waiting a minute); ▶ starts it again, which also
  retries a channel that didn't come up
- Multi-view: Stop all / Start all buttons, and leaving the page frees every
  tuner right away
- Multi-view: channels now start one after another instead of all at once,
  since the Tablo tunes one channel at a time; this makes 4 channels reliable
- Multi-view: a channel that gives up says why and offers ▶ to try again
- Changing one picture's channel or going from 4 to 2 keeps the other pictures
  playing

## 1.3.2 (2026-10-21)

- Fixed: in multi-view, the second channel could stay on "Tuning…" forever.
  The browser player gave up after 10 seconds, but tuning a second channel
  (the Tablo tunes one at a time) plus starting its conversion can take
  15-20 seconds. Players now wait up to 30 seconds and retry, on all pages
- Multi-view: a channel that can't start now says why ("Couldn't tune this
  channel (all Tablo tuners busy?)") and retries a few times, instead of
  staying on "Tuning…"

## 1.3.1 (2026-10-21)

- Fixed: with ffmpeg installed (the "ffmpeg relay" stream mode), the channel
  menu stopped accepting typing while a channel played in VLC, until VLC was
  closed. The relay's ffmpeg was reading the terminal; it no longer does

## 1.3.0 (2026-10-21)

- Multi-view page (`/multi`, or `m` in the menu): 2 or 4 live channels side by
  side in a browser, each with its own channel picker; tap a picture for its
  sound; full screen; picks remembered. 4-up uses 540p to keep the work down
- Second player window from the menu: `w 4.1` plays another channel in its own
  VLC (or ffplay) window next to the first; `w off` closes it
- One device can now hold several channels at once (each player keeps its own
  tuner) instead of a new channel replacing the old one
- The `--lan` startup lines include the multi-view address, and `m` in the menu
  also prints the address for other devices on your Wi-Fi (or, without
  `--lan`, how to turn that on)
- Phone page: the top bar (Multi-view, Guide as of, Refresh guide) no longer
  wraps awkwardly on narrow screens

## 1.2.0 (2026-10-21)

- Tuner status in the menu: "Tuners: 2 of 4 in use by this script (9.1 KNINE,
  4.1 KFOUR)", and `t` for details (which channel, on which device, for how
  long). The tuner count comes from the Tablo itself
- Clearer "couldn't tune" messages: says how many tuners this script holds,
  and that the rest are probably recording or in use on another TV
- Looks for a tuner-status report on the Tablo; if your Tablo has one, the
  menu also counts recordings and other TVs

## 1.1.0 (2026-10-21)

- Closed captions on phones and TVs: the broadcast's captions are kept through
  the conversion (x264, NVENC, Quick Sync, VAAPI); turn them on with the
  player's CC button on phones, or ◀ ▶ on the TV page. `captions = on` in
  tablo.ini starts with them showing
- Menu: `p` = previous channel (like a remote's Last button), and a "Recent"
  line with the last 5 channels watched (remembered between runs)
- Guide search: `/football` in the menu or `--search football` finds shows on
  now (every channel) and coming up (your favorites), with numbers to type

## 1.0.0 (2026-10-21) - first public release

Watching
- Terminal channel menu: favorites first (in color), local OTA, then FAST
  channels, in columns; what's on now, time left, and details (`i3`)
- Plays in VLC (apt, Snap or Flatpak), ffplay, mpv or any player; same-window
  channel switching in VLC; 3-second live buffer (adjustable), deinterlacing
- Web page with Play buttons; `--lan` shares with phones, tablets and TVs:
  phone page (AirPlay / Cast), remote-friendly TV page, IPTV playlist and
  XMLTV guide
- ffmpeg converts the Tablo's MPEG-2 to H.264 for browsers and TVs, on the
  graphics chip when possible (VAAPI, NVENC, Quick Sync, AMF)
- `--max-channels` to shorten the list; guide refreshes every 10 minutes,
  plus `r` in the menu, a Refresh guide button and `--refresh-guide`

Reliability
- Keeps playing local channels when the internet is down; `--offline` mode
- Re-logs in when the cloud login expires; frees tuners on switch/quit
- `--restart-tablo` power-cycles a stuck Tablo through a smart plug
  (Shelly or Tasmota; the Tablo has no restart command)

Privacy and security
- Single readable Python file, standard library only
- tokens, password and access key scrubbed from the log
- optional system keyring for the password; optional access key for `--lan`

Running it
- Linux and Windows 11; headless server mode; systemd service, Docker and a
  Linux app-menu entry in `extras/`; `TABLO_VLC_DIR` to keep settings elsewhere
- Automated tests with a fake Tablo; GitHub Actions on Linux and Windows
