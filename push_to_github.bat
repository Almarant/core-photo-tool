@echo off
REM ---------------------------------------------------------------
REM  Core Photo Tool - one-click push to GitHub
REM  Edit the line below to your repository URL, then double-click.
REM ---------------------------------------------------------------
set REPO_URL=https://github.com/USERNAME/core-photo-tool.git

where git >nul 2>nul
if errorlevel 1 (
  echo Git is not installed. Get it from https://git-scm.com/download/win
  echo Then run this file again.
  pause
  exit /b 1
)

cd /d "%~dp0"
if not exist .git (
  git init
  git branch -M main
)
git add .
git commit -m "Core Photo Tool" || echo (nothing new to commit)
git remote remove origin 2>nul
git remote add origin %REPO_URL%
git push -u origin main
git tag -f v1.0.0
git push -f origin v1.0.0

echo.
echo Done. Open the Actions tab of your repo - the Windows build takes about 4 minutes.
pause
