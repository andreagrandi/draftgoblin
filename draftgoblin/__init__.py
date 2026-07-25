"""Package metadata for Draftgoblin.
Keep shared version and disclaimer text in one place.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version(distribution_name="draftgoblin")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

DISCLAIMER = (
    "Draftgoblin is unofficial Fan Content permitted under the Fan Content Policy. "
    "Not approved/endorsed by Wizards. Portions of the materials used are property "
    "of Wizards of the Coast. ©Wizards of the Coast LLC. Card data from 17Lands "
    "(17lands.com); 17Lands does not endorse this tool."
)
