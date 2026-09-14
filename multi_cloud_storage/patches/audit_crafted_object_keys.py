# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

from urllib.parse import parse_qs, unquote, urlsplit

import frappe

from multi_cloud_storage.controller import OBJECT_KEY_FIELD, _parse_content_hash

GENERATE_FILE_PATH = "/api/method/multi_cloud_storage.controller.generate_file"
LOG_TITLE = "MultiCloud Storage: suspicious object keys"
MAX_LISTED = 500


def execute():
	"""
	One-off audit: report File rows whose object key looks crafted.

	Until secure_file_object_key_field moved mcs_object_key to permlevel 1, any
	user who could create a File could insert a row carrying somebody else's
	key and, by owning that row, pass the read check in generate_file. The
	permlevel fix stops new rows being made that way; it does nothing about
	rows made before it. This lists them. It deletes nothing -- every hit goes
	to the Error Log under LOG_TITLE for a person to look at.

	Flagged:
	  * a key shared by rows with different owners, or attached to different
	    documents (a genuine upload gets its own random suffix per attachment;
	    Frappe's dedup reuses the URL, not the key field);
	  * a row whose file_url is this app's generate_file URL but whose
	    embedded key is not the row's own key (full or 140-char legacy
	    truncation).
	"""
	if not frappe.db.has_column("File", OBJECT_KEY_FIELD):
		return

	findings = {"shared_keys": [], "url_key_mismatch": []}

	# 1. One key, several owners or several attachment targets.
	shared = frappe.db.sql(
		f"""
		SELECT `{OBJECT_KEY_FIELD}` AS object_key,
		       COUNT(*) AS rows_count,
		       COUNT(DISTINCT `owner`) AS owners,
		       COUNT(DISTINCT CONCAT(IFNULL(`attached_to_doctype`, ''), '/',
		                             IFNULL(`attached_to_name`, ''))) AS targets
		FROM `tabFile`
		WHERE IFNULL(`{OBJECT_KEY_FIELD}`, '') != ''
		GROUP BY `{OBJECT_KEY_FIELD}`
		HAVING owners > 1 OR targets > 1
		""",
		as_dict=True,
	)
	for group in shared:
		rows = frappe.get_all(
			"File",
			filters={OBJECT_KEY_FIELD: group.object_key},
			fields=["name", "owner", "attached_to_doctype", "attached_to_name", "file_url", "creation"],
			order_by="creation asc",
		)
		findings["shared_keys"].append(
			{
				"object_key": group.object_key,
				"owners": group.owners,
				"targets": group.targets,
				"rows": [
					{
						"name": r.name,
						"owner": r.owner,
						"attached_to": f"{r.attached_to_doctype or ''}/{r.attached_to_name or ''}",
						"creation": str(r.creation),
					}
					for r in rows
				],
			}
		)

	# 2. generate_file URL whose key is not the row's own.
	start = 0
	while True:
		batch = frappe.get_all(
			"File",
			filters={"file_url": ["like", GENERATE_FILE_PATH + "%"], OBJECT_KEY_FIELD: ["!=", ""]},
			fields=["name", "owner", "attached_to_doctype", "attached_to_name", "file_url", OBJECT_KEY_FIELD],
			order_by="name asc",
			limit_start=start,
			limit_page_length=500,
		)
		if not batch:
			break
		start += len(batch)
		for r in batch:
			stored = (r.get(OBJECT_KEY_FIELD) or "").strip()
			url_key = _key_in_url(r.file_url)
			if url_key is None:
				continue
			# Legacy rows hold the key truncated to content_hash's old width
			# while the URL kept the whole thing; that is not a mismatch.
			if url_key == stored or url_key[: frappe.db.VARCHAR_LEN] == stored:
				continue
			findings["url_key_mismatch"].append(
				{
					"name": r.name,
					"owner": r.owner,
					"attached_to": f"{r.attached_to_doctype or ''}/{r.attached_to_name or ''}",
					"stored_key": stored,
					"url_key": url_key,
				}
			)

	total = len(findings["shared_keys"]) + len(findings["url_key_mismatch"])
	if not total:
		return

	summary = {
		"site": frappe.local.site,
		"shared_key_groups": len(findings["shared_keys"]),
		"url_key_mismatches": len(findings["url_key_mismatch"]),
		"note": "Audit only; nothing was changed. Review each row and delete or re-attach by hand.",
		"shared_keys": findings["shared_keys"][:MAX_LISTED],
		"url_key_mismatch": findings["url_key_mismatch"][:MAX_LISTED],
	}
	frappe.log_error(title=LOG_TITLE, message=frappe.as_json(summary))


def _key_in_url(file_url):
	"""The object key a generate_file URL asks for, or None if it has none."""
	try:
		query = parse_qs(urlsplit(file_url or "").query)
	except ValueError:
		return None
	raw = (query.get("key") or [""])[0]
	if not raw:
		return None
	key, _bucket = _parse_content_hash(unquote(raw))
	return key or None
