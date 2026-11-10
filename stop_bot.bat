@echo off
powershell -NoProfile -Command "$p = Get-NetTCPConnection -LocalPort 47391 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($i in $p) { try { Stop-Process -Id $i -Force -ErrorAction Stop } catch { Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait -ArgumentList ('-NoProfile -Command Stop-Process -Id ' + $i + ' -Force') } }"
echo Bot stopped.
if not "%~1"=="nopause" pause
