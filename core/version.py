"""Single source of truth for the app's version string.

Bumped manually before each release and read by both the GUI (for the
update checker) and build/installer.iss (via CI scripts if needed).
"""

__version__ = "1.1.0"
