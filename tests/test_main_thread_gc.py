"""Garbage collection runs on the GUI thread only (ui/main_thread_gc.py):
automatic collection is off, so a lamp worker thread can never end up
destroying Qt objects in the middle of a send."""
import gc
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from airam_lights.ui import main_thread_gc  # noqa: E402


class _Cycle:
    def __init__(self):
        self.me = self

    def __del__(self):
        _finalized.append(threading.current_thread().name)


_finalized = []


def test_cycles_are_collected_on_the_gui_thread_only():
    app = QApplication.instance() or QApplication([])
    was_enabled = gc.isenabled()
    try:
        collector = main_thread_gc.install(app)
        assert not gc.isenabled()

        def make_garbage():  # a worker thread allocating a lot, like a lamp send
            for _ in range(50):
                _Cycle()
            for _ in range(100_000):
                [object()]

        _finalized.clear()
        worker = threading.Thread(target=make_garbage, name="lamp-worker-test")
        worker.start()
        worker.join()
        assert _finalized == []  # nothing collected there
        collector.collect()
        gc.collect()  # (a full one too, whatever tick the timer is on)
        assert _finalized and set(_finalized) == {threading.main_thread().name}
    finally:
        if was_enabled:
            gc.enable()
