@echo off
REM ScanGrade Dev Script for Windows
IF EXIST .env (
    echo Loading .env
) ELSE (
    echo Copy .env.example to .env first!
    exit /b 1
)

echo Starting Flask dev server...
flask run --host=0.0.0.0 --port=5000 --reload
