"""Python's cyclic garbage collector, run on the GUI thread only.

Left automatic, the collector runs on whichever thread happens to allocate
past its threshold - often a lamp worker in the middle of a send. If the
cycles it frees hold Qt objects (a painter, pens, colours left over from a
paintEvent), they're destroyed on that worker thread while the GUI thread
is painting with Qt: an access violation that ends the app without a
traceback (seen in crash.log: "Garbage-collecting" on lamp-worker-OV while
the main thread was in the info panel's paintEvent).

So automatic collection is switched off and this timer collects on the GUI
thread instead: the young generation every second (a few ms at most), a
full collection every 30 s (~6 ms). Plain reference counting - which frees
almost everything - is unaffected; only cycles wait for the timer.
"""
from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer

_YOUNG_EVERY_MS = 1000
_FULL_EVERY = 30  # ticks


class MainThreadGC(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        gc.disable()
        self._ticks = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.collect)
        self._timer.start(_YOUNG_EVERY_MS)

    def collect(self) -> None:
        self._ticks += 1
        gc.collect(2 if self._ticks % _FULL_EVERY == 0 else 0)


_instance: "MainThreadGC | None" = None


def install(app) -> MainThreadGC:
    """Switches automatic collection off and collects on `app`'s (the GUI)
    thread from now on. Call once, right after creating the QApplication."""
    global _instance
    if _instance is None:
        _instance = MainThreadGC(app)
    return _instance
