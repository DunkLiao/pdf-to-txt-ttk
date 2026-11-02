@echo off
setlocal

pushd "%~dp0" || (
    echo Could not open the project folder.
    pause
    exit /b 1
)

call python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto use_python
call py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto use_py

echo Python 3.11 or newer was not found.
echo Install Python, then run this file again.
goto failed

:use_python
set "PY_CMD=python"
goto check_tools

:use_py
set "PY_CMD=py -3"

:check_tools
set "BUILD_PY=.venv-build\Scripts\python.exe"
if not exist "%BUILD_PY%" (
    echo Creating an isolated build environment...
    call %PY_CMD% -m venv ".venv-build"
    if errorlevel 1 goto failed
)

echo Checking build dependencies...
call "%BUILD_PY%" -m pip install -r "requirements-build.txt"
if errorlevel 1 goto failed

if not exist "assets\shortcut_icon.ico" (
    echo Missing icon: assets\shortcut_icon.ico
    goto failed
)

echo Building portable Windows application...
call "%BUILD_PY%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "PdfToTxt" ^
    --icon "%CD%\assets\shortcut_icon.ico" ^
    --add-data "%CD%\assets\shortcut_icon.ico;assets" ^
    --distpath "dist" ^
    --workpath "build" ^
    --specpath "build" ^
    "pdf_to_txt.py"
if errorlevel 1 goto failed

if not exist "dist\PdfToTxt.exe" (
    echo Build finished without the expected executable.
    goto failed
)

echo Building portable command-line application...
call "%BUILD_PY%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --console ^
    --name "PdfToTxtCli" ^
    --distpath "dist" ^
    --workpath "build" ^
    --specpath "build" ^
    "pdf_to_txt_cli.py"
if errorlevel 1 goto failed

if not exist "dist\PdfToTxtCli.exe" (
    echo Build finished without the expected command-line executable.
    goto failed
)

echo.
echo Build complete:
echo   GUI: %CD%\dist\PdfToTxt.exe
echo   CLI: %CD%\dist\PdfToTxtCli.exe
popd
exit /b 0

:failed
echo.
echo Build failed. Review the messages above.
pause
popd
exit /b 1
