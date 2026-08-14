app_name = "ccd_portal"
app_title = "CCD Portal"
app_publisher = "HKSR"
app_description = "Governed staff portal for targeted CCD Master access"
app_email = "digital@rehabsociety.org.hk"
app_license = "mit"

required_apps = ["frappe", "erpnext"]

after_install = "ccd_portal.install.after_install"
after_migrate = "ccd_portal.install.after_migrate"

add_to_apps_screen = [
	{
		"name": "ccd_portal",
		"logo": "/assets/ccd_portal/ccd-portal/portal-mark.svg",
		"title": "CCD Portal",
		"route": "/ccd-portal",
		"has_permission": "ccd_portal.security.has_portal_permission",
	}
]

website_route_rules = [
	{"from_route": "/ccd-portal", "to_route": "ccd_portal"},
	{"from_route": "/ccd-portal/<path:app_path>", "to_route": "ccd_portal"},
]

doc_events = {
	"CCD Master": {
		"after_insert": "ccd_portal.sync.on_ccd_master_change",
		"on_update": "ccd_portal.sync.on_ccd_master_change",
		"on_trash": "ccd_portal.sync.on_ccd_master_delete",
	}
}

scheduler_events = {
	"hourly": ["ccd_portal.sync.reconcile_recent_records"],
	"daily": ["ccd_portal.sync.generate_coverage_snapshot"],
}

before_tests = "ccd_portal.install.before_tests"
