@echo off
rem RecallPlug demo: the same as run.bat, with "away" shortened to 10 seconds.
call "%~dp0run.bat" --demo %*
exit /b %ERRORLEVEL%
