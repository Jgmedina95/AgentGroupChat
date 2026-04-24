from __future__ import annotations

import sys
from pathlib import Path


if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))


from tui.app import main


if __name__ == "__main__":
    main()