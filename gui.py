from __future__ import annotations

import sys
from pathlib import Path


try:
    from wechat_meme_export.gui import main
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from wechat_meme_export.gui import main


if __name__ == "__main__":
    main()
