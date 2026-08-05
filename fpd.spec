# PyInstaller spec — build a single-file fpd.exe
# usage:  pip install pyinstaller && pyinstaller fpd.spec
import os

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

a = Analysis(
    ["LETHE/__main__.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[
        ("LETHE/data/sites.json", "LETHE/data"),
        # tldextract needs its offline public-suffix snapshot inside the bundle
        *collect_data_files("tldextract"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="fpd",
    console=True,
    upx=False,
    icon=None,
)
