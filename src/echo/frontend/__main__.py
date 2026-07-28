"""``python -m echo.frontend`` — launch the Streamlit dashboard.

Streamlit's multipage discovery is filesystem-based (it looks for a ``pages/``
directory next to the script it's given), so this just shells out to
``streamlit run`` on ``app.py`` rather than importing it as a package — mirrors
``echo.api``'s ``__main__.py`` shelling out to uvicorn.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    app_path = Path(__file__).parent / "app.py"
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app_path)])


if __name__ == "__main__":
    raise SystemExit(main())
