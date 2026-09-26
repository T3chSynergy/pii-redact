"""Einstiegspunkt der Kommandozeile für PyInstaller (pii-redact-cli.exe)."""
import sys

from pii_redact.cli import main

if __name__ == "__main__":
    sys.exit(main())
