#!/usr/bin/env bash
# Backups, run as root on the server, installed as /usr/local/sbin/kith-backup (install/kith-install.sh).
#
#   kith-backup             nightly backup (kith-backup.timer), named by the date
#   kith-backup now         a backup named by date and time
#   kith-backup restore STAMP [media]
#                           put the site back to a backup: the database, and with "media" the photos and
#                           documents too. A backup of how things are now is taken first, so it can be undone.
#   kith-backup delete STAMP
#                           remove one backup (never the only one)
#   kith-backup requests    carry out what admins asked for on the site's Backups page
#                           (kith-requests.path starts this when a request appears)
#   kith-backup status      only rewrite the status file the site reads
#   kith-backup schedule ON HH:MM DAYS
#                           nightly backups on (1) or off (0), when, and how many days of backups to keep
#                           (also asked for from the Backups page). Kept in STATUS_DIR/schedule.
#   kith-backup apply       set the timer from the saved schedule (update.sh runs this after installing it)
#
# Each backup is
#   db/<name>-STAMP.sql.gz   the whole database (every table)
#   media/STAMP/             the media files; unchanged files are hard links to the backup before,
#                            so each costs only what changed
#   notes/STAMP              why it was made, when it wasn't the nightly one (shown on the Backups page)
# STAMP is YYYY-MM-DD, or YYYY-MM-DD_HHMM for one made during the day.
# Keeps the schedule's days (else KITH_BACKUP_KEEP_DAYS, else 30) of backups. When KITH_BACKUP_REMOTE is set (an rsync target such as
# backup@nas:/volume1/kith), the whole backup folder is then copied there: a backup on the same machine
# doesn't survive that machine.
#
# The site runs as an unprivileged user and can't read the backups or run this. It only drops a request file
# (an empty file whose name says what to do) into REQUESTS; this script reads nothing but those names.
# What it tells the site goes in STATUS_DIR, which is root's (the site's group may read it).
set -euo pipefail
# The site's name comes from how this was installed (kith-backup; kith-backup on a site from before the
# rename): its settings file, service, user and folders all carry it.
SITE=$(basename "$0")
SITE=${SITE%-backup}
# Read the settings the way systemd does (KEY=value, spaces allowed), never by running the file as shell.
# Old KITH_ names count as their KITH_ ones.
while IFS= read -r line; do export "KITH_${line#*_}"; done < <(grep -E '^(KITH|GENEALOGY)_[A-Z_]+=' "/etc/$SITE.env")
DEST=${KITH_BACKUP_DIR:-/var/backups/$SITE}
REQUESTS=${KITH_INSTANCE:-/var/lib/$SITE}/requests
STATUS_DIR=/var/lib/$SITE-backup
STAMP_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}(_[0-9]{4})?$'
SCHEDULE_RE='^([01]) ([01][0-9]|2[0-3]):([0-5][0-9]) ([1-9][0-9]{0,2})$'  # on/off, HH:MM, 1-999 days
TIMER_DROPIN=/etc/systemd/system/$SITE-backup.timer.d/schedule.conf

# The schedule: "1 03:30 30" (on, at 03:30, keep 30 days). Written only by this script, and checked anyway.
ENABLED=1 AT=03:30 KEEP_DAYS=${KITH_BACKUP_KEEP_DAYS:-30}
if [[ -f $STATUS_DIR/schedule && $(head -c 64 "$STATUS_DIR/schedule") =~ $SCHEDULE_RE ]]; then
  ENABLED=${BASH_REMATCH[1]} AT=${BASH_REMATCH[2]}:${BASH_REMATCH[3]} KEEP_DAYS=${BASH_REMATCH[4]}
fi
umask 077  # dumps hold password hashes and living people's details
mkdir -p "$DEST/db" "$DEST/media" "$DEST/notes"

# One run at a time: the nightly backup, a requested one and a restore never overlap.
exec 9> "/run/$SITE-backup.lock"
flock 9

# The database password goes in a private file, not on the command line where ps would show it.
creds=$(mktemp)
trap 'rm -f "$creds"' EXIT
printf '[client]\nhost=%s\nport=%s\nuser=%s\npassword=%s\n' "$KITH_DB_HOST" "${KITH_DB_PORT:-3306}" \
  "$KITH_DB_USER" "$KITH_DB_PASSWORD" > "$creds"

# status.json for the site: the backups there are, what is running, and how the last action went.
# Everything in it comes from this script (file names it made, sizes, its own messages).
write_status() {  # running last_action last_ok last_message
  install -d -o root -g "$SITE" -m 750 "$STATUS_DIR"
  local next tz
  next=$(systemctl show "$SITE-backup.timer" -p NextElapseUSecRealtime --value 2>/dev/null || true)
  tz=$(date +%Z)
  DEST=$DEST NAME=$KITH_DB_NAME REMOTE=${KITH_BACKUP_REMOTE:+1} RUNNING=$1 ACTION=$2 OK=$3 MESSAGE=$4 \
    ENABLED=$ENABLED AT=$AT KEEP=$KEEP_DAYS NEXT=$next TZ_NAME=$tz \
    STATUS=$STATUS_DIR/status.json GROUP=$SITE python3 -I - <<'EOF'
import json, os, re, time
dest, name, path = os.environ["DEST"], os.environ["NAME"], os.environ["STATUS"]
try:
    old = json.load(open(path))
except (OSError, ValueError):
    old = {}
backups = []
for f in sorted(os.listdir(f"{dest}/db"), reverse=True):
    m = re.fullmatch(re.escape(name) + r"-(\d{4}-\d\d-\d\d(?:_\d{4})?)\.sql\.gz", f)
    if m:
        stamp = m.group(1)
        note = f"{dest}/notes/{stamp}"
        backups.append({"stamp": stamp, "db_bytes": os.path.getsize(f"{dest}/db/{f}"),
                        "media": os.path.isdir(f"{dest}/media/{stamp}"),
                        "note": open(note).read(200).strip() if os.path.isfile(note) else ""})
now = time.strftime("%Y-%m-%d %H:%M:%S")
status = {"updated": now, "running": os.environ["RUNNING"] or None, "remote": bool(os.environ["REMOTE"]),
          "backups": backups, "last": old.get("last"),
          "schedule": {"enabled": os.environ["ENABLED"] == "1", "time": os.environ["AT"],
                       "keep_days": int(os.environ["KEEP"]), "timezone": os.environ["TZ_NAME"],
                       "next": os.environ["NEXT"] if os.environ["ENABLED"] == "1" else ""}}
if os.environ["ACTION"]:
    status["last"] = {"action": os.environ["ACTION"], "ok": os.environ["OK"] == "1",
                      "message": os.environ["MESSAGE"], "finished": now}
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(status, f, indent=1)
os.chown(tmp, 0, __import__("grp").getgrnam(os.environ["GROUP"]).gr_gid)
os.chmod(tmp, 0o640)
os.replace(tmp, path)
EOF
}

# Every step checks itself: run from an "if", bash ignores set -e inside these functions.
backup() {  # STAMP [NOTE]
  local stamp=$1 dump="$DEST/db/$KITH_DB_NAME-$1.sql.gz" previous
  # TNG's tables are MyISAM: --lock-tables (the default) read-locks all of this database's tables together for
  # the second or two the dump takes, so the copy is consistent. (--lock-all-tables would need the RELOAD right.)
  mariadb-dump --defaults-extra-file="$creds" --default-character-set=utf8mb4 --lock-tables \
    "$KITH_DB_NAME" | gzip > "$dump.tmp" || { rm -f "$dump.tmp"; return 1; }
  mv "$dump.tmp" "$dump" || return 1
  # Media, next to the latest earlier snapshot. Thumbnails and deleted media are left out (made again on demand).
  previous=$(find "$DEST/media" -mindepth 1 -maxdepth 1 -type d ! -name "$stamp" | sort | tail -1)
  rsync -a --delete --exclude .thumbs --exclude .deleted ${previous:+--link-dest="$previous"} \
    "$KITH_MEDIA_ROOT/" "$DEST/media/$stamp/" || return 1
  if [[ -n ${2:-} ]]; then printf '%s\n' "$2" > "$DEST/notes/$stamp" || return 1; else rm -f "$DEST/notes/$stamp"; fi
  echo "backup $stamp done: $(du -sh "$dump" | cut -f1) database"
}

tidy() {
  # Old backups go by the date in their name (rsync copies folder dates, so file ages can't be trusted).
  local cutoff f d
  cutoff=$(date -d "-$KEEP_DAYS days" +%F)
  shopt -s nullglob
  for f in "$DEST"/db/*.sql.gz; do
    [[ $f =~ ([0-9]{4}-[0-9]{2}-[0-9]{2})(_[0-9]{4})?\.sql\.gz$ && ${BASH_REMATCH[1]} < "$cutoff" ]] && rm -f "$f"
  done
  for d in "$DEST"/media/*/; do
    [[ $(basename "$d") < "$cutoff" ]] && rm -rf "$d"
  done
  for f in "$DEST"/notes/*; do  # a note goes with its backup
    [[ -f $DEST/db/$KITH_DB_NAME-$(basename "$f").sql.gz ]] || rm -f "$f"
  done
  if [[ -n ${KITH_BACKUP_REMOTE:-} ]]; then
    rsync -aH --delete "$DEST/" "$KITH_BACKUP_REMOTE/" || return 1
  fi
  return 0
}

run_backup() {  # STAMP ACTION [NOTE]
  write_status backup "" "" ""
  if backup "$1" "${3:-}" && tidy; then
    write_status "" "$2" 1 "Backup $1 made."
  else
    write_status "" "$2" 0 "Backup $1 failed; see journalctl -u $SITE-backup -u $SITE-requests."
    return 1
  fi
}

restore() {  # STAMP [media]
  local stamp=$1 media=${2:-} dump="$DEST/db/$KITH_DB_NAME-$1.sql.gz" safety
  [[ $stamp =~ $STAMP_RE && -f $dump ]] || { echo "no backup $stamp" >&2; write_status "" restore 0 "There is no backup $stamp."; return 1; }
  if [[ $media == media && ! -d $DEST/media/$stamp ]]; then
    write_status "" restore 0 "Backup $stamp has no photos and documents."; return 1
  fi
  write_status restore "" "" ""
  safety=$(date +%F_%H%M)
  if [[ $safety == "$stamp" ]]; then sleep 60; safety=$(date +%F_%H%M); fi  # never overwrite the one being restored
  if ! backup "$safety" "Taken just before restoring $stamp"; then
    write_status "" restore 0 "Nothing was restored: the backup of how things are now ($safety) failed."; return 1
  fi
  systemctl stop "$SITE" || { write_status "" restore 0 "Nothing was restored: the site wouldn't stop."; return 1; }
  local ok=1
  # --sandbox: the dump may only hold SQL, never client commands such as running a shell.
  zcat "$dump" | mariadb --defaults-extra-file="$creds" --sandbox "$KITH_DB_NAME" || ok=0
  if [[ $ok == 1 && $media == media ]]; then
    rsync -a --delete --exclude .thumbs --exclude .deleted "$DEST/media/$stamp/" "$KITH_MEDIA_ROOT/" \
      && chown -R "$SITE:$SITE" "$KITH_MEDIA_ROOT" || ok=0
  fi
  systemctl start "$SITE"
  local what="the database"; [[ $media == media ]] && what="the database, photos and documents"
  if [[ $ok == 1 ]]; then
    write_status "" restore 1 "Restored $what from $stamp. How things were just before is backup $safety."
  else
    write_status "" restore 0 "Restoring $stamp failed part way. Backup $safety holds how things were just before; restore that."
    return 1
  fi
}

delete_backup() {  # STAMP
  local stamp=$1 dump="$DEST/db/$KITH_DB_NAME-$1.sql.gz"
  [[ $stamp =~ $STAMP_RE && -f $dump ]] || { write_status "" delete 0 "There is no backup $stamp."; return 1; }
  if (( $(find "$DEST/db" -maxdepth 1 -name "$KITH_DB_NAME-*.sql.gz" | wc -l) < 2 )); then
    write_status "" delete 0 "Backup $stamp is the only one, so it was kept."; return 1
  fi
  # Other snapshots' hard links to the same media files keep those files; only this snapshot's names go.
  if rm -f "$dump" "$DEST/notes/$stamp" && rm -rf "${DEST:?}/media/$stamp"; then
    write_status "" delete 1 "Backup $stamp deleted."
  else
    write_status "" delete 0 "Deleting backup $stamp failed part way."; return 1
  fi
}

apply() {
  # The timer runs at the schedule's time (no random delay: the admin chose it), or not at all.
  mkdir -p "$(dirname "$TIMER_DROPIN")"
  printf '[Timer]\nOnCalendar=\nOnCalendar=*-*-* %s:00\nRandomizedDelaySec=0\n' "$AT" > "$TIMER_DROPIN"
  chmod 644 "$TIMER_DROPIN"
  systemctl daemon-reload
  if [[ $ENABLED == 1 ]]; then systemctl enable --quiet --now "$SITE-backup.timer"
  else systemctl disable --quiet --now "$SITE-backup.timer"; fi
}

schedule() {  # ON HH:MM DAYS
  if ! [[ "$1 $2 $3" =~ $SCHEDULE_RE ]]; then
    write_status "" schedule 0 "Schedule not changed: \"$1 $2 $3\" isn't on/off, a time and a number of days."; return 1
  fi
  ENABLED=$1 AT=$2 KEEP_DAYS=$((10#$3))
  install -d -o root -g "$SITE" -m 750 "$STATUS_DIR"
  printf '%s %s %s\n' "$ENABLED" "$AT" "$KEEP_DAYS" > "$STATUS_DIR/schedule"
  if ! apply; then write_status "" schedule 0 "Saved the schedule, but setting the timer failed."; return 1; fi
  if [[ $ENABLED == 1 ]]; then
    write_status "" schedule 1 "Nightly backups at $AT ($(date +%Z)), keeping $KEEP_DAYS days."
  else
    write_status "" schedule 1 "Nightly backups are off. Backups can still be made by hand, and one is always made before a restore."
  fi
}

requests() {
  # Only the names of plain files are read; anything not exactly a known request is thrown away.
  # The folder is the site's, so the site could swap it for a link to somewhere else: work only inside the real
  # folder (checked after entering it), and remove entries without following them, never recursively.
  local name todo=()
  cd -P -- "$REQUESTS" 2>/dev/null || return 0
  if [[ $(pwd -P) != "$(realpath -ms -- "$REQUESTS")" ]]; then cd /; return 0; fi
  shopt -s nullglob
  for name in *; do
    if [[ -d $name && ! -L $name ]]; then rmdir -- "$name" 2>/dev/null || true; else rm -f -- "$name"; fi
    if [[ $name == backup ]]; then todo+=(backup)
    elif [[ $name =~ ^restore(\+media)?@(.+)$ ]]; then
      local media=${BASH_REMATCH[1]:+ media} stamp=${BASH_REMATCH[2]}
      [[ $stamp =~ $STAMP_RE ]] && todo+=("restore $stamp$media")
    elif [[ $name =~ ^delete@(.+)$ ]]; then
      local stamp=${BASH_REMATCH[1]}
      [[ $stamp =~ $STAMP_RE ]] && todo+=("delete $stamp")
    elif [[ $name =~ ^schedule@([01])-([0-9]{2})([0-9]{2})-([0-9]{1,3})$ ]]; then  # checked again by schedule()
      todo+=("schedule ${BASH_REMATCH[1]} ${BASH_REMATCH[2]}:${BASH_REMATCH[3]} ${BASH_REMATCH[4]}")
    fi
  done
  cd /
  for job in "${todo[@]}"; do
    case $job in
      backup) run_backup "$(date +%F_%H%M)" backup "Made from the Backups page" || true ;;
      restore*) restore ${job#restore } || true ;;  # unquoted on purpose: "STAMP" or "STAMP media"
      schedule*) schedule ${job#schedule } || true ;;  # unquoted on purpose: three words
      delete*) delete_backup "${job#delete }" || true ;;
    esac
  done
}

case ${1:-nightly} in
  nightly) if [[ $ENABLED == 1 ]]; then run_backup "$(date +%F)" nightly; else echo "nightly backups are off"; fi ;;
  now) run_backup "$(date +%F_%H%M)" backup "Made by hand on the server" ;;
  restore) restore "${2:-}" "${3:-}" ;;
  delete) delete_backup "${2:-}" ;;
  requests) requests ;;
  status) write_status "" "" "" "" ;;
  schedule) schedule "${2:-}" "${3:-}" "${4:-}" ;;
  apply) apply; write_status "" "" "" "" ;;
  *) echo "usage: $0 [now | restore STAMP [media] | delete STAMP | requests | status | schedule ON HH:MM DAYS | apply]" >&2; exit 2 ;;
esac
