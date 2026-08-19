"""
Entry point for the terminal app.

    python main.py            -> help
    python main.py check      -> is everything wired up?
    python main.py menu       -> interactive
"""

import sys

from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
