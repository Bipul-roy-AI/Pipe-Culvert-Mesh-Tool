@echo off
setlocal EnableExtensions
set "TOOL_DIR=%~dp0"
set "PY_BASE=C:\Python310\python.exe"
set "VENV_DIR=%LOCALAPPDATA%\SPEKTER\ExtractElevation1900\venv"
set "CALC_PY=%VENV_DIR%\Scripts\python.exe"

echo.
echo Extract Elevation from Elements (1900 pipes)
echo Python setup for this Windows PC
echo ============================================
echo.
echo Toolbox can remain on E: network drive.
echo Calculation libraries will be installed locally in:
echo "%VENV_DIR%"
echo.
if not defined LOCALAPPDATA (
  echo ERROR: LOCALAPPDATA is unavailable.
  pause
  exit /b 1
)

if not exist "%PY_BASE%" (
  echo ERROR: Python 3.10 was detected during diagnostics, but is now missing:
  echo "%PY_BASE%"
  echo Please do not install another Python yet; share this message.
  pause
  exit /b 1
)

echo [1/4] Checking Python 3.10 64-bit...
"%PY_BASE%" -c "import sys; assert sys.version_info[:2]==(3,10) and sys.maxsize>2**32; print('Python:',sys.version);print('Location:',sys.executable)"
if errorlevel 1 (
  echo ERROR: C:\Python310\python.exe is not a working 64-bit Python 3.10.
  pause
  exit /b 1
)

echo.
echo [2/4] Creating or reusing a local Python environment...
if exist "%CALC_PY%" (
  echo Existing environment detected. Verifying pip...
  "%CALC_PY%" -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo ERROR: The local environment is incomplete.
    echo Rename or delete "%VENV_DIR%" and rerun this setup.
    pause
    exit /b 1
  )
) else (
  "%PY_BASE%" -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo ERROR: Local environment creation failed.
    echo This is NOT proof Python 3 is missing. Share this output.
    pause
    exit /b 1
  )
)
if not exist "%CALC_PY%" (
  echo ERROR: Environment Python executable is missing.
  pause
  exit /b 1
)

echo.
echo [3/4] Installing GeoPandas, Shapely and other GIS libraries...
"%CALC_PY%" -m pip install --upgrade pip
if errorlevel 1 (
  echo ERROR: Could not upgrade pip. Check internet/proxy access.
  pause
  exit /b 1
)
"%CALC_PY%" -m pip install -r "%TOOL_DIR%requirements.txt"
if errorlevel 1 (
  echo ERROR: Could not install a GIS library. Check error output, internet or firewall.
  pause
  exit /b 1
)

echo.
echo [4/4] Verifying calculation libraries...
"%CALC_PY%" -c "import sys, geopandas, shapely, pyogrio, pandas, numpy, pyproj; print('SUCCESS: Python', sys.version.split()[0]); print('GeoPandas:',geopandas.__version__); print('Shapely:',shapely.__version__)"
if errorlevel 1 (
  echo ERROR: GIS library verification failed. Share the error output.
  pause
  exit /b 1
)

echo.
echo SUCCESS. Open ArcMap and use the toolbox.
echo Leave Calculation setup (automatic) BLANK.
echo Local environment: "%CALC_PY%"
echo.
pause
endlocal
