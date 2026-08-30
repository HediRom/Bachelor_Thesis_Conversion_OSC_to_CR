"""
Enables ``python -m osc2cr_extended``.

The interpreter check lives in ``osc2cr_extended/__init__.py``, which runs first and
exits with a short message if this Python is too old.
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
