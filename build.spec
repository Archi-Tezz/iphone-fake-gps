# PyInstaller build for the distributable Windows app.
#
# Build: .venv\Scripts\python.exe -m PyInstaller build.spec --noconfirm
#
# onedir, not onefile: onefile unpacks ~200 MB to a temp folder on every launch,
# which costs seconds of startup and is what most often trips antivirus
# heuristics. A folder in a zip starts instantly and looks like what it is.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

datas = [
    # The web panel: HTML/CSS/JS, the vendored Leaflet and the icon set.
    ("iosloc/static", "iosloc/static"),
]

# pymobiledevice3 ships resources it reads at runtime (device tables, certs).
datas += collect_data_files("pymobiledevice3")
# The Developer Disk Image index, needed to mount the DDI on the device.
datas += collect_data_files("developer_disk_image")

# Several packages look their own version up through importlib.metadata at
# import time, which needs the dist-info to survive the freeze. pyimg4 is read
# while mounting the Developer Disk Image, so missing it breaks the connection
# right after the tunnel comes up.
for _dist in ("pymobiledevice3", "pyimg4", "developer_disk_image", "ipsw_parser"):
    datas += copy_metadata(_dist)

# pytun_pmd3 loads wintun.dll by path with ctypes at import time. The analyser
# cannot see a ctypes load, so without this the iOS 17+ tunnel dies at
# `import pytun_pmd3` with "Failed to load dynlib/dll ... wintun.dll".
datas += collect_data_files("pytun_pmd3")

hiddenimports = [
    # Reached through local imports inside functions, so they are easy for the
    # analyser to miss if any of those ever become dynamic.
    "pymobiledevice3.lockdown",
    "pymobiledevice3.usbmux",
    "pymobiledevice3.services.amfi",
    "pymobiledevice3.services.mobile_image_mounter",
    "pymobiledevice3.services.simulate_location",
    "pymobiledevice3.services.cryptexd",
    "pymobiledevice3.services.dvt.instruments.dvt_provider",
    "pymobiledevice3.services.dvt.instruments.location_simulation",
    "pymobiledevice3.remote.userspace_tunnel",
    "pymobiledevice3.remote.tunnel_service",
    "pymobiledevice3.remote.remote_service_discovery",
    "pymobiledevice3.tunneld.api",
]
# uvicorn picks its event loop and HTTP protocol at runtime by import string.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["pmd_pytcp", "pmd_net_addr", "pmd_net_proto", "wsproto", "h11"]
# pystray picks its platform backend by import string at runtime.
hiddenimports += ["pystray", "pystray._win32", "PIL.Image", "PIL.ImageDraw"]
# qrcode picks its image backend at runtime.
hiddenimports += ["qrcode", "qrcode.image.pil", "qrcode.image.pure"]

excludes = [
    # Nothing in the location path touches these, and together they are well
    # over half the frozen size.
    "tkinter",
    "matplotlib",
    "IPython",
    "xonsh",
    "pytest",
    "av",
    "PIL.ImageQt",
    "pykdebugparser",
    "pyiosbackup",
    "asgiwebdav",
    "notebook",
    "jedi",
    "parso",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ios-loc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # the console carries the panel URL and every setup error
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="iosloc/static/brand/ios-loc.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ios-loc",
)
