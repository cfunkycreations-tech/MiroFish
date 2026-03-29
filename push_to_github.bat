@echo off
echo =======================================
echo  MiroFish - Push to GitHub
echo =======================================
cd /d "C:\Users\PHUNK\Documents\MiroFish"

echo Removing git lock if exists...
if exist ".git\index.lock" del /f ".git\index.lock"

echo Staging all changes...
git add .

echo Committing...
git commit -m "Sync all MiroFish changes - push to Mac"

echo Pushing to GitHub...
git push origin main

echo.
echo =======================================
echo  Done! You can now pull on your Mac:
echo  git pull origin main
echo =======================================
pause
