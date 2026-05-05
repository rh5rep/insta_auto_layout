@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PYTHON_BIN="
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" set "PYTHON_BIN=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not defined PYTHON_BIN if exist "%SCRIPT_DIR%venv\Scripts\python.exe" set "PYTHON_BIN=%SCRIPT_DIR%venv\Scripts\python.exe"
if not defined PYTHON_BIN for /f "delims=" %%I in ('where py 2^>nul') do (
  set "PYTHON_BIN=py"
  goto :python_found
)
if not defined PYTHON_BIN for /f "delims=" %%I in ('where python 2^>nul') do (
  set "PYTHON_BIN=python"
  goto :python_found
)

echo Could not find a Python interpreter.
echo Install Python or create the project's virtual environment, then try again.
pause
exit /b 1

:python_found
echo Launching Insta Autolayout from:
echo   %SCRIPT_DIR%
echo Using Python:
echo   %PYTHON_BIN%
echo.

if /i "%PYTHON_BIN%"=="py" (
  if "%~1"=="" (
    py -3 -m insta_autolayout --app
  ) else (
    py -3 -m insta_autolayout %*
  )
) else (
  if "%~1"=="" (
    "%PYTHON_BIN%" -m insta_autolayout --app
  ) else (
    "%PYTHON_BIN%" -m insta_autolayout %*
  )
)

set "STATUS=%ERRORLEVEL%"
if not "%STATUS%"=="0" (
  echo.
  echo Insta Autolayout exited with status %STATUS%.
  pause
)
exit /b %STATUS%
