@echo off
rem Slim install (no torch, about 400 MB): just double-click, same as Setup.bat --slim
cd /d "%~dp0"
call "%~dp0Setup.bat" --slim %*
