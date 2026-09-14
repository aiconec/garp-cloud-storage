# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from multi_cloud_storage.controller import OBJECT_KEY_FIELD


def after_install():
	create_file_object_key_field()


def create_file_object_key_field():
	"""
	Give File its own column for the cloud object key.

	The key used to be written over File.content_hash, which Frappe fills with
	a hash of the file's contents and queries to deduplicate uploads and to
	decide whether a stored file is still referenced. Every key carries a
	unique random suffix, so once this app was installed no two rows ever
	matched on content_hash again and deduplication silently stopped.

	Indexed because generate_file resolves a key back to its File row on every
	private-file download, and that lookup is the permission check.
	"""
	create_custom_fields(
		{
			"File": [
				{
					"fieldname": OBJECT_KEY_FIELD,
					"label": "Cloud Object Key",
					"fieldtype": "Data",
					"length": 255,
					"read_only": 1,
					# permlevel 1, and this is load-bearing security, not cosmetics.
					#
					# read_only is a UI hint: Frappe does NOT enforce it server-side,
					# so with the field at permlevel 0 any user who can create a File
					# (role "All") could insert a row carrying someone else's object
					# key, own that row, and thereby satisfy the read check in
					# generate_file -- handing themselves a presigned URL for another
					# user's (or another tenant's) private object.
					#
					# permlevel > 0 IS enforced: base_document.reset_values_if_no_permlevel_access
					# resets fields the user has no permlevel access to, and File.json
					# grants no permlevel-1 permission to anyone. The app's own writes
					# go through frappe.db.set_value, which bypasses the document layer,
					# so uploads are unaffected.
					"permlevel": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
					"search_index": 1,
					"insert_after": "content_hash",
				}
			]
		}
	)
