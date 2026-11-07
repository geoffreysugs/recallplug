@echo off
rem RecallPlug launcher. Any options are passed on, e.g.:  run.bat --preview 15
setlocal
cd /d "%~dp0"

rem "python --version" also catches the Microsoft Store placeholder, which fails
python --version >nul 2>nul
if errorlevel 1 goto no_python

where ollama >nul 2>nul
if errorlevel 1 goto no_ollama

python -m app.main %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto failed
exit /b 0

:failed
echo.
echo RecallPlug stopped with an error, exit code %RC%.
pause
exit /b %RC%

:no_python
echo Python was not found on this computer.
echo RecallPlug needs Python 3.12. The download page will open now:
echo   https://www.python.org/downloads/
echo While installing, tick "Add python.exe to PATH", then start run.bat again.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:no_ollama
echo Ollama was not found on this computer.
echo RecallPlug uses Ollama to run the recap model locally. The download page will open now:
echo   https://ollama.com/download
echo After installing, start the Ollama app, then start run.bat again.
start "" "https://ollama.com/download"
pause
exit /b 1
