@echo off
chcp 65001 >nul 2>nul

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python not found. Please install Python first.
    pause
    exit /b
)

pip install PyMuPDF requests -q 2>nul
python "%~dp0pdf_renamer.py"
if %errorlevel% neq 0 (
    echo.
    echo Error occurred. Press any key to exit.
    pause
)
