@echo off
REM ---------------------------------------------------------------
REM ComfyUI-LLM-Hub installer
REM ASCII ONLY - do not add non-ASCII characters to this file.
REM (Windows cmd uses a legacy codepage and would garble UTF-8.)
REM ---------------------------------------------------------------
setlocal enabledelayedexpansion

set "PACK_DIR=%~dp0"
set "PYEXE="

echo.
echo === ComfyUI-LLM-Hub installer ===
echo Pack directory: %PACK_DIR%
echo.

REM 1) ComfyUI portable layout:
REM    ComfyUI_windows_portable\python_embeded\python.exe
REM    ComfyUI_windows_portable\ComfyUI\custom_nodes\ComfyUI-LLM-Hub
if exist "%PACK_DIR%..\..\..\python_embeded\python.exe" (
    set "PYEXE=%PACK_DIR%..\..\..\python_embeded\python.exe"
    goto :found
)

REM 2) Some layouts keep python_embeded one level higher
if exist "%PACK_DIR%..\..\..\..\python_embeded\python.exe" (
    set "PYEXE=%PACK_DIR%..\..\..\..\python_embeded\python.exe"
    goto :found
)

REM 3) Virtualenv next to ComfyUI
if exist "%PACK_DIR%..\..\venv\Scripts\python.exe" (
    set "PYEXE=%PACK_DIR%..\..\venv\Scripts\python.exe"
    goto :found
)

REM 4) Fall back to python on PATH
where python >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PYEXE=python"
    echo WARNING: Using 'python' from PATH.
    echo          If ComfyUI uses its own embedded Python, install into that one instead.
    goto :found
)

echo ERROR: Could not find a Python interpreter.
echo Run this manually with the Python that ComfyUI uses:
echo     ^<python^> -m pip install -r "%PACK_DIR%requirements.txt"
exit /b 1

:found
echo Using Python: %PYEXE%
echo.
"%PYEXE%" -m pip install -r "%PACK_DIR%requirements.txt"
if not %ERRORLEVEL%==0 (
    echo.
    echo ERROR: pip install failed. See the message above.
    exit /b %ERRORLEVEL%
)

if not exist "%PACK_DIR%config.json" (
    copy /y "%PACK_DIR%config.example.json" "%PACK_DIR%config.json" >nul
    echo Created config.json from config.example.json
)

echo.
echo === Install complete ===
echo.
echo Next steps:
echo   1. Restart ComfyUI.
echo   2. Add the "LLM Hub Generate" node (category: LLM Hub).
echo   3. Make sure the backend you want is ready:
echo        lmstudio - LM Studio server running on 127.0.0.1:1234
echo        claude   - run 'claude' once in a terminal and log in
echo        codex    - run 'codex login'
echo        gemini   - run 'gemini' once and sign in with your Google account
echo   4. Video input on claude/codex/lmstudio needs ffmpeg on PATH.
echo      Download: https://ffmpeg.org/download.html
echo.
endlocal
