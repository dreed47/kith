#!/usr/bin/env bash
# Run as root: installs and starts Kith's scheduled jobs as systemd timers. The installer and updater run it;
# running it again is harmless.
set -euo pipefail
# Place coordinates (app/geocode.py, OpenStreetMap; only place names leave the server): nightly, only places that
# have none, so the first night takes a few hours and later ones seconds. The weekly email (app/digest.py) sends
# nothing until an admin turns it on (Admin > Email everyone).
for job in "geocode run|Kith place coordinates|*-*-* 02:30" "digest send|Kith weekly email|Mon *-*-* 13:00 UTC"; do
  IFS="|" read -r cmd desc when <<<"$job"
  name="kith-${cmd%% *}"
  cat <<EOF2 >"/etc/systemd/system/${name}.service"
[Unit]
Description=${desc}
After=network-online.target mariadb.service
Wants=network-online.target

[Service]
Type=oneshot
User=kith
Group=kith
WorkingDirectory=/opt/kith
EnvironmentFile=/etc/kith.env
ExecStart=/opt/kith/.venv/bin/flask --app app ${cmd}
ProtectSystem=strict
ReadWritePaths=/var/lib/kith /srv/kith
PrivateTmp=true
NoNewPrivileges=true
EOF2
  cat <<EOF2 >"/etc/systemd/system/${name}.timer"
[Unit]
Description=${desc}

[Timer]
OnCalendar=${when}
Persistent=true

[Install]
WantedBy=timers.target
EOF2
done
systemctl daemon-reload
systemctl enable -q --now kith-geocode.timer kith-digest.timer
