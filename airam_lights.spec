# PyInstaller build spec: three exes sharing one folder (and one copy of
# PySide6/numpy/scipy). Build with:  .venv\Scripts\pyinstaller airam_lights.spec
#
#   AiramMusicLights.exe   - the music visualizer (main.py)
#   AiramManualControl.exe - the standalone manual control app (manual_control.py)
#   AiramSetupWizard.exe   - console tool that fetches local_keys (tools/setup_wizard.py)

# Only what's actually used - keeps the Qt payload from ballooning.
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtMultimedia", "pytest"]


# The theme's icon files (airam_lights/ui/assets), looked up next to ui/theme.py.
datas = [("airam_lights/ui/assets", "airam_lights/ui/assets")]


def analysis(script):
    return Analysis([script], pathex=["."], excludes=excludes, datas=datas, noarchive=False)


def exe(a, name, console):
    pyz = PYZ(a.pure)
    return EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console, upx=False)


a_main = analysis("main.py")
a_manual = analysis("manual_control.py")
a_wizard = analysis("tools/setup_wizard.py")

coll = COLLECT(
    exe(a_main, "AiramMusicLights", console=False), a_main.binaries, a_main.datas,
    exe(a_manual, "AiramManualControl", console=False), a_manual.binaries, a_manual.datas,
    exe(a_wizard, "AiramSetupWizard", console=True), a_wizard.binaries, a_wizard.datas,
    upx=False,
    name="AiramMusicLights",
)
