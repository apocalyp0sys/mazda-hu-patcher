@echo off
rem ---------------------------------------------------------------------------
rem Windows wrapper for hu_patcher.py
rem
rem   hu-patcher.cmd 192.168.50.1
rem   hu-patcher.cmd --self-check
rem   hu-patcher.cmd --diagnose
rem
rem Finds Python 3.8+, offers to install it via winget or python.org,
rem prints diagnostics on failure (or with --diagnose), then runs the patcher.
rem ---------------------------------------------------------------------------
setlocal EnableExtensions
set "HERE=%~dp0"
set "SCRIPT=%HERE%hu_patcher.py"

if /i "%~1"=="--diagnose" goto :diagnose
if /i "%~1"=="-d" goto :diagnose

call :find_python
if defined PY goto :run

echo.
echo [i] Python 3.8+ was not found.
call :diagnose_body
echo.
call :install_python
call :find_python
if not defined PY (
    echo.
    echo [ERROR] Python is still not available in this window.
    echo         Close this window, open a new terminal, and retry.
    echo         Or install Python manually from https://www.python.org/downloads/windows/
    exit /b 1
)
goto :run

:run
if not exist "%SCRIPT%" (
    echo [ERROR] Missing patcher script: %SCRIPT%
    exit /b 1
)
for %%T in (ssh scp ssh-keygen) do (
    where %%T >nul 2>&1
    if errorlevel 1 echo [WARN] %%T not found; enable OpenSSH Client in Windows features.
)
set "PYVER="
for /f "delims=" %%V in ('"%PY%" -V 2^>^&1') do set "PYVER=%%V"
echo [i] %PYVER%  (%PY%)
"%PY%" "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
exit /b %RC%

rem ===========================================================================
rem Diagnostics
rem ===========================================================================
:diagnose
echo ==== HU patcher diagnostics ====
call :diagnose_body
echo.
call :find_python
if defined PY (
    echo [OK] Python: %PY%
    "%PY%" -c "import sys; print(sys.version)"
) else (
    echo [FAIL] Python 3.8+ not found.
)
echo.
for %%T in (winget ssh scp ssh-keygen) do (
    echo --- %%T
    where %%T 2>nul
    if errorlevel 1 echo     NOT FOUND
)
echo.
exit /b 0

:diagnose_body
echo [i] Windows:
ver
echo [i] Architecture: %PROCESSOR_ARCHITECTURE%
echo [i] Script: %SCRIPT%
if not exist "%SCRIPT%" echo [FAIL] Script not found: %SCRIPT%
echo.
echo [i] Python candidates:
for %%T in (python py python3) do (
    echo --- %%T
    where %%T 2>nul
    if errorlevel 1 echo     not in PATH
)
echo.
echo [i] Common install paths:
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do echo     %%~fD
for /d %%D in ("%ProgramFiles%\Python3*") do echo     %%~fD
echo.
for %%T in (winget ssh scp ssh-keygen) do (
    where %%T >nul 2>&1
    if errorlevel 1 (
        echo [WARN] %%T not found
    ) else (
        echo [OK]   %%T found
    )
)
goto :eof

rem ===========================================================================
rem Python discovery
rem ===========================================================================
:find_python
set "PY="
for /f "delims=" %%P in ('where python 2^>nul') do (
    call :try_python "%%P"
    if defined PY goto :eof
)
for /f "delims=" %%P in ('where py 2^>nul') do (
    call :try_python "%%P"
    if defined PY goto :eof
)
for /f "delims=" %%P in ('where python3 2^>nul') do (
    call :try_python "%%P"
    if defined PY goto :eof
)
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do (
    if exist "%%~fD\python.exe" call :try_python "%%~fD\python.exe"
    if defined PY goto :eof
)
for /d %%D in ("%ProgramFiles%\Python3*") do (
    if exist "%%~fD\python.exe" call :try_python "%%~fD\python.exe"
    if defined PY goto :eof
)
for /d %%D in ("C:\Python3*") do (
    if exist "%%~fD\python.exe" call :try_python "%%~fD\python.exe"
    if defined PY goto :eof
)
goto :eof

:try_python
set "CAND=%~1"
if "%CAND%"=="" goto :eof
if not exist "%CAND%" goto :eof
"%CAND%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "PY=%CAND%"
goto :eof

rem ===========================================================================
rem Python installation
rem ===========================================================================
:install_python
set "WINGET="
for /f "delims=" %%W in ('where winget.exe 2^>nul') do set "WINGET=%%W"
if not defined WINGET goto :install_from_python_org

echo [i] winget is available.
call :ask_yes "Install Python 3.12 via winget?"
if not "%ASK%"=="1" goto :install_skipped
echo [i] Installing Python 3.12 (winget) ...
"%WINGET%" install -e --id Python.Python.3.12 --source winget --accept-source-agreements --accept-package-agreements
if not errorlevel 1 goto :eof

echo [i] 3.12 failed; trying Python 3.11 ...
"%WINGET%" install -e --id Python.Python.3.11 --source winget --accept-source-agreements --accept-package-agreements
if not errorlevel 1 goto :eof

echo [WARN] winget installation failed; trying python.org installer.
goto :install_from_python_org

:install_from_python_org
echo [i] Will download Python 3.12.10 from python.org.
call :ask_yes "Download and install Python from python.org?"
if not "%ASK%"=="1" goto :install_skipped
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "$u='https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'; $o=Join-Path $env:TEMP 'python-3.12.10-amd64.exe'; Write-Host '[i] downloading ' $u; Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $o; Write-Host '[i] running installer'; $p=Start-Process -Wait -PassThru -FilePath $o -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_test=0','Include_launcher=1'; exit $p.ExitCode"
if errorlevel 1 echo [WARN] Python installer returned an error.
goto :eof

:ask_yes
set "ASK=0"
set "ANS="
set /p "ANS=%~1 [Y/n]: "
if errorlevel 1 goto :eof
set "ANS=%ANS: =%"
if "%ANS%"=="" goto :ask_yes_ok
if /i "%ANS%"=="y" goto :ask_yes_ok
if /i "%ANS%"=="yes" goto :ask_yes_ok
goto :eof
:ask_yes_ok
set "ASK=1"
goto :eof

:install_skipped
echo [i] Python installation skipped.
goto :eof
