@echo off
chcp 65001 >nul 2>nul

echo [1/2] Installing dependencies...
pip install pyinstaller PyMuPDF requests -q

echo [2/2] Building exe... (1-2 min)
pyinstaller --onefile --windowed --name PDF_Renamer pdf_renamer.py

echo.
echo Done! dist\PDF_Renamer.exe has been created.
echo Copy it to your Desktop.
pause
