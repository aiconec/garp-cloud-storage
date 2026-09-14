# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

import ipaddress
import os
import re
import socket
import string
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import frappe

# Object-key layout, shared so every backend agrees with the column the key is
# stored in. MAX_KEY_LENGTH matches the File.mcs_object_key custom field, which
# is indexed and therefore bounded.
KEY_ALPHABET = string.ascii_uppercase + string.digits
MAX_KEY_LENGTH = 255


class CloudStorageBackend(ABC):
	@abstractmethod
	def upload(self, file_path, key, content_type, is_private, file_name=None):
		pass

	@abstractmethod
	def delete(self, key, bucket_type="private"):
		pass

	@abstractmethod
	def get_url(self, key, file_name=None, bucket_type="private"):
		pass

	@abstractmethod
	def test_connection(self):
		pass


# Extensions that a browser will happily execute in the bucket's origin if the
# object is served inline. Frappe forces `Content-Disposition: attachment` for
# exactly these on its own private-file route (see
# frappe.utils.response.FORCE_DOWNLOAD_EXTENSIONS); serving them from cloud
# storage without doing the same is a regression, not a difference of opinion.
FORCE_DOWNLOAD_EXTENSIONS = (
	".svg",
	".svgz",
	".html",
	".htm",
	".xhtml",
	".xht",
	".shtml",
	".shtm",
	".mhtml",
	".mht",
	".xml",
	".xsl",
)


# Extensions a browser may render inline without executing anything. Everything
# else is downloaded, whatever the bytes claim to be.
INLINE_SAFE_EXTENSIONS = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".txt": "text/plain", ".csv": "text/plain", ".json": "application/json",
}


def _clean_name(file_name):
    name = (file_name or "").strip()
    # Keep the header a single well-formed parameter: no quotes, no separators,
    # no control characters, no path.
    return re.sub(r'[\r\n";\\]', "", name).replace("/", "_").strip()


def response_content_type_for(file_name):
    """The Content-Type the object should be SERVED with, from its name.

    Never from the bytes. The upload path stores a libmagic-sniffed type, so an
    object called `evil`, `evil.svg.` or `logo.png` whose bytes are an SVG is
    stored as image/svg+xml, and a browser handed that type renders it, script
    included, in the bucket's origin. Keying on the extension mirrors Frappe's
    own private-file route: a `.png` is served as image/png (a forged one is a
    broken image), and anything not on the inline-safe list is served as an
    opaque download.
    """
    extension = os.path.splitext(_clean_name(file_name))[1].lower()
    return INLINE_SAFE_EXTENSIONS.get(extension, "application/octet-stream")


def content_disposition_for(file_name):
    """Build a Content-Disposition value for a download URL.

    `inline` only for INLINE_SAFE_EXTENSIONS; `attachment` for everything else,
    including no extension at all. Pair with response_content_type_for: the
    disposition decides whether the browser navigates to the bytes, the type
    decides how it interprets them, and both must come from the name rather
    than the stored, sniffed type.
    """
    name = _clean_name(file_name)
    extension = os.path.splitext(name)[1].lower()
    disposition = "inline" if extension in INLINE_SAFE_EXTENSIONS else "attachment"

    if not name:
        return disposition
    return f'{disposition}; filename="{name}"'


# Hosts the storage endpoint may point at. A tenant System Manager can edit
# s3_endpoint_url, and boto3 will happily sign a request to whatever it says --
# including 169.254.169.254 or an internal Railway service. That is a
# request-forgery primitive that reaches out of the tenant and into the
# platform network, using the shared credential.
_BLOCKED_ENDPOINT_HOSTS = (
    "169.254.169.254",   # cloud instance metadata
    "metadata.google.internal",
    "metadata",
    "localhost",
)
_BLOCKED_ENDPOINT_SUFFIXES = (".internal", ".local", ".localhost", ".railway.internal")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def validate_endpoint_url(endpoint_url):
    """Return the endpoint if it is safe to use, else raise.

    Permissive about WHICH provider (S3-compatible vendors have their own
    domains), strict about the shapes that are never a real object store:
    non-HTTPS schemes, and any name that resolves to a loopback, link-local,
    private, CGNAT or reserved address.
    """
    if not endpoint_url:
        return None

    parsed = urlparse(endpoint_url)
    if parsed.scheme != "https":
        frappe.throw(
            frappe._("Storage endpoint must use https:// (got {0})").format(
                parsed.scheme or "no scheme"
            )
        )

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        frappe.throw(frappe._("Storage endpoint has no host"))

    if host in _BLOCKED_ENDPOINT_HOSTS or host.endswith(_BLOCKED_ENDPOINT_SUFFIXES):
        frappe.throw(frappe._("Storage endpoint host is not allowed"))

    # Resolve, and test every address the name maps to. ipaddress.ip_address
    # rejects the shorthand forms glibc accepts (2130706433, 0x7f000001, 127.1,
    # 0251.0376.0251.0376), and a name such as 169.254.169.254.nip.io is a
    # plain hostname until it is resolved.
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        frappe.throw(frappe._("Storage endpoint host does not resolve"))

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or ip in _CGNAT
        ):
            frappe.throw(frappe._("Storage endpoint may not point at an internal address"))

    return endpoint_url
