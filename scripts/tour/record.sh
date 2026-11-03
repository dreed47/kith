#!/usr/bin/env bash
# Record the landing page tour into site/tour.mp4 and site/tour-poster.jpg.
#
# Makes an empty Kith on the local test database (docker compose's db, database kith_tour), runs it on port 5078,
# and drives it with tour.py. Needs ffmpeg, Chromium, and a Python with Playwright:
#
#   PY=~/venvs/playwright/bin/python FLASK=.venv/bin/flask CHROMIUM=/usr/bin/chromium scripts/tour/record.sh
#
# portrait-1889.jpg is a public-domain photo from Wikimedia Commons (credited in site/index.html).
# coordinates.sql holds map coordinates for the sample's places, so the map has pins without a geocode run.
set -euo pipefail
cd "$(dirname "$0")/../.."
here=scripts/tour
PY=${PY:-python3} FLASK=${FLASK:-flask}
export CHROMIUM=${CHROMIUM:-/usr/bin/chromium}
work=$(mktemp -d)
trap 'kill "${site:-}" 2>/dev/null; rm -rf "$work"' EXIT

docker compose exec -T db mariadb -uroot -pdev \
  -e "DROP DATABASE IF EXISTS kith_tour; CREATE DATABASE kith_tour CHARACTER SET utf8mb4;"
export KITH_DB_NAME=kith_tour KITH_DB_USER=root KITH_INSTANCE=$work/instance KITH_MEDIA_ROOT=$work/media
mkdir -p "$KITH_INSTANCE" "$KITH_MEDIA_ROOT" "$work/video"
"$FLASK" --app app setup db --admin ruth --password "screenshots-only-1" | tail -1
"$FLASK" --app app run --port 5078 >"$work/site.log" 2>&1 &
site=$!
for _ in $(seq 40); do curl -sf -o /dev/null http://127.0.0.1:5078/login && break; sleep 0.5; done

"$PY" $here/tour.py http://127.0.0.1:5078 "$work/video" app/sample.ged $here/portrait-1889.jpg \
  $here/coordinates.sql kith_tour
raw=$(ls "$work"/video/*.webm)
end=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$raw" | awk '{print $1 - 0.3}')
# Trim the blank first frames and the tail, and play 20% faster
ffmpeg -v error -y -i "$raw" -ss 0.4 -to "$end" -vf "setpts=PTS/1.2,fps=30,format=yuv420p" \
  -c:v libx264 -preset slow -crf 26 -movflags +faststart -an site/tour.mp4
ffmpeg -v error -y -ss 1.2 -i site/tour.mp4 -frames:v 1 -q:v 4 site/tour-poster.jpg
ls -l site/tour.mp4 site/tour-poster.jpg
