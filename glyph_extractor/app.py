"""Application bootstrap."""
from __future__ import annotations

import sys

from PyQt5.QtWidgets import QApplication

from .main_window import MainWindow


def run(argv=None) -> int:
    if argv is None:
        argv = sys.argv
    app = QApplication(argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(run())