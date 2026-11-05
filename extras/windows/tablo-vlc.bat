@echo off
rem Double-click to open the Tablo channel menu. Keep this file next to tablo-vlc.py
rem (copy it there from the extras\windows folder). Make a desktop shortcut to it if you like.
cd /d "%~dp0"
py tablo-vlc.py %*
if errorlevel 1 pause
