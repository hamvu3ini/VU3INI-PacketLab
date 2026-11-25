"""Launcher used by PyInstaller and for running from source: python run_packetlab.py"""
import sys

from packetlab.main import main

if __name__ == "__main__":
    sys.exit(main())
