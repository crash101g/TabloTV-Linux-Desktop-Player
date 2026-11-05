# tablo-vlc as an always-on server for phones, tablets, TVs and IPTV apps.
# Build from the project folder (the one with tablo-vlc.py):
#   docker build -f extras/Dockerfile -t tablo-vlc .
# See extras/README.md for first-time setup and running it.
FROM python:3.12-slim

# ffmpeg converts the Tablo's broadcast for phones/TVs (and finds a GPU if one is passed in)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 tablo \
 && mkdir /data && chown tablo /data

COPY tablo-vlc.py /app/tablo-vlc.py

# Settings, saved channel list and log live in /data (mount a folder there)
ENV TABLO_VLC_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
USER tablo
EXPOSE 8000

ENTRYPOINT ["python3", "/app/tablo-vlc.py"]
CMD ["--lan", "--no-vlc"]
