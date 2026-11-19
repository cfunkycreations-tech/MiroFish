@echo off
rem 轻量安装（不装 torch，约 400 MB）：双击即可，等价于 Setup.bat --slim
cd /d "%~dp0"
call "%~dp0Setup.bat" --slim %*
