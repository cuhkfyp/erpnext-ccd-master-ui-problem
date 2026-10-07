#!/usr/bin/env bash
# Promote a pinned CCD Portal application revision to UAT or Production.
# Only application code and prebuilt public assets are transferred. Target
# data, site files, configuration, users, grants, policies, and secrets stay on
# the target server.
set -Eeuo pipefail

TARGET_ENV="${CCD_PORTAL_TARGET_ENV:-uat}"
TARGET_HOST="${CCD_PORTAL_TARGET_HOST:-}"
TARGET_USER="${CCD_PORTAL_TARGET_USER:-root}"
TARGET_PORT="${CCD_PORTAL_TARGET_PORT:-22}"
TARGET_SITE="${CCD_PORTAL_TARGET_SITE:-frontend}"
TARGET_ROOT="${CCD_PORTAL_TARGET_ROOT:-/root/erpnext_docker_volume}"
TARGET_BACKEND_CONTAINER="${CCD_PORTAL_TARGET_BACKEND_CONTAINER:-frappe_docker-backend-1}"
TARGET_SCHEDULER_CONTAINER="${CCD_PORTAL_TARGET_SCHEDULER_CONTAINER:-frappe_docker-scheduler-1}"
TARGET_QUEUE_LONG_CONTAINER="${CCD_PORTAL_TARGET_QUEUE_LONG_CONTAINER:-frappe_docker-queue-long-1}"
TARGET_QUEUE_SHORT_CONTAINER="${CCD_PORTAL_TARGET_QUEUE_SHORT_CONTAINER:-frappe_docker-queue-short-1}"
TARGET_FRONTEND_CONTAINER="${CCD_PORTAL_TARGET_FRONTEND_CONTAINER:-frappe_docker-frontend-1}"
SOURCE_ROOT="${CCD_PORTAL_SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SOURCE_COMMIT="${CCD_PORTAL_SOURCE_COMMIT:-}"
LOCAL_SITE="${CCD_PORTAL_LOCAL_SITE:-frontend}"
LOCAL_BACKEND_CONTAINER="${CCD_PORTAL_LOCAL_BACKEND_CONTAINER:-frappe_docker-backend-1}"
LOCAL_DOCKER_BIN="${CCD_PORTAL_LOCAL_DOCKER_BIN:-docker}"
PASSWORD_FILE="${ERPNEXT_SSH_PASSWORD_FILE:-/root/pass.txt}"
MIN_FREE_GIB="${CCD_PORTAL_MIN_FREE_GIB:-4}"
MODE="preflight"
ASSUME_YES=0
ALLOW_DIRTY=0

usage() {
	cat <<'USAGE'
Usage:
  Deploy_CCD_Portal.sh --preflight
  Deploy_CCD_Portal.sh --deploy [--yes] [--allow-dirty]

Actions:
  --preflight    Run read-only local/target compatibility and capacity checks.
                 This is the default and does not transfer or change anything.
  --deploy       Back up the target, transfer the pinned application, migrate,
                 install prebuilt assets, and restart only the four Python
                 runtime containers.
  --yes          Skip the final typed confirmation. Production additionally
                 requires CCD_PORTAL_PRODUCTION_NONINTERACTIVE_APPROVED=1.
  --allow-dirty  Allow an explicitly uncommitted UAT rehearsal. Never allowed
                 for Production.

Required target variables:
  CCD_PORTAL_TARGET_ENV=uat|production
  CCD_PORTAL_TARGET_HOST=<hostname-or-address>

Required for every deployment:
  CCD_PORTAL_SOURCE_COMMIT=<full reviewed git SHA>
  CCD_PORTAL_DEVELOPMENT_ACCEPTED=1
  CCD_PORTAL_PRIVATE_SECURITY_GATE_APPROVED=1

Production additionally requires:
  CCD_PORTAL_UAT_ACCEPTANCE_APPROVED=1
  CCD_PORTAL_PRODUCTION_CHANGE_ID=<approved change/reference>
  CCD_PORTAL_PRODUCTION_NONINTERACTIVE_APPROVED=1  (only with --yes)

Optional target overrides:
  CCD_PORTAL_TARGET_USER, CCD_PORTAL_TARGET_PORT, CCD_PORTAL_TARGET_SITE,
  CCD_PORTAL_TARGET_ROOT, CCD_PORTAL_TARGET_BACKEND_CONTAINER,
  CCD_PORTAL_TARGET_SCHEDULER_CONTAINER,
  CCD_PORTAL_TARGET_QUEUE_LONG_CONTAINER,
  CCD_PORTAL_TARGET_QUEUE_SHORT_CONTAINER,
  CCD_PORTAL_TARGET_FRONTEND_CONTAINER, ERPNEXT_SSH_PASSWORD_FILE
  CCD_PORTAL_MIN_FREE_GIB (default: 4)

Optional local overrides:
  CCD_PORTAL_SOURCE_ROOT, CCD_PORTAL_LOCAL_SITE,
  CCD_PORTAL_LOCAL_BACKEND_CONTAINER, CCD_PORTAL_LOCAL_DOCKER_BIN

The target portal feature flag must be disabled before an upgrade and remains
disabled after deployment. The script never copies Development database rows,
site_config, site files, credentials, centre mappings, profiles, grants,
policies, audit events, or search indexes. It also does not change target nginx
or the general Desk routes.
USAGE
}

while (($#)); do
	case "$1" in
		--preflight) MODE="preflight" ;;
		--deploy) MODE="deploy" ;;
		--yes) ASSUME_YES=1 ;;
		--allow-dirty) ALLOW_DIRTY=1 ;;
		-h|--help) usage; exit 0 ;;
		*) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
	esac
	shift
done

log() {
	printf '[CCD Portal %s] %s\n' "$TARGET_ENV" "$*"
}

die() {
	printf '[CCD Portal %s] ERROR: %s\n' "$TARGET_ENV" "$*" >&2
	exit 1
}

require_command() {
	command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

case "$TARGET_ENV" in
	uat|production) ;;
	*) die "CCD_PORTAL_TARGET_ENV must be uat or production" ;;
esac
[[ -n "$TARGET_HOST" ]] || die "CCD_PORTAL_TARGET_HOST is required"
[[ "$TARGET_PORT" =~ ^[0-9]+$ ]] || die "CCD_PORTAL_TARGET_PORT must be numeric"
[[ "$MIN_FREE_GIB" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "CCD_PORTAL_MIN_FREE_GIB must be numeric"
[[ "$TARGET_SITE" =~ ^[A-Za-z0-9._-]+$ ]] || die "Invalid target site name"
for container_name in \
	"$TARGET_BACKEND_CONTAINER" "$TARGET_SCHEDULER_CONTAINER" \
	"$TARGET_QUEUE_LONG_CONTAINER" "$TARGET_QUEUE_SHORT_CONTAINER" \
	"$TARGET_FRONTEND_CONTAINER"; do
	[[ "$container_name" =~ ^[A-Za-z0-9_.-]+$ ]] || die "Invalid target container name"
done
[[ "$TARGET_ROOT" == /* && "$TARGET_ROOT" != "/" ]] || die "CCD_PORTAL_TARGET_ROOT must be a bounded absolute path"

if [[ "$MODE" == "deploy" ]]; then
	[[ -n "$SOURCE_COMMIT" ]] || die "CCD_PORTAL_SOURCE_COMMIT is required for deployment"
	[[ "$SOURCE_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || die "CCD_PORTAL_SOURCE_COMMIT must be a full 40-character git SHA"
	[[ "${CCD_PORTAL_DEVELOPMENT_ACCEPTED:-0}" == "1" ]] || \
		die "Set CCD_PORTAL_DEVELOPMENT_ACCEPTED=1 after Development acceptance"
	[[ "${CCD_PORTAL_PRIVATE_SECURITY_GATE_APPROVED:-0}" == "1" ]] || \
		die "The private security-readiness gate must pass before UAT or Production deployment"
fi
if [[ "$TARGET_ENV" == "production" && "$MODE" == "deploy" ]]; then
	[[ "$ALLOW_DIRTY" == "0" ]] || die "Production deployment from a dirty tree is forbidden"
	[[ "${CCD_PORTAL_UAT_ACCEPTANCE_APPROVED:-0}" == "1" ]] || \
		die "Production requires CCD_PORTAL_UAT_ACCEPTANCE_APPROVED=1"
	[[ -n "${CCD_PORTAL_PRODUCTION_CHANGE_ID:-}" ]] || \
		die "Production requires CCD_PORTAL_PRODUCTION_CHANGE_ID"
	if [[ "$ASSUME_YES" == "1" && "${CCD_PORTAL_PRODUCTION_NONINTERACTIVE_APPROVED:-0}" != "1" ]]; then
		die "Non-interactive Production deployment requires CCD_PORTAL_PRODUCTION_NONINTERACTIVE_APPROVED=1"
	fi
fi

for command_name in ssh scp rsync tar sha256sum mktemp awk grep git python3 find tail du date; do
	require_command "$command_name"
done
if [[ "$LOCAL_DOCKER_BIN" == */* ]]; then
	[[ -x "$LOCAL_DOCKER_BIN" ]] || die "Docker executable is not usable: $LOCAL_DOCKER_BIN"
else
	require_command "$LOCAL_DOCKER_BIN"
fi

[[ -f "$SOURCE_ROOT/pyproject.toml" ]] || die "Missing $SOURCE_ROOT/pyproject.toml"
[[ -f "$SOURCE_ROOT/ccd_portal/hooks.py" ]] || die "Missing ccd_portal/hooks.py"
[[ -f "$SOURCE_ROOT/ccd_portal/www/ccd_portal.html" ]] || die "Missing portal web entry point"
[[ -f "$SOURCE_ROOT/scripts/scan_repository.py" ]] || die "Missing repository scanner"
asset_root="$SOURCE_ROOT/ccd_portal/public/ccd-portal/assets"
[[ -d "$asset_root" ]] || die "Missing prebuilt portal assets"

mapfile -t built_javascript < <(find "$asset_root" -maxdepth 1 -type f -name '*.js' -print)
mapfile -t built_stylesheets < <(find "$asset_root" -maxdepth 1 -type f -name '*.css' -print)
mapfile -t source_maps < <(find "$asset_root" -maxdepth 1 -type f -name '*.map' -print)
(( ${#built_javascript[@]} == 1 )) || die "Expected exactly one prebuilt portal JavaScript bundle"
(( ${#built_stylesheets[@]} == 1 )) || die "Expected exactly one prebuilt portal stylesheet"
(( ${#source_maps[@]} == 0 )) || die "Source maps must not be promoted"

SSH_OPTIONS=(
	-p "$TARGET_PORT"
	-o StrictHostKeyChecking=accept-new
	-o ConnectTimeout=15
	-o ServerAliveInterval=15
	-o ServerAliveCountMax=4
)
SCP_OPTIONS=(
	-P "$TARGET_PORT"
	-o StrictHostKeyChecking=accept-new
	-o ConnectTimeout=15
)
if [[ -r "$PASSWORD_FILE" ]]; then
	require_command sshpass
	SSH=(sshpass -f "$PASSWORD_FILE" ssh "${SSH_OPTIONS[@]}")
	SCP=(sshpass -f "$PASSWORD_FILE" scp "${SCP_OPTIONS[@]}")
else
	SSH=(ssh "${SSH_OPTIONS[@]}")
	SCP=(scp "${SCP_OPTIONS[@]}")
fi
REMOTE="$TARGET_USER@$TARGET_HOST"

remote_run() {
	local remote_command
	if (( $# == 1 )); then
		remote_command="$1"
	else
		printf -v remote_command '%q ' "$@"
	fi
	"${SSH[@]}" "$REMOTE" "$remote_command"
}

local_apps() {
	"$LOCAL_DOCKER_BIN" exec "$LOCAL_BACKEND_CONTAINER" \
		bench --site "$LOCAL_SITE" list-apps
}

remote_apps() {
	remote_run docker exec "$TARGET_BACKEND_CONTAINER" \
		bench --site "$TARGET_SITE" list-apps
}

remote_installed_apps() {
	remote_run docker exec "$TARGET_BACKEND_CONTAINER" \
		bench --site "$TARGET_SITE" execute frappe.get_installed_apps
}

app_version() {
	local app="$1"
	awk -v app="$app" '$1 == app { print $2; exit }'
}

remote_doctype_exists() {
	local doctype="$1" output
	output="$(remote_run docker exec "$TARGET_BACKEND_CONTAINER" \
		bench --site "$TARGET_SITE" execute frappe.client.get_count \
		--kwargs "{\"doctype\":\"DocType\",\"filters\":{\"name\":\"$doctype\"}}" | tail -n 1)"
	[[ "${output//$'\r'/}" == "1" ]]
}

validate_local_source() {
	log "Validating the local source and prebuilt assets"
	python3 "$SOURCE_ROOT/scripts/scan_repository.py"
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
		-s "$SOURCE_ROOT/tests" -p 'test_*.py' >/dev/null
	git -C "$SOURCE_ROOT" rev-parse --verify HEAD >/dev/null
	"$LOCAL_DOCKER_BIN" inspect "$LOCAL_BACKEND_CONTAINER" >/dev/null
	local local_list
	local_list="$(local_apps)"
	grep -Eq '^ccd_portal[[:space:]]' <<<"$local_list" || \
		die "ccd_portal is not installed on the Development site"
}

preflight() {
	local local_list remote_list installed_list local_version remote_version target_enabled target_same_person_enabled
	validate_local_source
	log "Running read-only target preflight for $REMOTE ($TARGET_SITE)"
	local_list="$(local_apps)"
	remote_run "set -eu
		test -d '$TARGET_ROOT'
		command -v docker >/dev/null
		command -v rsync >/dev/null
		command -v flock >/dev/null
		command -v sha256sum >/dev/null
		command -v tar >/dev/null
		command -v gzip >/dev/null
		command -v find >/dev/null
		command -v grep >/dev/null
		command -v tail >/dev/null
		command -v seq >/dev/null
		command -v date >/dev/null
		command -v install >/dev/null
		command -v python3 >/dev/null
		for container in '$TARGET_BACKEND_CONTAINER' '$TARGET_SCHEDULER_CONTAINER' '$TARGET_QUEUE_LONG_CONTAINER' '$TARGET_QUEUE_SHORT_CONTAINER' '$TARGET_FRONTEND_CONTAINER'; do
			docker inspect \"\$container\" >/dev/null
		done
		docker exec '$TARGET_BACKEND_CONTAINER' test -d '/home/frappe/frappe-bench/sites/$TARGET_SITE'
		available_kb=\$(df -Pk '$TARGET_ROOT' | awk 'NR == 2 {print \$4}')
		public_kb=\$(docker exec '$TARGET_BACKEND_CONTAINER' du -sk '/home/frappe/frappe-bench/sites/$TARGET_SITE/public' | awk '{print \$1}')
		private_kb=\$(docker exec '$TARGET_BACKEND_CONTAINER' du -sk '/home/frappe/frappe-bench/sites/$TARGET_SITE/private/files' | awk '{print \$1}')
		persistent_kb=0
		if test -d '$TARGET_ROOT/persistent_apps/ccd_portal'; then
			persistent_kb=\$(du -sk '$TARGET_ROOT/persistent_apps/ccd_portal' | awk '{print \$1}')
		fi
		required_kb=\$((public_kb + private_kb + persistent_kb + 1048576))
		minimum_kb=\$(awk -v gib='$MIN_FREE_GIB' 'BEGIN {printf \"%.0f\", gib*1048576}')
		if [ \"\$required_kb\" -lt \"\$minimum_kb\" ]; then required_kb=\$minimum_kb; fi
		awk -v available=\"\$available_kb\" -v required=\"\$required_kb\" 'BEGIN {printf \"Target deployment-space check: %.2f GiB available; %.2f GiB conservatively required\\n\", available/1048576, required/1048576}'
		if [ \"\$available_kb\" -lt \"\$required_kb\" ]; then
			echo 'Target does not have enough free disk for a guarded full-backup deployment.' >&2
			exit 42
		fi"
	remote_list="$(remote_apps)"
	installed_list="$(remote_installed_apps)"

	for framework_app in frappe erpnext; do
		local_version="$(app_version "$framework_app" <<<"$local_list")"
		remote_version="$(app_version "$framework_app" <<<"$remote_list")"
		[[ -n "$local_version" && "$local_version" == "$remote_version" ]] || \
			die "$framework_app version mismatch: Development=$local_version target=$remote_version"
		log "$framework_app version matches: $local_version"
	done

	for required_doctype in "CCD Master" "CCD Registration"; do
		remote_doctype_exists "$required_doctype" || \
			die "Target is missing required custom DocType: $required_doctype"
		log "Target DocType present: $required_doctype"
	done

	if grep -Eq '(^|["'"'"'[:space:],])ccd_portal(["'"'"'[:space:],]|$)' <<<"$installed_list"; then
		target_enabled="$(remote_run docker exec "$TARGET_BACKEND_CONTAINER" \
			bench --site "$TARGET_SITE" execute frappe.db.get_single_value \
			--args '["CCD Portal Settings","enabled"]' | tail -n 1)"
		target_enabled="${target_enabled//$'\r'/}"
		[[ "$target_enabled" != "1" ]] || \
			die "Target CCD Portal is enabled. Disable it before deploying code or migrations."
		target_same_person_enabled="$(remote_run docker exec "$TARGET_BACKEND_CONTAINER" \
			bench --site "$TARGET_SITE" execute frappe.db.get_single_value \
			--args '["CCD Portal Settings","same_person_enabled"]' | tail -n 1)"
		target_same_person_enabled="${target_same_person_enabled//$'\r'/}"
		[[ "$target_same_person_enabled" != "1" ]] || \
			die "Target same-person rollout is enabled. Disable it before deploying code or migrations."
		log "Target upgrade detected; portal feature flag is disabled"
	else
		log "Target first installation detected; the portal will be installed disabled"
	fi

	current_revision="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
	log "Source revision available for pinning: $current_revision"
	log "Preflight passed. No target state was changed and no Development data was read for transfer."
}

preflight
[[ "$MODE" == "deploy" ]] || exit 0

git_revision="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
[[ "${git_revision,,}" == "${SOURCE_COMMIT,,}" ]] || \
	die "Pinned source mismatch: requested=$SOURCE_COMMIT current=$git_revision"
git_status="$(git -C "$SOURCE_ROOT" status --porcelain 2>/dev/null || true)"
if [[ -n "$git_status" && "$ALLOW_DIRTY" != "1" ]]; then
	die "The source tree is dirty. Commit the intended revision, or use --allow-dirty for an explicit UAT rehearsal."
fi
if [[ -n "$git_status" ]]; then
	[[ "$TARGET_ENV" == "uat" ]] || die "Dirty deployments are limited to UAT"
	log "WARNING: packaging an explicitly allowed dirty UAT working tree"
fi

if [[ "$ASSUME_YES" != "1" ]]; then
	confirmation_token="DEPLOY-CCD-PORTAL-${TARGET_ENV^^}"
	printf 'Deploy pinned CCD Portal code to %s? Type %s: ' "$REMOTE" "$confirmation_token"
	read -r confirmation
	[[ "$confirmation" == "$confirmation_token" ]] || die "Confirmation did not match; nothing was deployed"
fi

work_dir="$(mktemp -d /tmp/ccd-portal-remote.XXXXXX)"
cleanup() {
	rm -rf -- "$work_dir"
}
trap cleanup EXIT

release_id="$(date -u +%Y%m%dT%H%M%SZ)-${git_revision:0:12}"
mkdir -p "$work_dir/app"
rsync -a --delete \
	--exclude='.git/' \
	--exclude='frontend/node_modules/' \
	--exclude='**/__pycache__/' \
	--exclude='*.py[co]' \
	--exclude='.pytest_cache/' \
	--exclude='.ruff_cache/' \
	--exclude='.mypy_cache/' \
	--exclude='.env' \
	--exclude='.env.*' \
	--exclude='*.env' \
	--exclude='*.log' \
	--exclude='*.sql*' \
	--exclude='*.dump' \
	--exclude='*.bak' \
	--exclude='*.backup' \
	--exclude='*.tar' \
	--exclude='*.tar.gz' \
	--exclude='*.tgz' \
	--exclude='*.zip' \
	--exclude='*.csv' \
	--exclude='*.xlsx' \
	--exclude='testing_for_the_agent/' \
	"$SOURCE_ROOT/" "$work_dir/app/"

[[ -f "$work_dir/app/pyproject.toml" ]] || die "Packaged app is missing pyproject.toml"
[[ -f "$work_dir/app/ccd_portal/hooks.py" ]] || die "Packaged app is missing hooks.py"
[[ -f "$work_dir/app/ccd_portal/www/ccd_portal.html" ]] || die "Packaged app is missing the web entry point"
[[ -d "$work_dir/app/ccd_portal/public/ccd-portal/assets" ]] || die "Packaged app is missing public assets"

cat > "$work_dir/manifest.txt" <<EOF
release_id=$release_id
created_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
source_git_revision=$git_revision
source_working_tree_dirty=$([[ -n "$git_status" ]] && printf yes || printf no)
target_environment=$TARGET_ENV
payload=ccd_portal_application_code_and_prebuilt_assets_only
development_database_included=no
development_site_files_included=no
environment_configuration_included=no
credentials_included=no
EOF

archive="$work_dir/ccd-portal-$release_id.tar.gz"
tar -C "$work_dir" -czf "$archive" app manifest.txt
archive_sha="$(sha256sum "$archive" | awk '{print $1}')"
printf '%s  %s\n' "$archive_sha" "$(basename "$archive")" > "$archive.sha256"

cat > "$work_dir/install_on_target.sh" <<'REMOTE_INSTALL'
#!/usr/bin/env bash
set -Eeuo pipefail

archive="$1"
expected_sha="$2"
release_id="$3"
target_env="$4"
target_root="$5"
site="$6"
backend_container="$7"
frontend_container="$8"
scheduler_container="$9"
queue_long_container="${10}"
queue_short_container="${11}"
source_revision="${12}"

deploy_root="$target_root/.ccd-portal-deploy"
release_root="$deploy_root/releases/$release_id"
backup_root="$deploy_root/backups"
persistent_app="$target_root/persistent_apps/ccd_portal"
app_in_container="/home/frappe/frappe-bench/apps/ccd_portal"
pth_in_container="/home/frappe/frappe-bench/env/lib/python3.11/site-packages/ccd_portal.pth"
apps_txt="/home/frappe/frappe-bench/sites/apps.txt"
frontend_assets="/home/frappe/frappe-bench/sites/assets/ccd_portal"
frontend_incoming="${frontend_assets}.__incoming__.${release_id}"
frontend_previous="${frontend_assets}.__previous__.${release_id}"
incoming_app="${app_in_container}.__incoming__.${release_id}"
previous_app="${app_in_container}.__previous__.${release_id}"
containers=(
	"$backend_container"
	"$scheduler_container"
	"$queue_long_container"
	"$queue_short_container"
)
maintenance_enabled=0
containers_swapped=0
persistent_changed=0
schema_mutation_started=0
initially_installed=0
apps_txt_backed_up=0
runtime_registration_changed=0
persistent_backup=""
backup_dir="/home/frappe/frappe-bench/sites/$site/private/backups"

mkdir -p "$release_root" "$backup_root"
exec 9>"$deploy_root/deploy.lock"
flock -n 9 || { echo "Another CCD Portal deployment is already running." >&2; exit 60; }

finish() {
	status=$?
	trap - EXIT
	set +e
	if [[ "$status" != "0" ]]; then
		echo "CCD Portal deployment failed with status $status." >&2
		if [[ "$schema_mutation_started" == "0" ]]; then
			if [[ "$containers_swapped" == "1" ]]; then
				for container in "${containers[@]}"; do
					docker exec -u root "$container" sh -c '
						app="$1"; previous="$2"; initially_installed="$3"
						if [ -e "$previous" ]; then
							rm -rf -- "$app"
							mv "$previous" "$app"
						elif [ "$initially_installed" = 0 ]; then
							rm -rf -- "$app"
						fi
					' sh "$app_in_container" "$previous_app" "$initially_installed"
				done
			fi
			if [[ "$persistent_changed" == "1" ]]; then
				if [[ -n "$persistent_backup" && -f "$persistent_backup" ]]; then
					rm -rf -- "$persistent_app"
					tar -C "$(dirname "$persistent_app")" -xzf "$persistent_backup"
				elif [[ "$initially_installed" == "0" ]]; then
					rm -rf -- "$persistent_app"
				fi
			fi
			if [[ "$initially_installed" == "0" && "$apps_txt_backed_up" == "1" ]]; then
				docker cp "$release_root/apps.txt.before" "$backend_container:$apps_txt"
				docker exec -u root "$backend_container" chown frappe:frappe "$apps_txt"
			fi
			if [[ "$initially_installed" == "0" && "$runtime_registration_changed" == "1" ]]; then
				for container in "${containers[@]}"; do
					docker exec -u root "$container" rm -f -- "$pth_in_container"
				done
			fi
			if [[ "$maintenance_enabled" == "1" ]]; then
				docker exec "$backend_container" bench --site "$site" set-maintenance-mode off >/dev/null 2>&1
			fi
			echo "Pre-migration changes were rolled back where prior code existed; the verified site backup remains available." >&2
		else
			echo "A schema operation had started; code was retained and maintenance mode remains enabled." >&2
			echo "Restore the verified backup set in $backup_dir before retrying or enabling the site." >&2
		fi
	fi
	exit "$status"
}
trap finish EXIT

actual_sha="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual_sha" == "$expected_sha" ]] || { echo "Release checksum mismatch." >&2; exit 61; }
tar -xzf "$archive" -C "$release_root"
release_app="$release_root/app"
[[ -f "$release_app/pyproject.toml" && -f "$release_app/ccd_portal/hooks.py" ]] || {
	echo "Invalid CCD Portal release payload." >&2
	exit 62
}
	[[ -f "$release_app/ccd_portal/www/ccd_portal.html" ]] || {
	echo "Release payload is missing the portal entry point." >&2
	exit 63
}

for container in "${containers[@]}" "$frontend_container"; do
	docker inspect "$container" >/dev/null
done
docker exec "$backend_container" test -d "/home/frappe/frappe-bench/sites/$site"
if docker exec "$backend_container" bench --site "$site" execute frappe.get_installed_apps | \
	grep -Eq '(^|["'"'"'[:space:],])ccd_portal(["'"'"'[:space:],]|$)'; then
	initially_installed=1
	current_enabled="$(docker exec "$backend_container" bench --site "$site" execute \
		frappe.db.get_single_value --args '["CCD Portal Settings","enabled"]' | tail -n 1)"
	[[ "${current_enabled//$'\r'/}" != "1" ]] || {
		echo "Target portal became enabled after preflight; deployment refused." >&2
		exit 64
	}
	current_same_person_enabled="$(docker exec "$backend_container" bench --site "$site" execute \
		frappe.db.get_single_value --args '["CCD Portal Settings","same_person_enabled"]' | tail -n 1)"
	[[ "${current_same_person_enabled//$'\r'/}" != "1" ]] || {
		echo "Target same-person rollout became enabled after preflight; deployment refused." >&2
		exit 64
	}
fi

echo "Enabling target maintenance mode..."
docker exec "$backend_container" bench --site "$site" set-maintenance-mode on
maintenance_enabled=1

echo "Creating and validating a target-local full backup before migration..."
backup_marker="$backup_dir/.ccd-portal-$release_id.marker"
docker exec "$backend_container" touch "$backup_marker"
docker exec "$backend_container" bench --site "$site" backup --with-files --compress
mapfile -t new_backups < <(docker exec "$backend_container" find "$backup_dir" \
	-maxdepth 1 -type f -newer "$backup_marker" -print)
docker exec "$backend_container" rm -f "$backup_marker"
(( ${#new_backups[@]} >= 4 )) || {
	echo "Expected a full backup set; found only ${#new_backups[@]} new files." >&2
	exit 65
}
for backup_file in "${new_backups[@]}"; do
	case "$backup_file" in
		*.sql.gz) docker exec "$backend_container" gzip -t "$backup_file" ;;
		*.tar.gz|*.tgz) docker exec "$backend_container" tar -tzf "$backup_file" >/dev/null ;;
		*.tar) docker exec "$backend_container" tar -tf "$backup_file" >/dev/null ;;
		*.json) docker exec "$backend_container" python -m json.tool "$backup_file" >/dev/null ;;
		*) echo "Unexpected backup file type: $backup_file" >&2; exit 66 ;;
	esac
	docker exec "$backend_container" sha256sum "$backup_file"
done

docker cp "$backend_container:$apps_txt" "$release_root/apps.txt.before"
apps_txt_backed_up=1
if [[ -d "$persistent_app" ]]; then
	persistent_backup="$backup_root/ccd_portal-code-before-$release_id.tar.gz"
	tar -C "$(dirname "$persistent_app")" -czf "$persistent_backup" "$(basename "$persistent_app")"
	sha256sum "$persistent_backup" > "$persistent_backup.sha256"
fi
mkdir -p "$persistent_app"
rsync -a --delete "$release_app/" "$persistent_app/"
persistent_changed=1

echo "Staging the complete application in all Python runtime containers..."
runtime_registration_changed=1
for container in "${containers[@]}"; do
	docker exec -u root "$container" rm -rf -- "$incoming_app" "$previous_app"
	docker exec -u root "$container" mkdir -p "$incoming_app"
	docker cp "$persistent_app/." "$container:$incoming_app/"
	docker exec "$container" test -f "$incoming_app/pyproject.toml"
	docker exec "$container" test -f "$incoming_app/ccd_portal/hooks.py"
	docker exec -u root "$container" chown -R frappe:frappe "$incoming_app"
done

swapped=()
for container in "${containers[@]}"; do
	if docker exec -u root "$container" sh -c '
		set -eu
		app="$1"; incoming="$2"; previous="$3"
		if [ -e "$app" ]; then mv "$app" "$previous"; fi
		if ! mv "$incoming" "$app"; then
			if [ -e "$previous" ]; then mv "$previous" "$app"; fi
			exit 1
		fi
	' sh "$app_in_container" "$incoming_app" "$previous_app"; then
		swapped+=("$container")
	else
		for changed_container in "${swapped[@]}"; do
			docker exec -u root "$changed_container" sh -c \
				'rm -rf -- "$1"; if [ -e "$2" ]; then mv "$2" "$1"; fi' \
				sh "$app_in_container" "$previous_app" || true
		done
		exit 67
	fi
done
containers_swapped=1

for container in "${containers[@]}"; do
	docker exec -u root "$container" sh -c \
		'printf "%s\n" "$1" > "$2"; chown frappe:frappe "$2"' \
		sh "$app_in_container" "$pth_in_container"
done
if ! docker exec "$backend_container" grep -qx ccd_portal "$apps_txt"; then
	docker exec "$backend_container" sh -c '
		file="$1"; tmp="${file}.ccd-portal.$$"
		awk "1" "$file" > "$tmp"
		printf "%s\n" ccd_portal >> "$tmp"
		mv "$tmp" "$file"
	' sh "$apps_txt"
fi

schema_mutation_started=1
if [[ "$initially_installed" == "0" ]]; then
	echo "Installing CCD Portal on $target_env for the first time..."
	docker exec "$backend_container" bench --site "$site" install-app ccd_portal
fi
echo "Migrating the target site..."
docker exec "$backend_container" bench --site "$site" migrate
docker exec "$backend_container" bench --site "$site" execute ccd_portal.install.ensure_site_secret >/dev/null

echo "Checking governed identity schema readiness (informational while V2 is disabled)..."
docker exec "$backend_container" bench --site "$site" execute ccd_portal.identity.schema_status

enabled_after="$(docker exec "$backend_container" bench --site "$site" execute \
	frappe.db.get_single_value --args '["CCD Portal Settings","enabled"]' | tail -n 1)"
[[ "${enabled_after//$'\r'/}" != "1" ]] || {
	echo "Portal feature flag is enabled after migration; refusing to continue." >&2
	exit 68
}
same_person_after="$(docker exec "$backend_container" bench --site "$site" execute \
	frappe.db.get_single_value --args '["CCD Portal Settings","same_person_enabled"]' | tail -n 1)"
[[ "${same_person_after//$'\r'/}" != "1" ]] || {
	echo "Same-person rollout flag is enabled after migration; refusing to continue." >&2
	exit 68
}

runtime_recovery="$target_root/ccd_portal_runtime_recovery.sh"
install -m 0755 "$persistent_app/deployment/recover_remote_runtime.sh" "$runtime_recovery"

echo "Installing reviewed prebuilt portal assets without changing nginx routes..."
docker exec -u root "$frontend_container" rm -rf -- "$frontend_incoming" "$frontend_previous"
docker exec -u root "$frontend_container" install -d -m 0755 -o frappe -g frappe "$frontend_incoming"
docker cp "$persistent_app/ccd_portal/public/." "$frontend_container:$frontend_incoming/"
docker exec -u root "$frontend_container" chown -R frappe:frappe "$frontend_incoming"
docker exec -u root "$frontend_container" find "$frontend_incoming" -type d -exec chmod 0755 '{}' '+'
docker exec -u root "$frontend_container" find "$frontend_incoming" -type f -exec chmod 0644 '{}' '+'
docker exec -u root "$frontend_container" sh -c '
	set -eu
	current="$1"; incoming="$2"; previous="$3"
	if [ -e "$current" ]; then mv "$current" "$previous"; fi
	if ! mv "$incoming" "$current"; then
		if [ -e "$previous" ]; then mv "$previous" "$current"; fi
		exit 1
	fi
' sh "$frontend_assets" "$frontend_incoming" "$frontend_previous"

docker exec "$backend_container" bench --site "$site" clear-cache
echo "Restarting only backend, scheduler, queue-long, and queue-short..."
docker restart "${containers[@]}" >/dev/null
for container in "${containers[@]}"; do
	for _attempt in $(seq 1 60); do
		state="$(docker inspect -f '{{.State.Status}}' "$container")"
		[[ "$state" == "running" ]] && break
		sleep 1
	done
	[[ "${state:-}" == "running" ]] || { echo "Container did not return: $container" >&2; exit 69; }
	docker exec "$container" python -c 'import ccd_portal, ccd_portal.hooks'
	docker exec "$container" grep -qx "$app_in_container" "$pth_in_container"
done
docker exec "$backend_container" bench --site "$site" list-apps | grep -Eq '^ccd_portal[[:space:]]'
docker exec "$frontend_container" test -f "$frontend_assets/ccd-portal/index.html"

docker exec "$backend_container" bench --site "$site" set-maintenance-mode off
maintenance_enabled=0
docker exec -u root "$frontend_container" rm -rf -- "$frontend_previous"
for container in "${containers[@]}"; do
	docker exec -u root "$container" rm -rf -- "$previous_app"
done

{
	printf 'release_id=%s\n' "$release_id"
	printf 'deployed_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	printf 'source_git_revision=%s\n' "$source_revision"
	printf 'target_environment=%s\n' "$target_env"
	printf 'portal_enabled=0\n'
	printf 'same_person_enabled=0\n'
	printf 'development_data_transferred=no\n'
	printf 'nginx_routes_changed=no\n'
	printf 'runtime_recovery=%s\n' "$runtime_recovery"
	printf 'backup_files=\n'
	printf '  %s\n' "${new_backups[@]}"
} > "$release_root/deployment-result.txt"

echo "CCD Portal deployment completed: $release_id"
echo "Verified target backup files remain in: $backup_dir"
echo "Code rollback archive: ${persistent_backup:-none-first-install}"
echo "Container-recreation recovery: $runtime_recovery --restore"
echo "The portal feature flag remains disabled; configure target-owned governance and indexes before access."
echo "No Development database, files, configuration, identities, grants, policies, or secrets were transferred."
REMOTE_INSTALL
chmod 0755 "$work_dir/install_on_target.sh"

remote_incoming="$TARGET_ROOT/.ccd-portal-deploy/incoming/$release_id"
remote_run mkdir -p "$remote_incoming"
log "Uploading code-only release $release_id ($(du -h "$archive" | awk '{print $1}'))"
"${SCP[@]}" "$archive" "$archive.sha256" "$work_dir/install_on_target.sh" \
	"$REMOTE:$remote_incoming/"

log "Starting guarded target installation with a verified full backup"
remote_run bash "$remote_incoming/install_on_target.sh" \
	"$remote_incoming/$(basename "$archive")" "$archive_sha" "$release_id" \
	"$TARGET_ENV" "$TARGET_ROOT" "$TARGET_SITE" "$TARGET_BACKEND_CONTAINER" \
	"$TARGET_FRONTEND_CONTAINER" "$TARGET_SCHEDULER_CONTAINER" \
	"$TARGET_QUEUE_LONG_CONTAINER" "$TARGET_QUEUE_SHORT_CONTAINER" "$git_revision"

log "Deployment complete. Run target-owned index generation and the two-centre acceptance matrix before enabling access."
