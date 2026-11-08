#!/usr/bin/env bash
# Start a throwaway Kith for the promo videos: database kith_promo on docker compose's db, port 5079.
# Usage: scripts/promo/site.sh [sample]   ("sample" loads the made-up family and its map pins)
# Prints the PID of the site; kill it when done. FLASK is the flask to run (default: flask on PATH).
#
# Needs ffmpeg, Chromium (CHROMIUM, default /usr/bin/chromium), and a Python with playwright, kokoro-onnx and
# soundfile, plus Kokoro's kokoro-v1.0.onnx and voices-v1.0.bin (github.com/thewh1teagle/kokoro-onnx releases).
#
#   TikTok (1080x1920, captions, no sound):  site.sh sample, then  tiktok.py OUT.mp4
#   YouTube (1920x1080, narrated):           voice.py KOKORO_DIR VOICE_DIR   (reads narration.txt)
#                                            site.sh (empty site: the import happens on camera), then
#                                            youtube.py VOICE_DIR OUT.mp4 [MUSIC.mp3]
# Start a fresh site.sh before each recording.
set -euo pipefail
cd "$(dirname "$0")/../.."
FLASK=${FLASK:-flask}
work=${WORK:-/tmp/kith-promo}
rm -rf "$work"; mkdir -p "$work/instance" "$work/media"
docker compose up -d db >/dev/null 2>&1
for _ in $(seq 30); do docker compose exec -T db mariadb -uroot -pdev -e "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done
docker compose exec -T db mariadb -uroot -pdev \
  -e "DROP DATABASE IF EXISTS kith_promo; CREATE DATABASE kith_promo CHARACTER SET utf8mb4;"
export KITH_DB_NAME=kith_promo KITH_DB_USER=root KITH_INSTANCE=$work/instance KITH_MEDIA_ROOT=$work/media
# A made-up mail account, so "Ask for an account" is open on camera (the videos never send mail)
export KITH_SMTP_USER=promo@example.invalid KITH_SITE_URL=http://127.0.0.1:5079
"$FLASK" --app app setup db --admin ruth --password "screenshots-only-1" >/dev/null
if [[ ${1:-} == sample ]]; then
  "$FLASK" --app app setup sample
  docker compose exec -T db mariadb -uroot -pdev kith_promo < scripts/tour/coordinates.sql
fi
"$FLASK" --app app run --port 5079 >"$work/site.log" 2>&1 &
for _ in $(seq 40); do curl -sf -o /dev/null http://127.0.0.1:5079/login && break; sleep 0.5; done
echo $!
