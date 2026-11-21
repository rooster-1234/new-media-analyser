import sys
from pathlib import Path

# Ensure root workspace directory is in sys.path for test discovery and direct execution
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
