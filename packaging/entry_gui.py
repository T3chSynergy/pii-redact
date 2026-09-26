"""Einstiegspunkt der Oberfläche für PyInstaller (pii-redact.exe)."""
import sys

from pii_redact.app import main

if __name__ == "__main__":
    sys.exit(main())
