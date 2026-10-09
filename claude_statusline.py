"""Entry point for Claude Code's statusLine setting; works from any working directory."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from usage_monitor.statusline import main


if __name__ == "__main__":
    raise SystemExit(main())
