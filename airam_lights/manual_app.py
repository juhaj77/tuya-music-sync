"""Entry point for the standalone manual (no-music) light control app."""
from __future__ import annotations

import sys


def main() -> None:
    from PySide6.QtWidgets import QApplication

    from .keep_awake import allow_sleep, prevent_sleep
    from .ui import main_thread_gc
    from .ui.theme import apply_theme
    from .ui.manual_main_window import ManualMainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Airam Manual Lights")
    main_thread_gc.install(app)  # collect cycles (and the Qt objects in them) on this thread only
    apply_theme(app)
    prevent_sleep()
    window = ManualMainWindow()
    window.show()
    try:
        exit_code = app.exec()
    finally:
        allow_sleep()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
