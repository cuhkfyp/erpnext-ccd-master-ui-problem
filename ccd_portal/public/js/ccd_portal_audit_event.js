function show_typed_justification(data) {
	const expiresIn = Number(data.expires_in || 120);
	let timeout;
	const dialog = new frappe.ui.Dialog({
		title: __("User-entered reveal justification"),
		fields: [{ fieldname: "justification", fieldtype: "HTML" }],
		primary_action_label: __("Close and clear"),
		primary_action() {
			dialog.hide();
		},
	});
	const wrapper = dialog.get_field("justification").$wrapper;
	$("<p>")
		.addClass("alert alert-warning")
		.text(
			__("This encrypted justification is temporarily visible and will clear automatically in {0} seconds.", [
				expiresIn,
			])
		)
		.appendTo(wrapper);
	$("<div>")
		.css({ "white-space": "pre-wrap", "overflow-wrap": "anywhere" })
		.text(String(data.context_note || ""))
		.appendTo(wrapper);

	const hideWhenTabIsHidden = () => {
		if (document.hidden) dialog.hide();
	};
	document.addEventListener("visibilitychange", hideWhenTabIsHidden);
	dialog.$wrapper.one("hidden.bs.modal", () => {
		clearTimeout(timeout);
		wrapper.empty();
		document.removeEventListener("visibilitychange", hideWhenTabIsHidden);
	});
	dialog.show();
	timeout = setTimeout(() => dialog.hide(), expiresIn * 1000);
}

frappe.ui.form.on("CCD Portal Audit Event", {
	refresh(frm) {
		frm.disable_save();
		if (frm.is_new() || !frm.doc.has_user_context) return;

		frm.add_custom_button(__("View typed justification"), () => {
			frappe.call({
				method: "ccd_portal.audit.view_user_context",
				type: "POST",
				args: { event_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Decrypting the audit justification…"),
				callback(response) {
					show_typed_justification(response.message || {});
				},
			});
		});
		frm.change_custom_button_type(__("View typed justification"), null, "primary");
	},
});
