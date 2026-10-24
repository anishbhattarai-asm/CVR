"""Open the CVR app straight from a checkout, without installing it.

    pip install -r requirements.txt
    python CVR.py

Once installed (pip install .), use the `cvr` command instead.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from cvr.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
