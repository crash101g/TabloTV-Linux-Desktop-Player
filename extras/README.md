# Extras: start tablo-vlc automatically

None of these are needed to use tablo-vlc. They're for running it all the time
(as a server for phones, tablets, TVs and IPTV apps) or for launching it with a
click instead of a terminal command.

| File | What it's for |
|---|---|
| `tablo-vlc.service` | Linux: start the server at boot (systemd user service) |
| `Dockerfile`, `docker-compose.yml` | Run the server in a Docker container (NAS, home server) |
| `tablo-vlc.desktop` | Linux: a "Tablo TV" entry in your app menu |

Run `./tablo-vlc.py` once by hand first, so `tablo.ini` has your Tablo login.
(Docker has its own first-run step, below.)

## Linux: start at boot (systemd)

```sh
# edit ExecStart in the file if tablo-vlc.py isn't in ~/tablo-vlc
mkdir -p ~/.config/systemd/user
cp extras/tablo-vlc.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tablo-vlc
sudo loginctl enable-linger $USER     # keep it running when you're logged out
journalctl --user -u tablo-vlc -f     # see the addresses to open on phones/TVs
```

## Docker

The container needs to be on your home network directly (host networking), so
phones and TVs can reach it and it can reach the Tablo.

```sh
cd extras
docker compose run --rm tablo-vlc --setup   # first time: enter your Tablo login
docker compose up -d                        # start it (restarts after a reboot)
docker compose logs -f                      # see the addresses to open
```

Your settings, saved channel list and log are kept in `extras/data/`. To use an
Intel/AMD graphics chip for the video conversion, uncomment the `devices` lines
in `docker-compose.yml`.

Without Compose:

```sh
docker build -f extras/Dockerfile -t tablo-vlc .
docker run -it --rm -v "$PWD/data:/data" tablo-vlc --setup
docker run -d --name tablo-vlc --network host --restart unless-stopped \
  -v "$PWD/data:/data" tablo-vlc
```

## Linux app menu

Edit the two `/home/YOU/tablo-vlc` paths in `tablo-vlc.desktop`, then copy it
to `~/.local/share/applications/`. "Tablo TV" then shows up in your app menu and
opens the channel menu in a terminal.

## Keeping settings in another folder

By default, `tablo.ini`, `tablo-cache.json` and `tablo-vlc.log` live next to
`tablo-vlc.py`. Set the `TABLO_VLC_DIR` environment variable to keep them
somewhere else (the Docker image uses `/data`).

## Testing status

- The `TABLO_VLC_DIR` setting is covered by the automated tests.
- The Docker image, systemd service and app-menu entry haven't been tried on a
  real system yet.
