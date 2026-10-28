"""Compatibility entry point; prefer the installed console command."""
import os
from pathlib import Path
legacy = Path(__file__).with_name('config.json')
if legacy.is_file():
    os.environ.setdefault('USIMMICS_BRIDGE_CONFIG', str(legacy))
from usimmics_bridge.server import main
if __name__ == '__main__':
    raise SystemExit(main())
