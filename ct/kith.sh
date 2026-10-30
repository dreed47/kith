#!/usr/bin/env bash
# Kith in a Proxmox VE container, in the community-scripts (ProxmoxVE helper scripts) format. On the Proxmox host:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/dreed47/kith/main/ct/kith.sh)"
# The engine comes from community-scripts/core; COMMUNITY_SCRIPTS_URL points it at this repo for install/kith-install.sh.
# Run it again inside the container (or choose Update) to update to the latest release.
export COMMUNITY_SCRIPTS_URL="${COMMUNITY_SCRIPTS_URL:-https://raw.githubusercontent.com/dreed47/kith/main}"
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")
# Copyright (c) 2026 The Kith authors
# License: AGPL-3.0 | https://github.com/dreed47/kith/raw/main/LICENSE
# Source: https://github.com/dreed47/kith

APP="Kith"
var_tags="${var_tags:-genealogy}"
var_cpu="${var_cpu:-2}"
var_ram="${var_ram:-1024}"
var_disk="${var_disk:-8}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_arm64="${var_arm64:-no}"
var_unprivileged="${var_unprivileged:-1}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources

  if [[ ! -d /opt/kith ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  if check_for_gh_release "kith" "dreed47/kith"; then
    msg_info "Stopping Service"
    systemctl stop kith
    msg_ok "Stopped Service"

    # The new version may upgrade the database as it starts: keep a copy of it first.
    msg_info "Backing up Database"
    mkdir -p /var/backups/kith
    mariadb-dump --single-transaction kith | gzip >"/var/backups/kith/kith-$(date +%Y%m%d-%H%M%S).sql.gz"
    msg_ok "Backed up Database to /var/backups/kith"

    # Photos, settings and the database live outside /opt/kith, so a clean install only replaces the code.
    CLEAN_INSTALL=1 fetch_and_deploy_gh_release "kith" "dreed47/kith" "tarball"
    install -o root -g root -m 755 /opt/kith/deploy/backup.sh /usr/local/sbin/kith-backup
    bash /opt/kith/deploy/jobs.sh

    msg_info "Updating Python Environment"
    $STD python3 -m venv /opt/kith/.venv
    $STD /opt/kith/.venv/bin/pip install -r /opt/kith/deploy/requirements.txt gunicorn==23.0.0
    msg_ok "Updated Python Environment"

    msg_info "Starting Service"
    systemctl start kith
    msg_ok "Started Service"
    msg_ok "Updated successfully!"
  fi
  exit
}

start
build_container
description

msg_ok "Completed Successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW} Access it using the following URL:${CL}"
echo -e "${TAB}${GATEWAY}${BGN}http://${IP}:8000${CL}"
echo -e "${INFO}${YW} The admin login is in the container: cat ~/kith.creds${CL}"
