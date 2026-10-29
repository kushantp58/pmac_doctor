# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PMAC Doctor (one-folder build, windowed).

Build with:  python -m PyInstaller build/pmacdoctor.spec --noconfirm  (run from the project root)
"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))
sys.path.insert(0, ROOT)
import pmacdoctor  # noqa: E402

block_cipher = None

datas = [
    (os.path.join(ROOT, "pmacdoctor", "ui", "resources"), os.path.join("pmacdoctor", "ui", "resources")),
    (os.path.join(ROOT, "docs"), "docs"),
    (os.path.join(ROOT, "README.md"), "."),
]
# pyepics ships the Channel Access shared libraries (libca / libCom) as package data
datas += collect_data_files("epics")
binaries = collect_dynamic_libs("epics")

hiddenimports = (collect_submodules("pyqtgraph") + collect_submodules("epics") +
                 ["paramiko", "cryptography", "bcrypt", "nacl", "scipy.special._cdflib",
                  "scipy._lib.messagestream", "numpy"])

a = Analysis(
    [os.path.join(ROOT, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "PyQt5", "PyQt6", "PySide2",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
              "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
              "PySide6.QtPdf", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtLocation",
              "PySide6.QtPositioning", "PySide6.QtRemoteObjects", "PySide6.QtSerialPort", "PySide6.QtTest",
              "PySide6.QtTextToSpeech", "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtDesigner",
              "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtXml", "PySide6.QtSql", "PySide6.QtScxml",
              "PySide6.QtStateMachine", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer", "PySide6.QtGraphs"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PmacDoctor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ROOT, "pmacdoctor", "ui", "resources", "icon.ico"),
    version=os.path.join(ROOT, "build", "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="PmacDoctor",
)
