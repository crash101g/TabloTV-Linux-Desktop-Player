@echo off
rem Double-click to share Tablo TV with phones, tablets and TVs (no channel menu).
rem Keep this file next to tablo-vlc.py. Close the window to stop.
rem To start it when you sign in: press Win+R, type shell:startup, and put a
rem shortcut to this file in the folder that opens.
cd /d "%~dp0"
py tablo-vlc.py --lan --no-vlc %*
pause
