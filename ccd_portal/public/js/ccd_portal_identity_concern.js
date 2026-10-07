frappe.ui.form.on("CCD Portal Identity Concern", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Reveal review context"), () => {
			frappe.prompt(
				[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }],
				(values) => {
					frappe.call({
						method: "ccd_portal.api.identity_concern_review_context",
						type: "POST",
						args: { concern_id: frm.doc.name, reason: values.reason },
						callback(response) {
							const data = response.message || {};
							const escape = frappe.utils.escape_html;
							const records = (data.workflow_records || []).map((value) => `<li>${escape(value.portal_record_id)} → ${escape(value.ccd_master_record)}</li>`).join("");
							const dialog = frappe.msgprint({
								title: __("Temporary review context"),
								message: `<p>${escape(data.reason || "")}</p><ul>${records}</ul><p>${__("Close this dialog when finished. Values are not stored in the form.")}</p>`,
								wide: true,
							});
							setTimeout(() => dialog?.hide(), Number(data.expires_in || 120) * 1000);
						},
					});
				},
				__("Reasoned access")
			);
		});
	},
});
