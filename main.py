"""Entry point for the Glyph Extractor application."""
import sys

from PyQt5.QtWidgets import QApplication

from glyph_extractor.app import run


if __name__ == "__main__":
    sys.exit(run(sys.argv))
