# CUDA libraries require a reliable one-directory distribution.
a = Analysis(['launcher.py'], pathex=[],
    binaries=[], datas=[('README.md', '.')],
    hiddenimports=['pynvml', 'torch', 'numpy'], hookspath=[], hooksconfig={},
    excludes=['matplotlib', 'IPython', 'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'tensorflow', 'tensorboard'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GPU-Link', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='GPU-Link')
