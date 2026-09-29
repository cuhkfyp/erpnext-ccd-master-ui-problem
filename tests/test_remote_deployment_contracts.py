from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RemoteDeploymentContractTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.deploy_path = ROOT / "deployment" / "deploy_remote.sh"
		cls.wrapper_path = ROOT / "deployment" / "Deploy_CCD_Portal.sh"
		cls.deploy = cls.deploy_path.read_text(encoding="utf-8")
		cls.wrapper = cls.wrapper_path.read_text(encoding="utf-8")

	def test_shell_entry_points_parse(self):
		for path in (self.deploy_path, self.wrapper_path):
			result = subprocess.run(
				["bash", "-n", str(path)],
				check=False,
				capture_output=True,
				text=True,
			)
			self.assertEqual(result.returncode, 0, result.stderr)

		remote = self.deploy.split("<<'REMOTE_INSTALL'", 1)[1].split("\nREMOTE_INSTALL\n", 1)[0]
		result = subprocess.run(
			["bash", "-n", "/dev/stdin"],
			input=remote,
			check=False,
			capture_output=True,
			text=True,
		)
		self.assertEqual(result.returncode, 0, result.stderr)

	def test_release_is_pinned_and_gated(self):
		for token in (
			"CCD_PORTAL_SOURCE_COMMIT",
			"CCD_PORTAL_DEVELOPMENT_ACCEPTED",
			"CCD_PORTAL_PRIVATE_SECURITY_GATE_APPROVED",
			"CCD_PORTAL_UAT_ACCEPTANCE_APPROVED",
			"CCD_PORTAL_PRODUCTION_CHANGE_ID",
			"Production deployment from a dirty tree is forbidden",
		):
			self.assertIn(token, self.deploy)

	def test_payload_is_code_only_and_target_configuration_stays_local(self):
		self.assertIn("payload=ccd_portal_application_code_and_prebuilt_assets_only", self.deploy)
		self.assertIn("development_database_included=no", self.deploy)
		self.assertIn("development_site_files_included=no", self.deploy)
		self.assertIn("environment_configuration_included=no", self.deploy)
		self.assertIn("credentials_included=no", self.deploy)
		self.assertNotIn("ensure_development_administrator >/dev/null", self.deploy)
		self.assertNotIn("frappe_nginx_current", self.deploy)
		self.assertNotIn("nginx -s", self.deploy)

	def test_target_is_backed_up_and_kept_disabled(self):
		for token in (
			"backup --with-files --compress",
			"gzip -t",
			"tar -tzf",
			"install-app ccd_portal",
			'bench --site "$site" migrate',
			"ccd_portal.install.ensure_site_secret",
			"Target portal became enabled after preflight",
			"Portal feature flag is enabled after migration",
			"maintenance mode remains enabled",
		):
			self.assertIn(token, self.deploy)

	def test_runtime_scope_is_bounded(self):
		for name in (
			"TARGET_BACKEND_CONTAINER",
			"TARGET_SCHEDULER_CONTAINER",
			"TARGET_QUEUE_LONG_CONTAINER",
			"TARGET_QUEUE_SHORT_CONTAINER",
			"TARGET_FRONTEND_CONTAINER",
		):
			self.assertIn(name, self.deploy)
		self.assertIn('docker restart "${containers[@]}"', self.deploy)
		self.assertNotIn('docker restart "$frontend_container"', self.deploy)
		self.assertNotIn("docker compose down", self.deploy)

	def test_container_recreation_has_a_target_local_recovery_path(self):
		recovery_path = ROOT / "deployment" / "recover_remote_runtime.sh"
		recovery = recovery_path.read_text(encoding="utf-8")
		result = subprocess.run(
			["bash", "-n", str(recovery_path)],
			check=False,
			capture_output=True,
			text=True,
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertIn("ccd_portal_runtime_recovery.sh", self.deploy)
		self.assertIn("--restore", recovery)
		self.assertIn('docker restart "${containers[@]}"', recovery)
		self.assertNotIn('docker restart "$FRONTEND_CONTAINER"', recovery)
		self.assertNotIn("bench --site \"$SITE\" migrate", recovery)
		self.assertNotIn("ensure_site_secret", recovery)
		self.assertNotIn("nginx -", recovery)
		self.assertNotIn("frappe_nginx_current", recovery)

	def test_stable_wrapper_is_installed_by_development_deploy(self):
		development = (ROOT / "deployment" / "deploy_development.sh").read_text(encoding="utf-8")
		self.assertIn('Deploy_CCD_Portal.sh" "$VOLUME_ROOT/Deploy_CCD_Portal.sh', development)
		self.assertIn("deploy_remote.sh", self.wrapper)


if __name__ == "__main__":
	unittest.main()
