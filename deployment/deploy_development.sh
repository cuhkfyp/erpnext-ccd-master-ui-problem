#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="ccd_portal"
SITE="${FRAPPE_SITE:-frontend}"
VOLUME_ROOT="${ERPNEXT_VOLUME_ROOT:-/root/erpnext_docker_volume}"
PERSISTENT_APP="$VOLUME_ROOT/persistent_apps/$APP_NAME"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ ! -f "$APP_ROOT/pyproject.toml" && -f "$PERSISTENT_APP/pyproject.toml" ]]; then
	APP_ROOT="$PERSISTENT_APP"
fi
APP_IN_CONTAINER="/home/frappe/frappe-bench/apps/$APP_NAME"
BENCH="/usr/local/bin/bench"
RUNTIME_ONLY=0
RESTART=0
PERSIST_ONLY=0

for option in "$@"; do
	case "$option" in
		--runtime-only) RUNTIME_ONLY=1 ;;
		--restart) RESTART=1 ;;
		--persist-only) PERSIST_ONLY=1 ;;
		*) echo "Unknown option: $option" >&2; exit 2 ;;
	esac
done

if (( PERSIST_ONLY && (RUNTIME_ONLY || RESTART) )); then
	echo "--persist-only cannot be combined with --runtime-only or --restart" >&2
	exit 2
fi

containers=(
	frappe_docker-backend-1
	frappe_docker-scheduler-1
	frappe_docker-queue-long-1
	frappe_docker-queue-short-1
)

test -f "$APP_ROOT/pyproject.toml"
test -f "$APP_ROOT/ccd_portal/hooks.py"
test -f "$APP_ROOT/frontend/package-lock.json"
test -f "$APP_ROOT/ccd_portal/www/ccd_portal.html"
test -d "$APP_ROOT/ccd_portal/public/ccd-portal/assets"

if (( ! RUNTIME_ONLY )); then
	python3 "$APP_ROOT/scripts/scan_repository.py"
	python3 -m compileall -q "$APP_ROOT/ccd_portal"
	mkdir -p "$PERSISTENT_APP"
	rsync -a --delete \
		--exclude='.git/' --exclude='frontend/node_modules/' \
		--exclude='**/__pycache__/' --exclude='*.py[co]' \
		"$APP_ROOT/" "$PERSISTENT_APP/"
	install -m 0755 "$APP_ROOT/deployment/deploy_development.sh" "$VOLUME_ROOT/deploy_ccd_portal.sh"
fi

if (( PERSIST_ONLY )); then
	echo "ccd_portal recovery source synchronized without touching Docker or the site."
	echo "Persistent recovery source: $PERSISTENT_APP"
	exit 0
fi

test -f "$PERSISTENT_APP/pyproject.toml"
test -f "$PERSISTENT_APP/ccd_portal/hooks.py"

for container in "${containers[@]}"; do
	docker inspect "$container" >/dev/null
	docker start "$container" >/dev/null
	docker exec -u root "$container" mkdir -p "$APP_IN_CONTAINER"
	docker cp "$PERSISTENT_APP/." "$container:$APP_IN_CONTAINER/"
	docker exec -u root "$container" chown -R frappe:frappe "$APP_IN_CONTAINER"
	docker exec -u root "$container" sh -c \
		'printf "%s\n" "$1" > /home/frappe/frappe-bench/env/lib/python3.11/site-packages/ccd_portal.pth' \
		sh "$APP_IN_CONTAINER"
done

if (( ! RUNTIME_ONLY )); then
	if ! docker exec frappe_docker-backend-1 grep -qx "$APP_NAME" \
		/home/frappe/frappe-bench/sites/apps.txt; then
		docker exec frappe_docker-backend-1 sh -c \
			'file=/home/frappe/frappe-bench/sites/apps.txt; tmp="${file}.ccd-portal.$$"; awk "1" "$file" > "$tmp"; printf "%s\n" "$1" >> "$tmp"; mv "$tmp" "$file"' \
			sh "$APP_NAME"
	fi
	if ! docker exec frappe_docker-backend-1 "$BENCH" --site "$SITE" list-apps | grep -qx "$APP_NAME"; then
		docker exec frappe_docker-backend-1 "$BENCH" --site "$SITE" install-app "$APP_NAME"
	fi
	docker exec frappe_docker-backend-1 "$BENCH" --site "$SITE" migrate
	docker exec frappe_docker-backend-1 "$BENCH" --site "$SITE" execute \
		ccd_portal.install.ensure_site_secret
	docker exec frappe_docker-backend-1 "$BENCH" --site "$SITE" execute \
		ccd_portal.install.ensure_development_administrator
	docker exec frappe_docker-backend-1 "$BENCH" build --app "$APP_NAME"
fi
docker exec frappe_docker-backend-1 "$BENCH" --site "$SITE" clear-cache

if (( ! RESTART )); then
	old_workers="$(docker exec frappe_docker-backend-1 sh -c \
		'pgrep -P 1 -x gunicorn 2>/dev/null || true' | tr '\n' ' ')"
	docker exec frappe_docker-backend-1 grep -qx gunicorn /proc/1/comm
	docker kill --signal=HUP frappe_docker-backend-1 >/dev/null
	workers_reloaded=0
	for _attempt in {1..40}; do
		current_workers="$(docker exec frappe_docker-backend-1 sh -c \
			'pgrep -P 1 -x gunicorn 2>/dev/null || true' | tr '\n' ' ')"
		stale_worker=0
		for worker in $old_workers; do
			[[ " $current_workers " == *" $worker "* ]] && stale_worker=1
		done
		set -- $current_workers
		if (( $# >= 2 && ! stale_worker )); then
			workers_reloaded=1
			break
		fi
		sleep 0.25
	done
	(( workers_reloaded )) || {
		echo "Gunicorn did not complete its graceful worker reload" >&2
		exit 1
	}
	echo "Gunicorn web workers gracefully reloaded without restarting the backend container."
fi

asset_stage="$(mktemp -d "$VOLUME_ROOT/.ccd-portal-assets.XXXXXX")"
trap 'rm -rf -- "$asset_stage"' EXIT
mkdir -p "$asset_stage/$APP_NAME"
if docker cp "frappe_docker-backend-1:$APP_IN_CONTAINER/$APP_NAME/public/." \
	"$asset_stage/$APP_NAME/"; then
	docker exec frappe_docker-frontend-1 mkdir -p "/home/frappe/frappe-bench/sites/assets/$APP_NAME"
	docker cp "$asset_stage/$APP_NAME/." "frappe_docker-frontend-1:/home/frappe/frappe-bench/sites/assets/$APP_NAME/"
fi

if (( RESTART )); then
	docker restart "${containers[@]}" >/dev/null
	if [[ -x "$VOLUME_ROOT/sshmount_docker_backend.sh" ]]; then
		(cd /tmp && "$VOLUME_ROOT/sshmount_docker_backend.sh") || \
			echo "Warning: portal deployed, but the optional backend SSHFS remount failed." >&2
	fi
else
	echo "Runtime containers were not restarted. Use --restart only in a controlled window."
fi

echo "ccd_portal deployed to $SITE with its feature flag disabled."
echo "The site-local HMAC secret was preserved or generated without printing it."
echo "Configure environment-owned governance records before policy activation and index generation."
echo "Persistent recovery source: $PERSISTENT_APP"
echo "Recovery command after container recreation: $VOLUME_ROOT/deploy_ccd_portal.sh --runtime-only"
