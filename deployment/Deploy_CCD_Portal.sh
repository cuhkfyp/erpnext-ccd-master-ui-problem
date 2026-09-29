#!/usr/bin/env bash
# Convenience entry point for the versioned CCD Portal UAT/Production deployer.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="${CCD_PORTAL_DEPLOY_SCRIPT:-$SCRIPT_DIR/deploy_remote.sh}"
if [[ ! -f "$DEPLOY_SCRIPT" ]]; then
	DEPLOY_SCRIPT="/root/erpnext_docker_volume/erpnext-ccd-master-ui-problem/deployment/deploy_remote.sh"
fi
if [[ ! -f "$DEPLOY_SCRIPT" ]]; then
	DEPLOY_SCRIPT="/root/erpnext_docker_volume/persistent_apps/ccd_portal/deployment/deploy_remote.sh"
fi
if [[ ! -f "$DEPLOY_SCRIPT" ]]; then
	printf 'Missing CCD Portal deployment script: %s\n' "$DEPLOY_SCRIPT" >&2
	exit 1
fi
exec bash "$DEPLOY_SCRIPT" "$@"
