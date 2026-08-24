#!/bin/bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
fetch() {
  slug="$1"; url="$2"
  curl -s -m 90 "$url" -A "$UA" -o "body_$slug.html"
  sz=$(wc -c < "body_$slug.html")
  { echo "<!-- URL: $url -->"; echo "<!-- Fetched: $(date -u '+%Y-%m-%d %H:%M UTC') -->"; cat "body_$slug.html"; } > "$slug.html"
  rm "body_$slug.html"
  echo "$slug $sz"
  sleep 2
}
fetch esystems "https://h1bdata.info/index.php?em=ESYSTEMS&year=All+Years"
fetch denovo_systems "https://h1bdata.info/index.php?em=DENOVO+SYSTEMS&year=All+Years"
fetch s3cloud_variant "https://h1bdata.info/index.php?em=S3CLOUD&year=All+Years"
fetch collabor_variant "https://h1bdata.info/index.php?em=COLLABOR&year=All+Years"
fetch district_consultants_variant "https://h1bdata.info/index.php?em=DISTRICT+CONSULTANTS&year=All+Years"
fetch v3_gate_variant "https://h1bdata.info/index.php?em=V3+GATE&year=All+Years"
fetch changing_tech_variant "https://h1bdata.info/index.php?em=CHANGING+TECH&year=All+Years"
fetch supre_variant "https://h1bdata.info/index.php?em=SUPRE&year=All+Years"
