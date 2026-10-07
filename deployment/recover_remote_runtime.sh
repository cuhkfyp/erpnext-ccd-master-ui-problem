#!/usr/bin/env bash
# Restore CCD Portal runtime code from the target server's persistent copy after
# Frappe container recreation. This never migrates, installs the app, changes
# governance data, rotates secrets, or changes nginx.
set -Eeuo pipefail

MODE="check"
SITE="${CCD_PORTAL_SITE:-frontend}"
VOLUME_ROOT="${ERPNEXT_VOLUME_ROOT:-/root/erpnext_docker_volume}"
BACKEND_CONTAINER="${CCD_PORTAL_BACKEND_CONTAINER:-frappe_docker-backend-1}"
SCHEDULER_CONTAINER="${CCD_PORTAL_SCHEDULER_CONTAINER:-frappe_docker-scheduler-1}"
QUEUE_LONG_CONTAINER="${CCD_PORTAL_QUEUE_LONG_CONTAINER:-frappe_docker-queue-long-1}"
QUEUE_SHORT_CONTAINER="${CCD_PORTAL_QUEUE_SHORT_CONTAINER:-frappe_docker-queue-short-1}"
FRONTEND_CONTAINER="${CCD_PORTAL_FRONTEND_CONTAINER:-frappe_docker-frontend-1}"
PERSISTENT_APP="$VOLUME_ROOT/persistent_apps/ccd_portal"
APP_IN_CONTAINER="/home/frappe/frappe-bench/apps/ccd_portal"
PTH_IN_CONTAINER="/home/frappe/frappe-bench/env/lib/python3.11/site-packages/ccd_portal.pth"
APPS_TXT="/home/frappe/frappe-bench/sites/apps.txt"
FRONTEND_ASSETS="/home/frappe/frappe-bench/sites/assets/ccd_portal"
containers=(
	"$BACKEND_CONTAINER"
	"$SCHEDULER_CONTAINER"
	"$QUEUE_LONG_CONTAINER"
	"$QUEUE_SHORT_CONTAINER"
)

usage() {
	cat <<'USAGE'
Usage:
  ccd_portal_runtime_recovery.sh --check
  ccd_portal_runtime_recovery.sh --restore

--check    Read-only verification of runtime code, registry, interpreter path,
           and frontend assets. This is the default.
--restore  Restore those runtime-only files from persistent_apps/ccd_portal and
           restart only backend, scheduler, queue-long, and queue-short.

Optional overrides:
  CCD_PORTAL_SITE, ERPNEXT_VOLUME_ROOT, CCD_PORTAL_BACKEND_CONTAINER,
  CCD_PORTAL_SCHEDULER_CONTAINER, CCD_PORTAL_QUEUE_LONG_CONTAINER,
  CCD_PORTAL_QUEUE_SHORT_CONTAINER, CCD_PORTAL_FRONTEND_CONTAINER

Run --restore after container recreation, once the five Frappe containers are
running. It does not run migrations or change site data, secrets, feature flags,
nginx, database, Redis, websocket, VPN, or unrelated applications.
USAGE
}

while (($#)); do
	case "$1" in
		--check) MODE="check" ;;
		--restore) MODE="restore" ;;
		-h|--help) usage; exit 0 ;;
		*) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
	esac
	shift
done

die() {
	printf '[CCD Portal runtime recovery] ERROR: %s\n' "$*" >&2
	exit 1
}

for command_name in docker python3 grep seq; do
	command -v "$command_name" >/dev/null 2>&1 || die "Required command not found: $command_name"
done
[[ "$SITE" =~ ^[A-Za-z0-9._-]+$ ]] || die "Invalid site name"
[[ "$VOLUME_ROOT" == /* && "$VOLUME_ROOT" != "/" ]] || die "Invalid volume root"
for container in "${containers[@]}" "$FRONTEND_CONTAINER"; do
	[[ "$container" =~ ^[A-Za-z0-9_.-]+$ ]] || die "Invalid container name"
	docker inspect "$container" >/dev/null || die "Container does not exist: $container"
	state="$(docker inspect -f '{{.State.Status}}' "$container")"
	[[ "$state" == "running" ]] || die "Container must be running first: $container"
done
[[ -f "$PERSISTENT_APP/pyproject.toml" ]] || die "Missing persistent CCD Portal application"
[[ -f "$PERSISTENT_APP/ccd_portal/hooks.py" ]] || die "Persistent application is incomplete"
[[ -f "$PERSISTENT_APP/ccd_portal/public/ccd-portal/index.html" ]] || \
	die "Persistent public assets are incomplete"

check_runtime() {
	local failed=0 container
	for container in "${containers[@]}"; do
		docker exec "$container" test -f "$APP_IN_CONTAINER/ccd_portal/hooks.py" || failed=1
		docker exec "$container" grep -qx "$APP_IN_CONTAINER" "$PTH_IN_CONTAINER" || failed=1
	done
	docker exec "$BACKEND_CONTAINER" grep -qx ccd_portal "$APPS_TXT" || failed=1
	docker exec "$FRONTEND_CONTAINER" test -f "$FRONTEND_ASSETS/ccd-portal/index.html" || failed=1
	printf '[CCD Portal runtime recovery] Identity readiness (required only before V2 activation):\n'
	docker exec "$BACKEND_CONTAINER" bench --site "$SITE" execute \
		ccd_portal.identity.schema_status || failed=1
	if [[ "$failed" == "0" ]]; then
		printf '[CCD Portal runtime recovery] Runtime is complete.\n'
		return 0
	fi
	printf '[CCD Portal runtime recovery] Runtime is incomplete; run --restore.\n' >&2
	return 1
}

if [[ "$MODE" == "check" ]]; then
	check_runtime
	exit
fi

docker exec "$BACKEND_CONTAINER" bench --site "$SITE" execute frappe.get_installed_apps | \
	grep -Eq '(^|["'"'"'[:space:],])ccd_portal(["'"'"'[:space:],]|$)' || \
	die "ccd_portal is not installed in the target site database"

original_maintenance="$(docker exec "$BACKEND_CONTAINER" bench --site "$SITE" show-config | \
	python3 -c 'import json,sys; print(1 if json.load(sys.stdin).get("maintenance_mode") else 0)')"
docker exec "$BACKEND_CONTAINER" bench --site "$SITE" set-maintenance-mode on
finish() {
	status=$?
	trap - EXIT
	set +e
	if [[ "$status" == "0" && "$original_maintenance" == "0" ]]; then
		docker exec "$BACKEND_CONTAINER" bench --site "$SITE" set-maintenance-mode off >/dev/null
	elif [[ "$status" != "0" ]]; then
		printf '[CCD Portal runtime recovery] Restore failed; maintenance mode remains enabled.\n' >&2
	fi
	exit "$status"
}
trap finish EXIT

for container in "${containers[@]}"; do
	docker exec -u root "$container" rm -rf -- "$APP_IN_CONTAINER"
	docker exec -u root "$container" mkdir -p "$APP_IN_CONTAINER"
	docker cp "$PERSISTENT_APP/." "$container:$APP_IN_CONTAINER/"
	docker exec -u root "$container" chown -R frappe:frappe "$APP_IN_CONTAINER"
	docker exec -u root "$container" sh -c \
		'printf "%s\n" "$1" > "$2"; chown frappe:frappe "$2"' \
		sh "$APP_IN_CONTAINER" "$PTH_IN_CONTAINER"
done

if ! docker exec "$BACKEND_CONTAINER" grep -qx ccd_portal "$APPS_TXT"; then
	docker exec "$BACKEND_CONTAINER" sh -c '
		file="$1"; tmp="${file}.ccd-portal.$$"
		awk "1" "$file" > "$tmp"
		printf "%s\n" ccd_portal >> "$tmp"
		mv "$tmp" "$file"
	' sh "$APPS_TXT"
fi

asset_stage="${FRONTEND_ASSETS}.__incoming__.runtime-recovery"
asset_previous="${FRONTEND_ASSETS}.__previous__.runtime-recovery"
docker exec -u root "$FRONTEND_CONTAINER" rm -rf -- "$asset_stage" "$asset_previous"
docker exec -u root "$FRONTEND_CONTAINER" install -d -m 0755 -o frappe -g frappe "$asset_stage"
docker cp "$PERSISTENT_APP/ccd_portal/public/." "$FRONTEND_CONTAINER:$asset_stage/"
docker exec -u root "$FRONTEND_CONTAINER" chown -R frappe:frappe "$asset_stage"
docker exec -u root "$FRONTEND_CONTAINER" find "$asset_stage" -type d -exec chmod 0755 '{}' '+'
docker exec -u root "$FRONTEND_CONTAINER" find "$asset_stage" -type f -exec chmod 0644 '{}' '+'
docker exec -u root "$FRONTEND_CONTAINER" sh -c '
	set -eu
	current="$1"; incoming="$2"; previous="$3"
	if [ -e "$current" ]; then mv "$current" "$previous"; fi
	if ! mv "$incoming" "$current"; then
		if [ -e "$previous" ]; then mv "$previous" "$current"; fi
		exit 1
	fi
' sh "$FRONTEND_ASSETS" "$asset_stage" "$asset_previous"

docker exec "$BACKEND_CONTAINER" bench --site "$SITE" clear-cache
docker restart "${containers[@]}" >/dev/null
for container in "${containers[@]}"; do
	for _attempt in $(seq 1 60); do
		state="$(docker inspect -f '{{.State.Status}}' "$container")"
		[[ "$state" == "running" ]] && break
		sleep 1
	done
	[[ "${state:-}" == "running" ]] || die "Container did not return: $container"
	docker exec "$container" python -c 'import ccd_portal, ccd_portal.hooks'
done
check_runtime
docker exec -u root "$FRONTEND_CONTAINER" rm -rf -- "$asset_previous"
printf '[CCD Portal runtime recovery] Runtime restored from %s.\n' "$PERSISTENT_APP"
