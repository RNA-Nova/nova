import faulthandler
import sys

faulthandler.dump_traceback_later(25, exit=True)

import pytest

sys.exit(
    pytest.main(
        [
            "tests/test_http_request.py",
            "tests/test_sandbox_params.py",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
        ]
    )
)
