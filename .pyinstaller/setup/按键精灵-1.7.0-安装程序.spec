# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/Users/22576/Desktop/按键精灵-1.7.0-Windows打包/install.py'],
    pathex=['C:/Users/22576/Desktop/按键精灵-1.7.0-Windows打包'],
    binaries=[],
    datas=[('C:/Users/22576/Desktop/按键精灵-1.7.0-Windows打包/.pyinstaller/payload/按键精灵_1101112851', 'payload')],
    hiddenimports=[],
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
    name='按键精灵-1.7.0-安装程序',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:/Users/22576/Desktop/按键精灵-1.7.0-Windows打包/assets/icon.ico'],
)
