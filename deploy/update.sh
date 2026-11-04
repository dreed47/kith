#!/usr/bin/env bash
# Update a Debian install made by hand (README "Debian by hand") to the newest Kith release. Run as root; installed
# as /usr/local/sbin/kith-update, and each update installs the new copy. Proxmox installs use `update` instead.
#
#   kith-update           update, when a newer release is out
#   kith-update --force   install the newest release again
#
# Photos, settings and the database live outside /opt/kith, so only the code is replaced. A copy of the database
# goes to /var/backups/kith first. Database upgrades run as the site starts.
set -euo pipefail

release=$(curl -fsSL https://api.github.com/repos/dreed47/kith/releases/latest)
read -r tag url < <(python3 -c 'import json, sys; r = json.load(sys.stdin); print(r["tag_name"], r["tarball_url"])' \
  <<<"$release")
current=$(sed -n 's/^version = "\(.*\)"/\1/p' /opt/kith/pyproject.toml)
if [[ "v$current" == "$tag" && "${1:-}" != "--force" ]]; then
  echo "Up to date ($current)."
  exit 0
fi
echo "Updating Kith $current to ${tag#v}."

# Download before stopping anything, so a failed download leaves the site running.
new=$(mktemp -d /opt/kith-new.XXXXXX)
trap 'rm -rf "$new"' EXIT
curl -fsSL "$url" | tar -xz --strip-components=1 -C "$new"
chmod 755 "$new"

systemctl stop kith
mkdir -p /var/backups/kith
mariadb-dump --single-transaction kith | gzip >"/var/backups/kith/kith-$(date +%Y%m%d-%H%M%S).sql.gz"
rm -rf /opt/kith
mv "$new" /opt/kith
python3 -m venv /opt/kith/.venv
/opt/kith/.venv/bin/pip install -q -r /opt/kith/deploy/requirements.txt gunicorn==23.0.0
bash /opt/kith/deploy/jobs.sh
if [[ -e /usr/local/sbin/kith-backup ]]; then
  install -m 755 /opt/kith/deploy/backup.sh /usr/local/sbin/kith-backup
fi
install -m 755 /opt/kith/deploy/update.sh /usr/local/sbin/kith-update
systemctl start kith
sleep 2
systemctl is-active kith
echo "Kith ${tag#v} is running."
