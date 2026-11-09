@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo === SCUM Volume Control: сборка EXE ===

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Создание виртуального окружения...
    py -3.12 -m venv .venv || goto :error
)

echo [2/3] Установка зависимостей...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller==6.10.0 || goto :error

echo [3/3] Сборка через PyInstaller...
".venv\Scripts\python.exe" -m PyInstaller scum_volume.spec --noconfirm --clean || goto :error

echo.
echo Готово: dist\SCUMVolumeControl.exe
exit /b 0

:error
echo.
echo Сборка завершилась с ошибкой.
exit /b 1
