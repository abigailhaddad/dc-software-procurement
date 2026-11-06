"""The names that tie this project to its current owner, in one place.

Each can be overridden with an environment variable; the defaults are the
current owner's. The site's own files (``site/``) and a few documents name the
same things in plain text and cannot read this file: HANDOFF.md lists them.
"""

from __future__ import annotations

import os

# The District's portal. Not an owner setting: this is the source.
PORTAL = "https://contracts.ocp.dc.gov"

# The code.
GITHUB_REPO = os.environ.get("DCPROCURE_GITHUB_REPO", "abigailhaddad/dc-software-procurement")

# The working archive: portal responses, kept after the portal drops them.
HF_ARCHIVE_REPO = os.environ.get("DCPROCURE_HF_ARCHIVE", "abigailhaddad/dc-procurement-raw")

# The public dataset: tidy Parquet tables for people who want to use the data.
HF_DATASET_REPO = os.environ.get("DCPROCURE_HF_DATASET", "abigailhaddad/dc-procurement")

# The explorer site.
SITE_URL = os.environ.get("DCPROCURE_SITE_URL", "https://dc-procurement.abigailhaddad.com")
