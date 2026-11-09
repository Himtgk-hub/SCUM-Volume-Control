# -*- mode: python ; coding: utf-8 -*-
# Спецификация PyInstaller для SCUM Volume Control.
# Сборка: pyinstaller scum_volume.spec --noconfirm

from PyInstaller.utils.hooks import collect_submodules, collect_all

hiddenimports = [
    "comtypes.stream",
    "pycaw.pycaw",
    "pycaw.api.audiopolicy",
    "pycaw.api.mmdeviceapi",
    "pycaw.constants",
    "psutil",
]
hiddenimports += collect_submodules("comtypes.gen")
speech_datas, speech_binaries, speech_imports = collect_all("vosk")
hiddenimports += speech_imports

excludes = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtHelp",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
]

a = Analysis(
    ["src/main.py"],
    pathex=["src"],
    binaries=speech_binaries,
    datas=[("resources", "resources")] + speech_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SCUMVolumeControl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    icon="resources/icon.ico",
    version="version_info.txt",
    disable_windowed_traceback=False,
)
