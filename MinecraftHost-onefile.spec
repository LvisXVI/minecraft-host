# -*- mode: python ; coding: utf-8 -*-
# Build: $env:MINECRAFT_HOST_TOOLS_DIR = 'path containing syncthing.exe, gate.exe, playit-agent.exe'
#        pyinstaller MinecraftHost-onefile.spec
# Output: dist/MinecraftHost-Standalone.exe (no neighboring _internal or helper EXEs required).
import os
from pathlib import Path

tools_dir = Path(os.environ.get('MINECRAFT_HOST_TOOLS_DIR', SPECPATH))
helpers = ['syncthing.exe', 'gate.exe', 'playit-agent.exe']
missing = [name for name in helpers if not (tools_dir / name).is_file()]
if missing:
    raise ValueError(f'Missing helper binaries in {tools_dir}: {", ".join(missing)}')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[(str(tools_dir / name), '.') for name in helpers],
    datas=[('index.html', '.'), ('styles.css', '.'), ('app.js', '.'),
           ('scene-cherry.webp', '.'), ('scene-aurora.webp', '.'), ('scene-crystal.webp', '.')],
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
    pyz, a.scripts, a.binaries, a.datas,
    [],
    name='MinecraftHost-Standalone',
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
