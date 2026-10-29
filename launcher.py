"""Frozen-app entry point (used by PyInstaller)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from pmacdoctor.app import main
    sys.exit(main())
