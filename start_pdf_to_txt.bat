@echo off
setlocal

pushd "%~dp0" || (
    echo Could not open the application folder.
    pause
    exit /b 1
)

call py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto launch_py

call python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto launch_python

echo Python 3.11 or newer was not found.
echo Install Python, then run this file again.
pause
popd
exit /b 1

:launch_py
call py -3 "pdf_to_txt.py"
goto finish

:launch_python
call python "pdf_to_txt.py"

:finish
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo The application could not start. Check the error above.
    echo To install dependencies, run: python -m pip install -r requirements.txt
    pause
)

popd
exit /b %EXIT_CODE%
