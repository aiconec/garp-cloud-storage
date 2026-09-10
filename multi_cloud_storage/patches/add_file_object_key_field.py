# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

import frappe

from multi_cloud_storage.controller import OBJECT_KEY_FIELD
from multi_cloud_storage.install import create_file_object_key_field


def execute():
	"""
	Add File.mcs_object_key and backfill it from the keys already parked in
	content_hash.

	content_hash is left as it is. Rewriting it would need the file's bytes,
	which by now live in the bucket; the rows keep working because the
	controller falls back to a content_hash lookup for anything the new field
	does not answer. New uploads stop touching content_hash, so Frappe's
	deduplication starts working again for everything from here on.

	Keys longer than 140 characters were already truncated by the content_hash
	column when they were written, so what lands in the new field for those
	rows is truncated too. That is deliberate: the controller's fallback
	matches on the same truncation, which is what still resolves those files,
	and the full key it signs with comes from the file_url, which kept it.
	"""
	create_file_object_key_field()

	for prefix in ("private:", "public:"):
		frappe.db.sql(
			f"""
			UPDATE `tabFile`
			SET `{OBJECT_KEY_FIELD}` = SUBSTRING(content_hash, %s)
			WHERE content_hash LIKE %s
			  AND (`{OBJECT_KEY_FIELD}` IS NULL OR `{OBJECT_KEY_FIELD}` = '')
			""",
			(len(prefix) + 1, prefix + "%"),
		)
