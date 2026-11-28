"""Run a .zby source file via `python -m zby`."""
from .interpreter import main

if __name__ == "__main__":
    raise SystemExit(main())
