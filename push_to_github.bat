@echo off
setlocal enabledelayedexpansion
REM ---------------------------------------------------------------
REM  Core Photo Tool - push this folder to GitHub.
REM  Double-click. Edit REPO_URL below only if the repo moves.
REM
REM  Unlike dragging files into a browser, git DOES upload the
REM  .github folder - which is what makes the automatic Windows
REM  build run. Browsers silently skip dot-folders.
REM
REM  This script pushes the commits that are already here and tags
REM  the version in corephoto\__init__.py, so the Release that
REM  colleagues download matches the version the app reports.
REM  It does NOT rewrite history: an earlier version of this script
REM  ran "git reset --soft FETCH_HEAD" and force-moved the v1.0.0
REM  tag, which flattened every commit into one and pointed the
REM  published v1.0.0 Release at code that was not v1.0.0.
REM ---------------------------------------------------------------
set REPO_URL=https://github.com/Almarant/core-photo-tool.git

where git >nul 2>nul
if errorlevel 1 (
  echo Git is not installed.
  echo Get it from https://git-scm.com/download/win  then run this again.
  pause & exit /b 1
)

cd /d "%~dp0"

if not exist .git (
  git init
  git branch -M main
)

REM Git refuses to commit without an identity. Set one just for this repo
REM if the machine has none, so a first-time user is not stopped here.
for /f "delims=" %%i in ('git config user.email 2^>nul') do set HAVE_EMAIL=%%i
if "!HAVE_EMAIL!"=="" (
  echo No git identity found - setting one for this repository only.
  git config user.email "core-photo-tool@local"
  git config user.name "Core Photo Tool"
)

git remote remove origin 2>nul
git remote add origin %REPO_URL%

REM The version the app reports, e.g. 1.7.0
for /f "delims=" %%L in ('findstr /b "__version__" corephoto\__init__.py') do set LINE=%%L
set VER=!LINE:*"=!
set VER=!VER:"=!
if "!VER!"=="" (
  echo Could not read the version from corephoto\__init__.py
  pause & exit /b 1
)
echo Version !VER!

REM Anything edited by hand since the last commit goes in as its own commit.
git add -A
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "Core Photo Tool !VER!: local changes"
) else (
  echo Nothing new to commit.
)

git push -u origin main
if errorlevel 1 (
  echo.
  echo ---------------------------------------------------------------
  echo Push failed.
  echo  * Asked for a password? GitHub wants a Personal Access Token,
  echo    not your account password. Easiest fix: install GitHub CLI
  echo    or GitHub Desktop, sign in once, then run this again.
  echo  * Says the remote has commits you do not have? Run:
  echo        git pull --rebase origin main
  echo    then run this script again. Do NOT force-push unless you
  echo    are sure nothing on GitHub is worth keeping.
  echo ---------------------------------------------------------------
  pause & exit /b 1
)

REM Tagging is what publishes a Release. An existing tag is left alone:
REM moving a tag that is already published changes what people already
REM downloaded, so a new version needs a new version number.
git rev-parse -q --verify refs/tags/v!VER! >nul
if errorlevel 1 (
  git tag v!VER!
  git push origin v!VER!
  echo Tagged v!VER! - the Release will appear in a few minutes.
) else (
  echo Tag v!VER! already exists; nothing tagged. Bump __version__ for a new Release.
)

echo.
echo ===============================================================
echo Pushed.
echo  Build progress : https://github.com/Almarant/core-photo-tool/actions
echo  Download ^(~4m^) : https://github.com/Almarant/core-photo-tool/releases
echo ===============================================================
pause
