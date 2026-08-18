@echo off
setlocal
REM ---------------------------------------------------------------
REM  Core Photo Tool - push this folder to GitHub.
REM  Double-click. Edit REPO_URL below only if the repo moves.
REM
REM  Unlike dragging files into a browser, git DOES upload the
REM  .github folder - which is what makes the automatic Windows
REM  build run. Browsers silently skip dot-folders.
REM ---------------------------------------------------------------
set REPO_URL=https://github.com/Almarant/core-photo-tool.git

where git >nul 2>nul
if errorlevel 1 (
  echo Git is not installed.
  echo Get it from https://git-scm.com/download/win  then run this again.
  pause & exit /b 1
)

cd /d "%~dp0"

REM Git refuses to commit without an identity. Set one just for this repo
REM if the machine has none, so a first-time user is not stopped here.
if not exist .git (
  git init
  git branch -M main
)
for /f "delims=" %%i in ('git config user.email 2^>nul') do set HAVE_EMAIL=%%i
if "%HAVE_EMAIL%"=="" (
  echo No git identity found - setting one for this repository only.
  git config user.email "core-photo-tool@local"
  git config user.name "Core Photo Tool"
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
git commit -m "Core Photo Tool: built-in label reader, drag-and-drop sources, manual crop corners"
if errorlevel 1 echo (nothing new to commit - continuing)

git push -u origin main
if errorlevel 1 (
  echo.
  echo ---------------------------------------------------------------
  echo Push failed.
  echo  * Asked for a password? GitHub wants a Personal Access Token,
  echo    not your account password. Easiest fix: install GitHub CLI
  echo    or GitHub Desktop, sign in once, then run this again.
  echo  * Complains about diverged history? Run:
  echo        git push -u origin main --force
  echo ---------------------------------------------------------------
  pause & exit /b 1
)

git tag -f v1.0.0
git push -f origin v1.0.0

echo.
echo ===============================================================
echo Pushed.
echo  Build progress : https://github.com/Almarant/core-photo-tool/actions
echo  Download (~4m) : https://github.com/Almarant/core-photo-tool/releases
echo ===============================================================
pause
