# Job posting evidence

Job ads cited in the briefing, preserved against takedown. For each posting:

- a full-page screenshot (`<slug>.png`) taken on the capture date
- the raw page text as fetched (`<slug>.txt`, URL and timestamp on the first lines)
- a Wayback Machine snapshot requested at capture time, so a third party
  holds a copy with its own timestamp

`manifest.csv` is the index: one row per posting with the capture date, the
original URL, the archive URL, the pay text exactly as posted, and which files
hold the copies. Nothing from a posting goes into the briefing unless it is in
this directory first.
