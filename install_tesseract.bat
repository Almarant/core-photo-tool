@echo off
REM ---------------------------------------------------------------
REM  Installs Tesseract OCR, which the app uses to PRE-FILL depths.
REM
REM  Optional. Without it the app works exactly the same, the depth
REM  boxes just start empty and you type them.
REM
REM  Only needed when running from source (python run_app.py).
REM  The built CorePhotoTool.exe already has Tesseract inside it.
REM ---------------------------------------------------------------
echo Installing Tesseract OCR...
winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo.
  echo winget failed. Download the installer manually instead:
  echo    https://github.com/UB-Mannheim/tesseract/wiki
  echo During setup, tick "Add to PATH".
  pause
  exit /b 1
)
echo.
echo Installed. Close this window, reopen your terminal, and start the app again.
echo The OCR checkbox on step 1 should now be enabled.
echo.
echo If it still says "not found", Tesseract is installed but not on PATH. Add
echo    C:\Program Files\Tesseract-OCR
echo to your PATH, or reinstall with the "Add to PATH" option ticked.
pause
