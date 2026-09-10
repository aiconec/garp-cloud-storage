// Copyright (c) 2026, Bhushan Barbuddhe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Cloud Storage Configuration", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint({
					title: __("Save First"),
					message: __("Please save your changes before testing the connection."),
					indicator: "blue",
				});
				return;
			}
			frappe.call({
				method: "multi_cloud_storage.controller.test_connection",
				freeze: true,
				callback(r) {
					if (r.message && r.message.success) {
						frappe.show_alert({
							message: __("Connection successful"),
							indicator: "green",
						});
					} else {
						frappe.msgprint({
							title: __("Connection Failed"),
							message: (r.message && r.message.message) || __("Unknown error"),
							indicator: "red",
						});
					}
				},
			});
		}).addClass("btn-primary");

		frm.add_custom_button(__("Migrate Existing Files"), () => {
			frappe.confirm(
				__("Upload all local files (/files/ and /private/files/) to cloud. Continue?"),
				() => {
					frappe.call({
						method: "multi_cloud_storage.controller.migrate_existing_files",
						freeze: true,
						callback(r) {
							if (!r.message) return;
							// The migration runs in a background job now: walking the
							// whole File table inline ran past the gateway timeout and
							// left the site half migrated. So there are no per-file
							// counts to report here — the job writes its summary to the
							// Error Log when it finishes.
							frappe.msgprint({
								title: r.message.queued
									? __("Migration queued")
									: __("Migration not started"),
								message: r.message.message,
								indicator: r.message.queued ? "blue" : "orange",
							});
						},
					});
				}
			);
		});
	},
});
