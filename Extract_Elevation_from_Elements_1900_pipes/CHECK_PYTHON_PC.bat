@echo off
setlocal EnableExtensions
set "LOG=%TEMP%\ExtractElevation1900_Python_Check.txt"
call :CHECK > "%LOG%" 2>&1
cls
echo ===== PYTHON CHECK ON THIS PC =====
type "%LOG%"
echo.
echo Report saved to: "%LOG%"
echo Please share the report text if setup still fails.
pause
exit /b 0
:CHECK
echo Python diagnostic for Extract Elevation from Elements (1900 pipes)
echo ================================================================
echo Date and time: %DATE% %TIME%
echo Windows user: %USERNAME%
echo Local AppData directory: %LOCALAPPDATA%
echo.
echo [1] Locations of Windows Python launcher:
where py
if errorlevel 1 echo Windows Python launcher not on PATH.
echo.
echo [2] Installed Python versions registered with py:
py -0p
if errorlevel 1 echo py launcher could not list versions.
echo.
echo [3] Python 3 version and location:
py -3 -c "import sys,platform; print('Version:',sys.version); print('Executable:',sys.executable); print('Architecture:',platform.architecture()[0])"
if errorlevel 1 echo The py -3 command failed.
echo.
echo [4] Python programs on PATH:
where python
if errorlevel 1 echo python was not found on PATH.
echo.
echo [5] Default Python version:
python --version
if errorlevel 1 echo python --version failed.
echo.
echo [6] Python 3 pip availability:
py -3 -m pip --version
if errorlevel 1 echo pip in the default Python 3 installation is missing or failing.
echo.
echo [7] Python 3 ensurepip availability (read-only check):
py -3 -m ensurepip --version
if errorlevel 1 echo ensurepip could not report a version.
echo.
echo [8] Installed GIS packages in default Python 3:
py -3 -c "import importlib.util; names=['geopandas','shapely','pyogrio','pandas','numpy','pyproj']; print(' '.join(n+':'+('OK' if importlib.util.find_spec(n) else 'MISSING') for n in names))"
if errorlevel 1 echo GIS package inspection could not be completed.
echo.
echo [9] Local toolbox environment:
if exist "%LOCALAPPDATA%\SPEKTER\ExtractElevation1900\venv\Scripts\python.exe" (
  "%LOCALAPPDATA%\SPEKTER\ExtractElevation1900\venv\Scripts\python.exe" --version
  "%LOCALAPPDATA%\SPEKTER\ExtractElevation1900\venv\Scripts\python.exe" -m pip --version
) else (
  echo NOT INSTALLED in LocalAppData.
)
echo.
echo [10] Drive mapping (if available):
net use E:
