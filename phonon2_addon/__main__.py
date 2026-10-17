"""Entry point: `python -m phonon2_addon`, or the `phonon2-addon` script."""

import sys

from phonon2_addon.server import main

if __name__ == '__main__':
    sys.exit(main())
