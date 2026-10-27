# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller MinecraftHost.spec  (Python 3.11+, from this folder) -> dist/MinecraftHost/
# onedir without UPX: starts faster, avoids self-extraction to %TEMP% (a common antivirus
# false-positive trigger) and lets the self-update helper outlive the app.

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('index.html', '.'), ('styles.css', '.'), ('app.js', '.'), ('scene-cherry.webp', '.'), ('scene-aurora.webp', '.'), ('scene-crystal.webp', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MinecraftHost',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MinecraftHost')
