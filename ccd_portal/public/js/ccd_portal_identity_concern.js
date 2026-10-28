function concern_reason(title, description, action, callback) {
	frappe.prompt(
		[
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Reason"),
				description,
				reqd: 1,
			},
		],
		callback,
		title,
		action
	);
}

function show_review_context(data) {
	const escape = frappe.utils.escape_html;
	const records = (data.workflow_records || [])
		.map((value, index) => {
			const name = String(value.ccd_master_record || "");
			const route = `/app/ccd-master/${encodeURIComponent(name)}`;
			return `<li><a href="${route}" target="_blank" rel="noopener noreferrer">${__("Record")} ${index + 1}: ${escape(name)}</a></li>`;
		})
		.join("");
	const dialog = frappe.msgprint({
		title: __("Review selected identity records"),
		message: `
			<p>${__("Open and compare every selected CCD Master record using the links below.")}</p>
			<ol>${records}</ol>
			<p><strong>${__("If the existing link is correct:")}</strong> ${__("return here and choose Dismiss — link is correct.")}</p>
			<p><strong>${__("If the link is wrong:")}</strong> ${__("complete the governed identity correction, split, or revalidation workflow first; then return here and choose Resolve — correction completed.")}</p>
			<p class="text-muted">${__("The concern itself never changes identity links. This temporary context closes automatically and is not saved in the form.")}</p>`,
		wide: true,
	});
	setTimeout(() => dialog?.hide(), Number(data.expires_in || 120) * 1000);
}

function reveal_review_context(frm, supplied_reason) {
	const reveal = (reason) => {
		frappe.call({
			method: "ccd_portal.api.identity_concern_review_context",
			type: "POST",
			args: { concern_id: frm.doc.name, reason },
			freeze: true,
			freeze_message: __("Preparing temporary review links…"),
			callback(response) {
				show_review_context(response.message || {});
			},
		});
	};
	if (supplied_reason) {
		reveal(supplied_reason);
		return;
	}
	concern_reason(
		__("Review selected records"),
		__("Explain why you need temporary access to the selected CCD Master records."),
		__("Reveal links"),
		(values) => reveal(values.reason)
	);
}

function transition_concern(frm, status, title, description, action, reveal_after) {
	concern_reason(title, description, action, (values) => {
		frappe.call({
			method: "ccd_portal.api.transition_identity_concern",
			type: "POST",
			args: { concern_id: frm.doc.name, status, reason: values.reason },
			freeze: true,
			freeze_message: __("Recording the audited review action…"),
			callback() {
				frm.reload_doc().then(() => {
					if (reveal_after) reveal_review_context(frm, values.reason);
				});
			},
		});
	});
}

frappe.ui.form.on("CCD Portal Identity Concern", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.disable_save();

		if (frm.doc.status === "Submitted") {
			frm.set_intro(
				__("Start review to obtain temporary links to all selected records. The concern does not change identity links."),
				"orange"
			);
			frm.add_custom_button(__("Start review"), () => {
				transition_concern(
					frm,
					"In Review",
					__("Start identity concern review"),
					__("Enter the audit reason for investigating this reported link."),
					__("Start review"),
					true
				);
			});
			frm.change_custom_button_type(__("Start review"), null, "primary");
			return;
		}

		if (frm.doc.status === "In Review") {
			frm.set_intro(
				__("Compare every selected record. Dismiss only when the link is correct. Resolve only after the governed identity correction, split, or revalidation is complete."),
				"blue"
			);
			frm.add_custom_button(__("Review selected records"), () => reveal_review_context(frm));
			frm.change_custom_button_type(__("Review selected records"), null, "primary");
			frm.add_custom_button(__("Dismiss — link is correct"), () => {
				transition_concern(
					frm,
					"Dismissed",
					__("Dismiss identity concern"),
					__("Explain why the selected records belong to the same person."),
					__("Dismiss concern"),
					false
				);
			});
			frm.add_custom_button(__("Resolve — correction completed"), () => {
				transition_concern(
					frm,
					"Resolved",
					__("Resolve identity concern"),
					__("Confirm that the governed identity correction, split, or revalidation has already been completed, and record the outcome."),
					__("Resolve concern"),
					false
				);
			});
			return;
		}

		const closed_message = frm.doc.status === "Resolved"
			? __("Resolved: the reviewer recorded that the required governed identity action was completed.")
			: __("Dismissed: the reviewer recorded that the existing identity link is correct.");
		frm.set_intro(closed_message, "green");
	},
});
