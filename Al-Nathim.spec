# -*- mode: python ; coding: utf-8 -*-

import os

a = Analysis(
    ['app.py'],
    pathex=['c:\\Users\\WARER\\Desktop\\projects\\internet'],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('al-nazim-icon.svg', '.'),
        ('_license_core.py', '.'),
        ('pyarmor_runtime_000000\\__init__.py', 'pyarmor_runtime_000000'),
        ('pyarmor_runtime_000000\\pyarmor_runtime.pyd', 'pyarmor_runtime_000000'),
    ],
    hiddenimports=['pyarmor_runtime_000000'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Al-Nathim',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)