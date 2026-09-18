@echo off
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 run.pyw & exit /b)
where pythonw >nul 2>nul && (start "" pythonw run.pyw & exit /b)
where py >nul 2>nul && (py -3 run.py & exit /b)
where python >nul 2>nul && (python run.py & exit /b)
echo Python 3 was not found. Install it from https://www.python.org/downloads/ (tick "Add Python to PATH"), then run this again.
start https://www.python.org/downloads/
pause
