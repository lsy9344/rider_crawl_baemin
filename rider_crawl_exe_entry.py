"""PyInstaller entry point for the rider_crawl onefile build.

PyInstaller cannot use ``python -m rider_crawl`` as an entry point, so this
module is the script the ``rider_crawl_onefile.spec`` Analysis points at. It is
committed at the repo root (not under the throwaway ``build/`` directory, which
PyInstaller recreates) so a clean checkout can rebuild the exe without first
running an extra script. ``pathex=['src']`` in the spec puts ``rider_crawl`` on
the import path.
"""

from __future__ import annotations

from rider_crawl.ui import main

if __name__ == "__main__":
    main()
