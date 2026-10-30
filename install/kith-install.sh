#!/usr/bin/env bash

# Copyright (c) 2026 The Kith authors
# License: AGPL-3.0 | https://github.com/dreed47/kith/raw/main/LICENSE
# Source: https://github.com/dreed47/kith
# Runs inside the new container (ct/kith.sh starts it). Also the reference for installing Kith by hand on Debian.

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

msg_info "Installing Dependencies"
# pdftoppm (poppler-utils) makes PDF thumbnails.
# rsync makes the media backups.
$STD apt install -y python3-venv poppler-utils rsync
msg_ok "Installed Dependencies"

setup_mariadb
MARIADB_DB_NAME="kith" MARIADB_DB_USER="kith" setup_mariadb_db

fetch_and_deploy_gh_release "kith" "dreed47/kith" "tarball"

msg_info "Setting up Python Environment"
$STD python3 -m venv /opt/kith/.venv
$STD /opt/kith/.venv/bin/pip install -r /opt/kith/deploy/requirements.txt gunicorn==23.0.0
msg_ok "Set up Python Environment"

msg_info "Configuring Kith"
useradd --system --home-dir /var/lib/kith --shell /usr/sbin/nologin kith
install -d -o kith -g kith -m 750 /var/lib/kith /srv/kith /srv/kith/media
# Read by systemd as root, so the database password stays out of the site's reach and out of ps.
install -m 600 /dev/null /etc/kith.env
cat <<ENV >/etc/kith.env
KITH_DB_HOST=127.0.0.1
KITH_DB_PORT=3306
KITH_DB_NAME=kith
KITH_DB_USER=kith
KITH_DB_PASSWORD=${MARIADB_DB_PASS}
KITH_MEDIA_ROOT=/srv/kith/media
KITH_INSTANCE=/var/lib/kith
KITH_STRONG_PASSWORDS=1
# Behind an HTTPS reverse proxy: KITH_SECURE_COOKIES=1 and KITH_TRUSTED_PROXIES=1.
# Email: KITH_SMTP_HOST, _PORT, _USER, _PASSWORD, KITH_MAIL_FROM and KITH_SITE_URL.
ENV
ADMIN_PASS=$(openssl rand -base64 18 | tr -dc 'a-zA-Z0-9' | head -c16)
# The password goes in on stdin (the prompt and its confirmation), never on a command line.
printf '%s\n%s\n' "$ADMIN_PASS" "$ADMIN_PASS" | $STD systemd-run --quiet --wait --pipe --uid=kith --gid=kith \
  --working-directory=/opt/kith -p EnvironmentFile=/etc/kith.env /opt/kith/.venv/bin/flask --app app setup db --admin admin
{
  echo ""
  echo "Kith admin login"
  echo "Username: admin"
  echo "Password: ${ADMIN_PASS}"
} >>~/kith.creds
chmod 600 ~/kith.creds
msg_ok "Configured Kith"

msg_info "Creating Service"
cat <<EOF2 >/etc/systemd/system/kith.service
[Unit]
Description=Kith family history site
After=network.target mariadb.service
Requires=mariadb.service

[Service]
User=kith
Group=kith
WorkingDirectory=/opt/kith
EnvironmentFile=/etc/kith.env
# Database upgrades first, so a new version's tables are in place before it serves a page.
ExecStartPre=/opt/kith/.venv/bin/flask --app app setup upgrade
ExecStart=/opt/kith/.venv/bin/gunicorn --workers 3 --bind 0.0.0.0:8000 --access-logfile - "app:create_app()"
Restart=on-failure
ProtectSystem=strict
ReadWritePaths=/var/lib/kith /srv/kith
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF2
systemctl enable -q --now kith
msg_ok "Created Service"

msg_info "Setting up Backups"
# Nightly backups to /var/backups/kith, and the Admin > Backups page: the site drops a request file in its
# requests folder and the root-run kith-backup (deploy/backup.sh) carries it out.
install -d -o kith -g kith -m 700 /var/lib/kith/requests
install -o root -g root -m 755 /opt/kith/deploy/backup.sh /usr/local/sbin/kith-backup
cat <<EOF2 >/etc/systemd/system/kith-backup.service
[Unit]
Description=Kith backup
After=mariadb.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/kith-backup
EOF2
cat <<EOF2 >/etc/systemd/system/kith-backup.timer
[Unit]
Description=Nightly Kith backup

[Timer]
OnCalendar=*-*-* 03:30
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
EOF2
cat <<EOF2 >/etc/systemd/system/kith-requests.service
[Unit]
Description=Kith backup and restore requests
After=mariadb.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/kith-backup requests
EOF2
cat <<EOF2 >/etc/systemd/system/kith-requests.path
[Unit]
Description=Watch for backup and restore requests from Kith

[Path]
PathExistsGlob=/var/lib/kith/requests/*
Unit=kith-requests.service

[Install]
WantedBy=paths.target
EOF2
systemctl daemon-reload
systemctl enable -q --now kith-requests.path
/usr/local/sbin/kith-backup apply
msg_ok "Set up Backups"

msg_info "Setting up Scheduled Jobs"
bash /opt/kith/deploy/jobs.sh
msg_ok "Set up Scheduled Jobs"

motd_ssh
customize
cleanup_lxc
