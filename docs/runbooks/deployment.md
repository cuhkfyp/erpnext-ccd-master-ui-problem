# Development, UAT, production, and rollback

## Development deployment

1. Record the commit SHA and back up the development site.
2. Run the private security-readiness check. For development, retain the existing
   shared agent behavior as directed, but do not treat it as production-ready.
3. Copy the pinned app source to all Python runtime containers, register its
   local path with the bench interpreter, add it to the site, migrate, and build
   assets. The deployment does not download Python packages at runtime.
4. Generate the centre/search indexes and inspect aggregate coverage.
5. Leave the feature flag disabled. Add only named administrators to preview.
6. Execute automated checks and the administrator masked-data smoke test.

The repository's `deployment/deploy_development.sh` performs the repeatable app
copy/install/migrate/build/cache sequence for the current Docker topology. It
creates the site-local HMAC secret only when missing, without printing it. It
does not configure centres, aliases, grants, ordinary-user identities, activate
the draft policy, generate indexes, or enable the feature flag.

### Docker persistence and recovery

The Python application directories inside the Frappe containers are disposable.
A normal container restart retains them, but image/container recreation may not.
The deployment therefore copies the reviewed application to the host-owned
`/root/erpnext_docker_volume/persistent_apps/ccd_portal` directory and installs
the idempotent host wrapper `/root/erpnext_docker_volume/deploy_ccd_portal.sh`.
The existing ERPNext restart scripts conditionally invoke:

```bash
/root/erpnext_docker_volume/deploy_ccd_portal.sh --runtime-only --restart
```

That restores only `ccd_portal` into backend, scheduler, and both queue workers,
restores the local interpreter path and frontend assets, reloads those Python
workers, and remounts the existing backend SSHFS mount through
`sshmount_docker_backend.sh`. It does not
capture or overwrite `db_connector`, `hksr`, Studio, site data, nginx settings,
VPN settings, or other application code.

The `sites/apps.txt` registration, installed-app row, portal DocTypes, audit
records, corrections, governance configuration, and site-local secret live in
the existing database/sites volumes. They are not copied into Git. After
container recreation, verify the
flag is still disabled, `bench --site frontend list-apps` contains `ccd_portal`,
the four runtime copies exist, and the administrator bootstrap works before any
further action. Never make an unrecorded portal edit only inside a container.

The deployment script does not restart containers unless `--restart` is passed.
Use that option only in a controlled window; it invokes the established SSHFS
remount safeguard afterward. A code/migration staging run without reload is:

```bash
./deployment/deploy_development.sh
```

For documentation, tests, or recovery-script changes that must survive reboot
but must not touch Docker or the site, synchronize only the host recovery copy:

```bash
./deployment/deploy_development.sh --persist-only
```

### Current Docker/SSHFS safeguards

- Treat `backend/apps` as an SSHFS runtime view, not as an editing location.
  Change the reviewed repository or `persistent_apps` source first, then copy
  the exact file and compare hashes. A failed multi-file write across that mount
  can otherwise leave only the first file changed.
- `sites/apps.txt` may originate without a final newline. The deployment uses
  an atomic temporary file plus `awk` before appending `ccd_portal`; a plain
  `printf >>` can concatenate two app names and make scheduler startup fail.
- Snap-packaged Docker cannot necessarily access a host directory created under
  `/tmp`. Portal asset staging is therefore created under
  `/root/erpnext_docker_volume/.ccd-portal-assets.*`, which is visible to Docker,
  and is removed automatically.
- The app is registered through a local `ccd_portal.pth` in each current Frappe
  runtime. This avoids a network-dependent editable install and is recreated by
  `deploy_ccd_portal.sh --runtime-only` after container replacement.
- If an app-registry check fails, inspect and repair `sites/apps.txt` before any
  restart. Confirm that every app is on its own line with `bench --site frontend
  list-apps`; then reload only backend, scheduler, queue-long, and queue-short.
- A controlled code reload affects those four Python runtime containers only.
  Confirm their prior state and the SSHFS mount after the reload; do not restart
  the database, Redis, frontend, websocket, VPN, or unrelated services for a
  portal-only deployment.

#### Duplicate SSHFS mount-layer case

`findmnt -T /root/erpnext_docker_volume/backend` run inside a managed command
sandbox can show more entries than the host view. Never unmount based on that
output alone. Compare `/proc/self/ns/mnt` with PID 1 and inspect PID 1's view:

```bash
readlink /proc/self/ns/mnt
readlink /proc/1/ns/mnt
nsenter -t 1 -m findmnt -rn -T /root/erpnext_docker_volume/backend \
  -o TARGET,SOURCE,FSTYPE,OPTIONS
```

During the 2026-08-14 development deployment, the sandbox showed four entries;
the PID 1 view confirmed two real, readable, read-write SSHFS layers for the same
source. No live unmount was performed. The cause was the older remount script:
multiple `findmnt` source lines failed a scalar equality test, it removed only
one top layer, and then added a replacement.

The reviewed `deployment/sshmount_docker_backend.sh` now treats one or more
identical readable layers as already mounted, so it will not grow that stack.
When the source changes or becomes unreadable, it refuses non-SSHFS layers and
drains every stale SSHFS layer with a bounded, decreasing-count check before
mounting exactly once. The existing duplicate is transient and should disappear
on the next host reboot; after reboot run the PID 1 check and require exactly one
read-write layer. Do not manually collapse a healthy live stack without an
approved maintenance window and rollback access.

## UAT promotion

Promotion is blocked until development acceptance and the private security gate
are signed off. Pin an exact reviewed commit; never deploy a floating branch.

1. Back up the UAT database and sites volume; verify restore instructions.
2. Record currently installed app versions and the prior `ccd_portal` commit.
3. Deploy the pinned commit, migrate, and build.
4. Configure UAT-owned centres, aliases, profiles, grants, identities, URLs, and
   secret through controlled administration—not fixtures or Git.
5. Build indexes and obtain 100% coverage for the selected two-centre cohort.
6. Run the complete [two-centre UAT](../uat/two-centre-acceptance.md).

## Production pilot

After UAT acceptance, take and verify a production backup, deploy the same pinned
commit, and migrate while the flag is disabled. Configure only a named pilot
cohort. Rebuild/validate coverage, enable the flag in an approved window, then
review denial, reveal, coverage, error, and latency measures daily before any
controlled expansion. Unpublish—but retain—the Studio prototype only after
accepted cutover.

## Rollback

1. Disable `CCD Portal Settings.enabled` immediately.
2. Preserve audit/correction records and application logs; do not delete evidence.
3. If code-only rollback is sufficient, deploy the previously recorded commit,
   rebuild assets, clear cache, and restart runtime containers.
4. If the migration must be reversed, stop writes and restore the verified
   pre-deployment database/sites backup. Frappe schema migrations are not assumed
   reversible in place.
5. Re-run the isolation smoke tests before any re-enable. Record the incident and
   rollback decision as governance evidence.

The application does not delete the hidden CCD Master centre field, Studio
records, or audit events during rollback.
