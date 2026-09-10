# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

import os
import re
from urllib.parse import quote

import frappe

from .backends.gcs_backend import GCSBackend
from .backends.s3_backend import S3Backend

# Custom field on File holding the object key this app uploaded the file under.
#
# The key used to be written over File.content_hash. Frappe puts a hash of the
# file's CONTENT there and looks files up by it to avoid storing the same bytes
# twice, and to decide whether a physical file is still referenced before
# deleting it. Overwriting it with a key that carries a unique random suffix
# meant no two rows ever matched again, so deduplication silently stopped
# working. The key now lives in its own field and content_hash is left alone.
OBJECT_KEY_FIELD = "mcs_object_key"

CONTENT_HASH_PRIVATE = "private:"
CONTENT_HASH_PUBLIC = "public:"


def get_config():
	config = frappe.get_single("Cloud Storage Configuration")
	if not config.enabled:
		return None
	return config


def get_backend(config=None):
	config = config or get_config()
	if not config:
		return None
	if config.storage_provider == "Amazon S3":
		return S3Backend(config)
	if config.storage_provider == "Google Cloud Storage":
		return GCSBackend(config)
	return None


def _get_content_type(file_path):
	try:
		import magic

		return magic.from_file(file_path, mime=True)
	except Exception:
		return "application/octet-stream"


def _is_cloud_file_url(file_url):
	if not file_url:
		return False
	patterns = [
		r"^https?://.*\.s3\.amazonaws\.com/",
		r"^/api/method/multi_cloud_storage\.controller\.generate_file",
		r"^https://storage\.googleapis\.com/",
		r"^https://storage\.cloud\.google\.com/",
	]
	return any(re.match(p, file_url) for p in patterns)


def _is_local_file_url(file_url):
	if not file_url or not isinstance(file_url, str):
		return False
	return file_url.startswith("/files/") or file_url.startswith("/private/files/")


def _parse_content_hash(content_hash):
	if not content_hash or not isinstance(content_hash, str):
		return None, "private"
	s = content_hash.strip()
	if s.startswith(CONTENT_HASH_PRIVATE):
		return s[len(CONTENT_HASH_PRIVATE) :].strip(), "private"
	if s.startswith(CONTENT_HASH_PUBLIC):
		return s[len(CONTENT_HASH_PUBLIC) :].strip(), "public"
	return s.strip(), "private"


def _has_key_field():
	"""Whether the File custom field exists yet (it is created on install/migrate)."""
	try:
		return frappe.get_meta("File").has_field(OBJECT_KEY_FIELD)
	except Exception:
		return False


def can_serve_public(config):
	"""
	Whether a file marked public can actually be fetched from the public bucket
	without a signature.

	When ACLs are disabled and the "public" bucket is the same private bucket,
	nothing about an uploaded object is public: get_public_url hands out an
	unsigned URL that the bucket answers with 403. The upload path used to do
	that anyway AND delete the local copy, so every public attachment — logos,
	website images — broke permanently the moment this app was switched on,
	with no way back. Rather than produce a dead URL, such files are left on
	local disk where they still work.

	Setting s3_public_base_url (a CDN or a bucket that really is world
	readable) is the way to opt back in.
	"""
	if not config:
		return False
	if config.storage_provider == "Amazon S3":
		if (config.get("s3_public_base_url") or "").strip():
			return True
		if not config.get("s3_disable_acl"):
			return True
		# ACLs off: only trustworthy if the public bucket is a genuinely
		# separate bucket the operator has made readable.
		private_bucket = (config.get("s3_private_bucket_name") or "").strip()
		public_bucket = (config.get("s3_public_bucket_name") or "").strip()
		return bool(public_bucket) and public_bucket != private_bucket
	if config.storage_provider == "Google Cloud Storage":
		private_bucket = (config.get("gcs_private_bucket_name") or "").strip()
		public_bucket = (config.get("gcs_public_bucket_name") or "").strip()
		return bool(public_bucket) and public_bucket != private_bucket
	return False


def _record_key(key, is_private):
	"""
	Build the File field updates that record an upload.

	The caller writes them with db.set_value rather than raw SQL, so the
	document cache is invalidated instead of left holding the old file_url.
	"""
	values = {
		"folder": "Home/Attachments",
		"old_parent": "Home/Attachments",
	}
	if _has_key_field():
		values[OBJECT_KEY_FIELD] = key
	else:
		prefix = CONTENT_HASH_PRIVATE if is_private else CONTENT_HASH_PUBLIC
		# Pre-migration fallback: the old location, so an install that has not
		# run the patch yet still resolves its own uploads.
		values["content_hash"] = prefix + key
	return values


def _delete_local_after_commit(file_path):
	"""
	Remove the local copy only once the database write is durable.

	Deleting first and writing second meant a failure anywhere later in the
	request rolled the File row back while the bytes were already gone from
	disk — the upload vanished, and the object was orphaned in the bucket with
	nothing referencing it.
	"""

	def _remove():
		try:
			os.remove(file_path)
		except OSError:
			pass

	frappe.db.after_commit.add(_remove)


def _cloud_url_for(key, file_name, is_private, backend):
	if is_private:
		return (
			"/api/method/multi_cloud_storage.controller.generate_file"
			f"?key={quote(CONTENT_HASH_PRIVATE + key)}&file_name={quote(file_name or '')}"
		)
	return backend.get_public_url(key)


def file_upload_to_cloud(doc, method=None):
	if doc.attached_to_doctype == "Prepared Report":
		return
	config = get_config()
	backend = get_backend(config)
	if not backend:
		return
	ignore_doctypes = frappe.local.conf.get("ignore_multi_cloud_storage_doctype") or ["Data Import"]
	if doc.attached_to_doctype in ignore_doctypes:
		return
	site_path = frappe.utils.get_site_path()
	path = doc.file_url
	if not path or _is_cloud_file_url(path):
		return

	if not doc.is_private and not can_serve_public(config):
		# See can_serve_public: uploading this would swap a working local URL
		# for one that 403s.
		return

	if doc.is_private:
		file_path = os.path.join(site_path, path.lstrip("/"))
	else:
		file_path = os.path.join(site_path, "public", path.lstrip("/"))
	if not os.path.isfile(file_path):
		return
	parent_doctype = doc.attached_to_doctype or "File"
	parent_name = doc.attached_to_name or ""
	if hasattr(backend, "key_generator"):
		key = backend.key_generator(doc.file_name, parent_doctype, parent_name)
	else:
		key = f"{parent_doctype}/{doc.file_name}"
	content_type = _get_content_type(file_path)
	backend.upload(file_path, key, content_type, doc.is_private, doc.file_name)

	file_url = _cloud_url_for(key, doc.file_name, doc.is_private, backend)
	values = _record_key(key, doc.is_private)
	values["file_url"] = file_url
	frappe.db.set_value("File", doc.name, values, update_modified=False)

	doc.file_url = file_url
	if _has_key_field():
		doc.set(OBJECT_KEY_FIELD, key)
	else:
		doc.content_hash = values["content_hash"]

	_delete_local_after_commit(file_path)


def _key_of(doc):
	"""The object key this File row was uploaded under, new field or old."""
	if _has_key_field():
		key = (doc.get(OBJECT_KEY_FIELD) or "").strip()
		if key:
			bucket_type = "private" if doc.is_private else "public"
			return key, bucket_type
	return _parse_content_hash(doc.content_hash)


def delete_from_cloud(doc, method=None):
	backend = get_backend()
	if not backend:
		return

	key, bucket_type = _key_of(doc)
	if not key:
		return

	# Only act on files this app actually uploaded. A public file's URL points
	# at the provider's own endpoint, which for an S3-compatible provider is a
	# custom domain _is_cloud_file_url knows nothing about — so a populated key
	# field counts as proof of ownership too, otherwise public objects would
	# never be cleaned up.
	stored_key = (doc.get(OBJECT_KEY_FIELD) or "").strip() if _has_key_field() else ""
	if not stored_key and not _is_cloud_file_url(doc.file_url):
		return

	# Only remove the object when this is the last row pointing at it.
	#
	# Mirrors Frappe's own on_trash check, and closes a hole: File.content_hash
	# and the key field are read_only in the UI but Frappe does not enforce
	# read_only server-side, so a user could insert a File row carrying someone
	# else's key and delete it to destroy their object. A key still referenced
	# by another row is never deleted.
	if stored_key:
		filters = {OBJECT_KEY_FIELD: key}
	elif doc.content_hash:
		filters = {"content_hash": doc.content_hash}
	else:
		return
	filters["name"] = ["!=", doc.name]
	if frappe.db.exists("File", filters):
		return

	backend.delete(key, bucket_type)


# One object can be referenced by several File rows — Frappe creates a row per
# attachment. More than this many sharing a key means something is wrong; the
# cap keeps a lookup from turning into an unbounded scan.
MAX_ROWS_PER_KEY = 20


def _readable_file_for_key(raw_key, parsed_key):
	"""
	Find a File row for this object key that the current user may read.

	Returning None means either no row claims the key or none of them is
	readable. generate_file used to sign whatever key it was handed, so anyone
	with a session could redeem a guessed or overheard key for the file behind
	it.

	Checks EVERY row that references the key, not just the first. Frappe's own
	download path does the same (core.doctype.file.utils.find_file_by_url):
	the same bytes can be attached to several documents, and access to any one
	of them is access to the file. Judging only one row would deny a user a
	file they can legitimately reach.
	"""
	from frappe.core.doctype.file.file import has_permission as file_has_permission

	names = []
	if _has_key_field() and parsed_key:
		names = frappe.get_all(
			"File", filters={OBJECT_KEY_FIELD: parsed_key}, pluck="name", limit=MAX_ROWS_PER_KEY
		)
	if not names and raw_key:
		# Uploads from before the key moved into its own field.
		#
		# Compared against the TRUNCATED key. content_hash is a Data column,
		# varchar(140), so the old code silently cut long keys off at write
		# time while the file_url kept the whole thing. Matching on the full
		# key the URL carries would then miss those rows, and a permission
		# check that cannot find the row denies access — turning a legacy file
		# that used to download into a 403.
		names = frappe.get_all(
			"File",
			filters={"content_hash": raw_key[: frappe.db.VARCHAR_LEN]},
			pluck="name",
			limit=MAX_ROWS_PER_KEY,
		)

	for name in names:
		doc = frappe.get_doc("File", name)
		if file_has_permission(doc, "read"):
			return doc
	return None


@frappe.whitelist()
def generate_file(key=None, file_name=None):
	# isinstance as well as truthiness: key is request input, and a non-string
	# would reach the slice in _readable_file_for_key and raise a TypeError
	# instead of a clean refusal.
	if not key or not isinstance(key, str):
		frappe.throw(frappe._("Key not found."), frappe.DoesNotExistError)
	backend = get_backend()
	if not backend:
		frappe.throw(frappe._("MultiCloud Storage is not enabled"))

	parsed_key, _prefix_bucket = _parse_content_hash(key)

	# The permission check Frappe would have applied while the file was still
	# served from /private/files. Without it this endpoint handed a presigned
	# URL for any private file to any authenticated user, whatever their roles
	# — and with every site sharing one bucket, to other tenants' files too.
	#
	# An unknown key and an unreadable one give the same answer, so this is not
	# an oracle for which keys exist.
	file_doc = _readable_file_for_key(key, parsed_key)
	if not file_doc:
		frappe.throw(frappe._("Not permitted"), frappe.PermissionError)

	# Same audit trail the native private-file route writes.
	from frappe.core.doctype.access_log.access_log import make_access_log

	make_access_log(doctype="File", document=file_doc.name)

	# The stored name, not the caller's: the caller's value lands in
	# Content-Disposition on a URL that leaves our control.
	download_name = file_doc.file_name or file_name

	# Bucket from the File row, not from the prefix on the caller's key. The
	# prefix is caller-controlled text, and it was the last thing they could
	# still steer about the request we sign.
	bucket_type = "private" if file_doc.is_private else "public"

	url = backend.get_url(parsed_key, download_name, bucket_type)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = url


def _upload_existing_file(file_doc):
	config = get_config()
	backend = get_backend(config)
	if not backend:
		return False
	doc = file_doc
	path = (doc.file_url or "").strip()
	if not _is_local_file_url(path):
		return False
	if not doc.is_private and not can_serve_public(config):
		return "public_not_servable"
	if path.startswith("/private/files/"):
		relative = path[len("/private/files/") :].lstrip("/")
		file_path = frappe.utils.get_files_path(*relative.split("/"), is_private=True)
	else:
		relative = path[len("/files/") :].lstrip("/")
		file_path = frappe.utils.get_files_path(*relative.split("/"))
	if not os.path.isfile(file_path):
		return "file_not_found"
	parent_doctype = doc.attached_to_doctype or "File"
	parent_name = doc.attached_to_name or ""
	if hasattr(backend, "key_generator"):
		key = backend.key_generator(doc.file_name, parent_doctype, parent_name)
	else:
		key = f"{parent_doctype}/{doc.file_name}"
	content_type = _get_content_type(file_path)
	backend.upload(file_path, key, content_type, doc.is_private, doc.file_name)

	file_url = _cloud_url_for(key, doc.file_name, doc.is_private, backend)
	values = _record_key(key, doc.is_private)
	values["file_url"] = file_url
	frappe.db.set_value("File", doc.name, values, update_modified=False)
	frappe.db.commit()

	# After the commit, so a failed migration never loses the source file.
	try:
		os.remove(file_path)
	except OSError:
		pass
	return True


MIGRATE_BATCH_SIZE = 200


@frappe.whitelist()
def migrate_existing_files():
	"""
	Queue a migration of every local file into the configured bucket.

	Enqueued rather than run inline: this walks the whole File table and
	uploads one object at a time, which on any real site ran past the gateway
	timeout and left the migration half applied with no way to resume.
	"""
	frappe.only_for("System Manager")
	config = get_config()
	if not config:
		frappe.throw(frappe._("MultiCloud Storage is not enabled"))

	job_id = "multi_cloud_storage::migrate"
	job = frappe.enqueue(
		"multi_cloud_storage.controller.run_migration",
		queue="long",
		timeout=21600,
		job_id=job_id,
		deduplicate=True,
	)
	if not job:
		return {"queued": False, "message": frappe._("A migration is already running.")}
	return {"queued": True, "message": frappe._("Migration queued. Progress is written to the Error Log.")}


def run_migration():
	"""Worker entry point for migrate_existing_files. Resumable: re-run to continue."""
	config = get_config()
	if not config:
		return

	migrated = 0
	skipped_no_url_or_cloud = 0
	skipped_not_local = 0
	skipped_file_not_found = 0
	skipped_public_not_servable = 0
	skipped_other = 0
	errors = []
	start = 0

	while True:
		batch = frappe.get_all(
			"File",
			filters={"is_folder": 0},
			fields=["name", "file_url"],
			order_by="creation asc",
			limit_start=start,
			limit_page_length=MIGRATE_BATCH_SIZE,
		)
		if not batch:
			break
		start += len(batch)

		for f in batch:
			file_url = f.get("file_url")
			if not file_url or _is_cloud_file_url(file_url):
				skipped_no_url_or_cloud += 1
				continue
			if not _is_local_file_url(file_url):
				skipped_not_local += 1
				continue
			try:
				doc = frappe.get_doc("File", f["name"])
				result = _upload_existing_file(doc)
				if result is True:
					migrated += 1
				elif result == "file_not_found":
					skipped_file_not_found += 1
				elif result == "public_not_servable":
					skipped_public_not_servable += 1
				else:
					skipped_other += 1
			except Exception as e:
				skipped_other += 1
				errors.append({"file": f["name"], "error": str(e)})
				frappe.log_error(
					title=f"MultiCloud Storage migrate: {f.get('name')}",
					message=frappe.get_traceback(),
				)

	summary = {
		"migrated": migrated,
		"skipped": (
			skipped_no_url_or_cloud
			+ skipped_not_local
			+ skipped_file_not_found
			+ skipped_public_not_servable
			+ skipped_other
		),
		"skipped_no_url_or_cloud": skipped_no_url_or_cloud,
		"skipped_not_local_url": skipped_not_local,
		"skipped_file_not_found": skipped_file_not_found,
		"skipped_public_not_servable": skipped_public_not_servable,
		"skipped_other": skipped_other,
		"errors": errors[:10],
	}
	frappe.log_error(title="MultiCloud Storage migration finished", message=frappe.as_json(summary))
	return summary


@frappe.whitelist()
def test_connection():
	frappe.only_for("System Manager")
	config = get_config()
	if not config:
		return {"success": False, "message": frappe._("MultiCloud Storage is not enabled")}
	backend = get_backend(config)
	if not backend:
		return {"success": False, "message": frappe._("Invalid provider configuration")}
	ok, err = backend.test_connection()
	if ok:
		return {"success": True, "message": frappe._("Connection successful")}
	# The provider's own message names buckets, endpoints and sometimes the key
	# id. It goes to the Error Log, which System Manager can read deliberately,
	# rather than into an API response.
	if err:
		frappe.log_error(title="MultiCloud Storage connection test failed", message=str(err))
	return {
		"success": False,
		"message": frappe._("Connection failed. See the Error Log for details."),
	}
