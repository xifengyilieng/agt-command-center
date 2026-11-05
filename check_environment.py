"""Report local installation prerequisites without exposing session content."""
import json
import os
from pathlib import Path
import sys


def inspect_environment():
    home = Path.home()
    codex = Path(os.environ.get('CODEX_HOME') or home / '.codex').expanduser()
    dsh = Path(os.environ.get('DSH_HOME') or home / '.dsh').expanduser()
    return {
        'supported': os.name == 'nt' and sys.version_info >= (3, 10),
        'python': sys.version.split()[0],
        'platform': sys.platform,
        'codex': {'directoryExists': codex.is_dir(), 'indexFound': any(codex.glob('state_*.sqlite'))},
        'dsh': {'directoryExists': dsh.is_dir(), 'projectionFound': (dsh / 'storages/session_projcache/sessions').is_dir()},
        'note': 'Missing sources are allowed. Start Codex or DSH and create a task to populate the dashboard.',
    }


if __name__ == '__main__':
    result = inspect_environment()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    sys.exit(0 if result['supported'] else 1)
