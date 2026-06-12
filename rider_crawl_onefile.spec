# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
# psutil은 '준비하기'가 같은 프로필을 점유한 Chrome을 감지해 빈 창 폭주를 막는 데
# 쓰인다(browser_launcher._chrome_running_for_profile). lazy import라 누락돼도 죽지는
# 않지만, 빠지면 안전장치가 조용히 비활성화되므로 exe에 명시적으로 포함한다.
hiddenimports = ['playwright.async_api', 'playwright.sync_api', 'pywinauto', 'psutil']
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['rider_crawl_exe_entry.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='rider_crawl_onefile',
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
