"""tests/test_package.py - a smoke test that the package installs and imports.

What: imports fraudstream and checks it reports a version.
Why:  the simplest possible test proves the setup works (virtual environment,
      editable install, pytest) before any real code depends on it.
"""

import fraudstream


def test_package_imports_and_has_version():
    assert fraudstream.__version__ == "0.1.0"
