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
FRONTEND_CONTAINER="frappe_docker-frontend-1"
NGINX_ROUTE_SNIPPET="$APP_ROOT/deployment/nginx_frontend_route_overrides.conf"
PERSISTENT_NGINX_CONFIG="$VOLUME_ROOT/frappe_nginx_current.conf"
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
test -f "$NGINX_ROUTE_SNIPPET"

persist_nginx_route_overrides() {
	local nginx_stage
	local begin_marker="# BEGIN CCD Portal managed Desk entry redirects."
	local end_marker="# END CCD Portal managed Desk entry redirects."

	if [[ ! -f "$PERSISTENT_NGINX_CONFIG" ]]; then
		(( PERSIST_ONLY )) && {
			echo "Persistent Frappe nginx configuration is missing: $PERSISTENT_NGINX_CONFIG" >&2
			exit 1
		}
		docker cp "$FRONTEND_CONTAINER:/etc/nginx/conf.d/frappe.conf" "$PERSISTENT_NGINX_CONFIG"
	fi
	nginx_stage="$(mktemp "$VOLUME_ROOT/.frappe-nginx.XXXXXX")"
	if grep -qF "$begin_marker" "$PERSISTENT_NGINX_CONFIG"; then
		grep -qF "$end_marker" "$PERSISTENT_NGINX_CONFIG"
		awk -v snippet="$NGINX_ROUTE_SNIPPET" -v begin="$begin_marker" -v end="$end_marker" '
			index($0, begin) {
				while ((getline line < snippet) > 0) print line
				close(snippet)
				skipping = 1
				next
			}
			index($0, end) { skipping = 0; next }
			!skipping { print }
		' "$PERSISTENT_NGINX_CONFIG" > "$nginx_stage"
	else
		if grep -qF "$end_marker" "$PERSISTENT_NGINX_CONFIG"; then
			echo "Incomplete managed nginx redirect block: $PERSISTENT_NGINX_CONFIG" >&2
			rm -f -- "$nginx_stage"
			exit 1
		fi
		sed "/^server[[:space:]]*{$/r $NGINX_ROUTE_SNIPPET" "$PERSISTENT_NGINX_CONFIG" > "$nginx_stage"
	fi
	grep -qF "$begin_marker" "$nginx_stage"
	grep -qF "$end_marker" "$nginx_stage"
	if ! cmp -s "$nginx_stage" "$PERSISTENT_NGINX_CONFIG"; then
		install -m 0644 "$nginx_stage" "$PERSISTENT_NGINX_CONFIG"
	fi
	rm -f -- "$nginx_stage"
}

apply_nginx_route_overrides() {
	docker cp "$PERSISTENT_NGINX_CONFIG" "$FRONTEND_CONTAINER:/tmp/frappe.conf.ccd-portal"
	docker exec -u root "$FRONTEND_CONTAINER" sh -c '
		set -eu
		current=/etc/nginx/conf.d/frappe.conf
		backup=/tmp/frappe.conf.before-ccd-portal
		cp -p "$current" "$backup"
		cp /tmp/frappe.conf.ccd-portal "$current"
	'
	repair_nginx_runtime_permissions
	if docker exec -u frappe "$FRONTEND_CONTAINER" nginx -t; then
		docker exec -u frappe "$FRONTEND_CONTAINER" nginx -s reload
		docker exec -u root "$FRONTEND_CONTAINER" \
			rm -f /tmp/frappe.conf.before-ccd-portal /tmp/frappe.conf.ccd-portal
	else
		docker exec -u root "$FRONTEND_CONTAINER" sh -c '
			cp /tmp/frappe.conf.before-ccd-portal /etc/nginx/conf.d/frappe.conf
			rm -f /tmp/frappe.conf.before-ccd-portal /tmp/frappe.conf.ccd-portal
		'
		repair_nginx_runtime_permissions
		docker exec -u frappe "$FRONTEND_CONTAINER" nginx -t
		exit 1
	fi
}

repair_nginx_runtime_permissions() {
	# The frontend image runs nginx as frappe. Running nginx -t as root changes
	# its private temp directories to the compiled-in nobody user, causing HTML
	# 500 responses whenever a request body exceeds the 16 KiB memory buffer.
	docker exec -u root "$FRONTEND_CONTAINER" sh -c '
		set -eu
		for dir in body proxy fastcgi uwsgi scgi; do
			target="/var/lib/nginx/$dir"
			test -d "$target"
			chown frappe:frappe "$target"
			chmod 0700 "$target"
		done
	'
}

sync_frontend_dependency_assets() {
	local app source target app_stage
	for app in chat hrms; do
		source="/home/frappe/frappe-bench/apps/$app/$app/public"
		target="/home/frappe/frappe-bench/apps/$app/$app/public"
		if ! docker exec frappe_docker-backend-1 test -d "$source"; then
			continue
		fi
		app_stage="$(mktemp -d "$VOLUME_ROOT/.frontend-$app-assets.XXXXXX")"
		docker cp "frappe_docker-backend-1:$source/." "$app_stage/"
		docker exec -u root "$FRONTEND_CONTAINER" install -d -m 0755 \
			-o frappe -g frappe "$target"
		docker cp "$app_stage/." "$FRONTEND_CONTAINER:$target/"
		docker exec -u root "$FRONTEND_CONTAINER" chown -R frappe:frappe "$target"
		docker exec -u root "$FRONTEND_CONTAINER" find "$target" \
			-type d -exec chmod 0755 '{}' '+'
		docker exec -u root "$FRONTEND_CONTAINER" find "$target" \
			-type f -exec chmod 0644 '{}' '+'
		rm -rf -- "$app_stage"
	done
}

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

persist_nginx_route_overrides

if (( PERSIST_ONLY )); then
	echo "ccd_portal recovery source synchronized without touching Docker or the site."
	echo "Persistent recovery source: $PERSISTENT_APP"
	echo "Persistent Desk entry redirects: $PERSISTENT_NGINX_CONFIG"
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
	docker exec -u frappe -w "$APP_IN_CONTAINER/frontend" frappe_docker-backend-1 \
		npm ci --no-audit --no-fund
	docker exec -u frappe -w "$APP_IN_CONTAINER/frontend" frappe_docker-backend-1 \
		npm run check
	docker exec -u frappe -w "$APP_IN_CONTAINER/frontend" frappe_docker-backend-1 \
		npm run build
	docker exec -u frappe frappe_docker-backend-1 sed -i 's/[[:space:]]\+$//' \
		"$APP_IN_CONTAINER/$APP_NAME/public/ccd-portal/index.html" \
		"$APP_IN_CONTAINER/$APP_NAME/www/ccd_portal.html"
	docker exec -u root frappe_docker-backend-1 find \
		"$APP_IN_CONTAINER/$APP_NAME/public/ccd-portal" -type d -exec chmod 0755 '{}' '+'
	docker exec -u root frappe_docker-backend-1 find \
		"$APP_IN_CONTAINER/$APP_NAME/public/ccd-portal" -type f -exec chmod 0644 '{}' '+'
	build_stage="$(mktemp -d "$VOLUME_ROOT/.ccd-portal-build.XXXXXX")"
	build_output="$build_stage/output"
	install -d -m 0755 "$build_output"
	docker cp \
		"frappe_docker-backend-1:$APP_IN_CONTAINER/$APP_NAME/public/ccd-portal/." \
		"$build_output/"
	find "$build_output" -type d -exec chmod 0755 '{}' '+'
	find "$build_output" -type f -exec chmod 0644 '{}' '+'
	rsync -a --delete "$build_output/" "$PERSISTENT_APP/$APP_NAME/public/ccd-portal/"
	docker cp \
		"frappe_docker-backend-1:$APP_IN_CONTAINER/$APP_NAME/www/ccd_portal.html" \
		"$PERSISTENT_APP/$APP_NAME/www/ccd_portal.html"
	if [[ "$APP_ROOT" != "$PERSISTENT_APP" ]]; then
		rsync -a --delete "$build_output/" "$APP_ROOT/$APP_NAME/public/ccd-portal/"
		cp -p "$PERSISTENT_APP/$APP_NAME/www/ccd_portal.html" \
			"$APP_ROOT/$APP_NAME/www/ccd_portal.html"
	fi
	rm -rf -- "$build_stage"

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
apply_nginx_route_overrides

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
install -d -m 0755 "$asset_stage/$APP_NAME"
if docker cp "frappe_docker-backend-1:$APP_IN_CONTAINER/$APP_NAME/public/." \
	"$asset_stage/$APP_NAME/"; then
	find "$asset_stage/$APP_NAME" -type d -exec chmod 0755 '{}' '+'
	find "$asset_stage/$APP_NAME" -type f -exec chmod 0644 '{}' '+'
	docker exec -u root frappe_docker-frontend-1 install -d -m 0755 \
		-o frappe -g frappe "/home/frappe/frappe-bench/sites/assets/$APP_NAME"
	docker cp "$asset_stage/$APP_NAME/." "frappe_docker-frontend-1:/home/frappe/frappe-bench/sites/assets/$APP_NAME/"
	docker exec -u root frappe_docker-frontend-1 chown -R frappe:frappe \
		"/home/frappe/frappe-bench/sites/assets/$APP_NAME"
	docker exec -u root frappe_docker-frontend-1 find \
		"/home/frappe/frappe-bench/sites/assets/$APP_NAME" -type d -exec chmod 0755 '{}' '+'
	docker exec -u root frappe_docker-frontend-1 find \
		"/home/frappe/frappe-bench/sites/assets/$APP_NAME" -type f -exec chmod 0644 '{}' '+'
fi
sync_frontend_dependency_assets

if (( RESTART )); then
	docker restart "${containers[@]}" >/dev/null
	if [[ -x "$VOLUME_ROOT/sshmount_docker_backend.sh" ]]; then
		(cd /tmp && "$VOLUME_ROOT/sshmount_docker_backend.sh") || \
			echo "Warning: portal deployed, but the optional backend SSHFS remount failed." >&2
	fi
else
	echo "Runtime containers were not restarted. Use --restart only in a controlled window."
fi

echo "ccd_portal deployed to $SITE without changing its existing feature-flag state."
echo "The site-local HMAC secret was preserved or generated without printing it."
echo "Configure environment-owned governance records before policy activation and index generation."
echo "Persistent recovery source: $PERSISTENT_APP"
echo "Recovery command after container recreation: $VOLUME_ROOT/deploy_ccd_portal.sh --runtime-only"
