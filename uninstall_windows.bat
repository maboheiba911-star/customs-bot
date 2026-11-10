@echo off
call "%~dp0stop_bot.bat" nopause
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\CustomsBot.vbs" 2>nul
echo Autostart removed. The bot will not start with Windows any more.
echo You can now delete this folder if you want.
pause
