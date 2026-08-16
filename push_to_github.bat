@echo off
REM ---------------------------------------------------------------
REM  Core Photo Tool - push this folder to GitHub.
REM  Edit REPO_URL below, save, then double-click this file.
REM
REM  Unlike dragging files into the browser, git DOES upload the
REM  .github folder, which is what makes the automatic Windows build
REM  work. Browsers silently skip dot-folders.
REM ---------------------------------------------------------------
set REPO_URL=https://github.com/Almarant/core-photo-tool.git

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
git remote remove origin 2>nul
git remote add origin %REPO_URL%

REM Build on top of whatever is already in the repo, so nothing is lost.
git fetch origin main 2>nul
if not errorlevel 1 (
  echo Found existing commits on the remote - building on top of them.
  git reset --soft FETCH_HEAD
)

git add -A
git commit -m "Core Photo Tool" || echo (nothing new to commit)
git push -u origin main
if errorlevel 1 (
  echo.
  echo Push failed. If it complains about diverged history, run:
  echo    git push -u origin main --force
  pause
  exit /b 1
)

git tag -f v1.0.0
git push -f origin v1.0.0

echo.
echo Done. Open the Actions tab of your repo - the Windows build takes ~4 minutes,
echo then the exe appears on the Releases page.
pause
