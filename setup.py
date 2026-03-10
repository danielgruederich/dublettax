from setuptools import setup

APP = [{"script": "menubar.py", "plist": {"LSUIElement": True, "CFBundleName": "DublettaX", "CFBundleIdentifier": "com.monschi.dublettax"}}]

OPTIONS = {
    "argv_emulation": False,
    "packages": ["rumps", "mutagen", "watchdog"],
    "includes": ["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox"],
    "resources": ["gui.py", "deduplicator.py"],
    "iconfile": "DublettaX.icns",
}

setup(
    name="DublettaX",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
