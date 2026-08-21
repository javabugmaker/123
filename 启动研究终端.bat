@echo off
setlocal
cd /d "%~dp0"
python gui_v84.py
if errorlevel 1 (
  echo.
  echo 研究终端启动失败，请检查上方错误信息。
  pause
)
endlocal
