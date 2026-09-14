# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

import frappe

from multi_cloud_storage.controller import OBJECT_KEY_FIELD
from multi_cloud_storage.install import create_file_object_key_field


def execute():
	"""
	Move File.mcs_object_key to permlevel 1 on sites that already have it.

	The field was created read_only, which Frappe treats as a UI hint and does
	not enforce on the server. At permlevel 0 that let any user who can create
	a File (role "All" has create) insert a row carrying somebody else's object
	key. Because they own that row, the read check inside generate_file passed,
	and the endpoint signed a URL for an object that was never theirs -- across
	tenants too, since every site in the fleet shares one bucket.

	permlevel > 0 IS enforced (base_document.reset_values_if_no_permlevel_access
	drops fields the user has no permlevel access to), and File.json grants
	nobody permlevel-1 access, so after this patch the field can only be written
	the way the app writes it: through frappe.db.set_value, which bypasses the
	document layer entirely.

	create_custom_fields updates an existing field in place, so this is just a
	re-run of the install definition, which now carries the permlevel.
	"""
	create_file_object_key_field()

	# create_custom_fields skips the update when nothing it compares changed on
	# some versions, so set it explicitly and make the outcome verifiable.
	name = frappe.db.get_value("Custom Field", {"dt": "File", "fieldname": OBJECT_KEY_FIELD})
	if not name:
		return

	if frappe.db.get_value("Custom Field", name, "permlevel") != 1:
		frappe.db.set_value("Custom Field", name, "permlevel", 1)

	frappe.clear_cache(doctype="File")
