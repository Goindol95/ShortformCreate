@echo off
chcp 65001 > nul
set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)
start "" "%PYTHON_EXE%" "%~dp0main.py"
