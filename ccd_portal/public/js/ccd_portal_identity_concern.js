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
	const escape = (value) => frappe.utils.escape_html(String(value ?? ""));
	const records = data.workflow_records || [];
	const fields = data.comparison_fields || [];
	const expiresIn = Number(data.expires_in || 120);
	const recordHeaders = records
		.map((record, index) => {
			const name = String(record.ccd_master_record || "");
			const route = `/app/ccd-master/${encodeURIComponent(name)}`;
			return `<th style="position:sticky;top:0;z-index:2;min-width:220px;vertical-align:top;background:var(--control-bg,#f7f7f7)">
				<a href="${route}" target="_blank" rel="noopener noreferrer">${__("Record")} ${index + 1}: ${escape(name)}</a>
				<div class="text-muted" style="font-weight:normal;margin-top:.25rem">${escape(record.service_name || __("Service not specified"))}</div>
			</th>`;
		})
		.join("");
	const fieldRows = fields
		.map((field) => {
			const cells = records
				.map((record) => {
					const raw = record.values?.[field.fieldname];
					const display = raw === null || raw === undefined || raw === "" ? "—" : raw;
					return `<td style="white-space:pre-wrap;overflow-wrap:anywhere">${escape(display)}</td>`;
				})
				.join("");
			const difference = field.different
				? `<span class="indicator-pill orange" style="margin-left:.35rem">${__("Different")}</span>`
				: "";
			const background = field.different
				? "background:var(--yellow-50,#fff8dd);"
				: "background:var(--card-bg,#fff);";
			return `<tr style="${background}">
				<th style="position:sticky;left:0;z-index:1;min-width:190px;${background}">${escape(field.label)}${difference}</th>
				${cells}
			</tr>`;
		})
		.join("");
	const comparison = records.length && fields.length
		? `<div style="overflow:auto;max-height:58vh;border:1px solid var(--border-color);border-radius:6px">
			<table class="table table-bordered" style="margin:0;min-width:${190 + records.length * 220}px">
				<thead><tr>
					<th style="position:sticky;left:0;top:0;z-index:3;min-width:190px;background:var(--control-bg,#f7f7f7)">${__("Identity field")}</th>
					${recordHeaders}
				</tr></thead>
				<tbody>${fieldRows}</tbody>
			</table>
		</div>`
		: `<p class="text-muted">${__("No current policy-approved identity fields are available for comparison.")}</p>`;
	const reportedReason = data.reason
		? `<p><strong>${__("Reported concern:")}</strong> ${escape(data.reason)}</p>`
		: "";
	const html = `
		<div data-identity-review-context>
			<p class="alert alert-warning">${__("Sensitive identity values are temporarily revealed for this audited review and will clear automatically in")} ${expiresIn} ${__("seconds.")}</p>
			${reportedReason}
			${comparison}
			<p style="margin-top:1rem"><strong>${__("If the existing link is correct:")}</strong> ${__("close this comparison and choose Dismiss — link is correct.")}</p>
			<p><strong>${__("If the link is wrong:")}</strong> ${__("complete the governed identity correction, split, or revalidation workflow first; then choose Resolve — correction completed.")}</p>
			<p class="text-muted">${__("Highlighted rows contain different values. The concern itself never changes identity links, and these values are not saved in the form or browser storage.")}</p>
		</div>`;
	let timeout;
	const dialog = new frappe.ui.Dialog({
		title: __("Compare selected identity records"),
		size: "extra-large",
		fields: [{ fieldname: "comparison", fieldtype: "HTML", options: html }],
		primary_action_label: __("Close and clear"),
		primary_action() {
			dialog.hide();
		},
	});
	const wrapper = dialog.get_field("comparison").$wrapper;
	dialog.$wrapper.find(".modal-dialog").css("max-width", "95vw");
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

function reveal_review_context(frm, supplied_reason) {
	const reveal = (reason) => {
		frappe.call({
			method: "ccd_portal.api.identity_concern_review_context",
			type: "POST",
			args: { concern_id: frm.doc.name, reason },
			freeze: true,
			freeze_message: __("Preparing the temporary comparison…"),
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
		__("Explain why you need a temporary side-by-side comparison of the selected CCD Master records."),
		__("Reveal comparison"),
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
				__("Start review to compare all selected records side by side. The concern does not change identity links."),
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
