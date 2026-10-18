# Johnbrick data publisher

This directory contains a deterministic public-data publisher for
[Johnbrick123/sp500-data](https://github.com/Johnbrick123/sp500-data).
GitHub Actions checks the source release and, when asset hashes change,
downloads and verifies every asset, checks price keys, and publishes bounded
tables plus a supporting-file archive for a separate Google Apps Script importer.

No Google credentials, private spreadsheet IDs, private folder IDs, or personal
investment records are stored here. The source data and derived tables are public.
The fixed upstream release ID is not used as a freshness indicator.

`latest.json` is written only after the immutable snapshot outputs are uploaded.
Unchanged source releases skip data processing. A failed build does not advance
the current snapshot pointer. GitHub scheduling may be delayed; original source
dates and successful import times must be checked by data consumers.

Local validation: `python publish.py --local` (portable DuckDB CLI is fetched
from its official release and checked against its published SHA256).
