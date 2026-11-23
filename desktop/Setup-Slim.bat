@echo off
rem Slim install (no torch, about 400 MB): just double-click, same as Setup.bat --slim
rem The scripts are pure ASCII, so UTF-8 is only needed for the messages uv prints.
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call "%~dp0Setup.bat" --slim %*
