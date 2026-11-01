# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['PIL.ImageGrab', 'PIL.ImageTk']
hiddenimports += collect_submodules('pynput')


a = Analysis(
    ['C:/Users/22576/Desktop/按键精灵-1.7.0-Windows打包/main.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/22576/Desktop/按键精灵-1.7.0-Windows打包/assets/icon.png', 'icon.png')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'psutil', 'yaml', 'PIL._avif', 'PIL._webp', 'PIL._imagingcms', 'PIL._imagingmath', 'PIL.ImageQt', 'PIL.ImageShow', 'matplotlib', 'pandas', 'scipy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='按键精灵',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:/Users/22576/Desktop/按键精灵-1.7.0-Windows打包/assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='按键精灵',
)
