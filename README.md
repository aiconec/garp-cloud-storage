# GARP Cloud Storage

Multi-cloud file storage for GARP ERP. Uploads **File** attachments to **Amazon S3-compatible** providers (including Railway Bucket) or **Google Cloud Storage (GCS)** and serves them from the cloud.

## Features

- **Dual provider**: Amazon S3-compatible (AWS, Railway Bucket, MinIO, etc.) and Google Cloud Storage; switch via single configuration.
- **Enable/disable**: All upload, delete, and migrate behaviour runs only when **Cloud Storage Configuration** is enabled.
- **Automatic upload**: New File attachments (via Attach or image fields) are uploaded to the configured bucket; local file is removed and `file_url` is updated to the cloud URL.
- **Two buckets**: Separate **private** and **public** buckets. Private bucket: no public ACL; all access via signed URL. Public bucket: objects get public-read (S3) or make_public (GCS).
- **Private files**: Uploaded to the private bucket; served via time-limited signed URLs only.
- **Public files**: Uploaded to the public bucket with public read; `file_url` is the bucket's public URL.
- **Custom endpoint**: Set an endpoint URL to use any S3-compatible provider (Railway Bucket, MinIO, Cloudflare R2, etc.).
- **Disable ACL**: Toggle for providers that do not support ACLs (Railway Bucket, MinIO).
- **Delete from cloud**: Optional "Delete file from cloud when File is deleted"; when enabled, deleting a File document also deletes the object from the bucket.
- **Test connection**: Toolbar button on Cloud Storage Configuration to verify bucket access.
- **Migrate existing files**: Toolbar button to upload all existing local File records to the configured cloud (skips files already on cloud).

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app multi_cloud_storage https://github.com/aiconec/garp-cloud-storage
bench install-app multi_cloud_storage
```

Install Python dependencies (if not installed by bench):

```bash
pip install -r apps/multi_cloud_storage/requirements.txt
```

Dependencies: `boto3`, `google-cloud-storage`, `python-magic`.

## Configuration

Go to **Cloud Storage Configuration**.

| Field | Description |
|-------|-------------|
| **Enabled** | Turn cloud storage on/off. When off, no upload/delete/migrate runs. |
| **Delete file from cloud when File is deleted** | If enabled, deleting a File document also deletes the object in the bucket. |
| **Storage Provider** | `Amazon S3` or `Google Cloud Storage`. |
| **Signed URL Expiry (seconds)** | Expiry for private-file signed URLs (default 300). |
| **Folder Prefix** | Optional prefix for object keys (e.g. `garp-files`). |

### Amazon S3 / S3-Compatible

| Field | Description |
|-------|-------------|
| Private Bucket Name | Bucket for private files (required). |
| Public Bucket Name | Bucket for public files (required). |
| Region | AWS region or `auto` for S3-compatible providers. |
| Endpoint URL | Leave blank for AWS S3. Set for S3-compatible providers (e.g. `https://storage.railway.app`). |
| Disable ACL | Enable for providers that do not support ACLs (Railway Bucket, MinIO). |
| Access Key ID | Optional; omit to use IAM role or env credentials. |
| Secret Access Key | Optional; required if Access Key ID is set. |

### Railway Bucket

Set **Endpoint URL** to `https://storage.railway.app`, **Region** to `auto`, and tick **Disable ACL**. Use the `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, and `BUCKET` values from your Railway Bucket service.

### Google Cloud Storage

| Field | Description |
|-------|-------------|
| Private Bucket Name | Bucket for private files (required). |
| Public Bucket Name | Bucket for public files (required). |
| Service Account JSON | Full JSON key for a service account with access to both buckets. |

You can use the same bucket for both by setting the same name for Private and Public Bucket; private files will still be served only via signed URL. Use **Test Connection** after saving to confirm access.

## How it works

- **Upload**: On File `after_insert`, if cloud storage is enabled and the file is on disk, it is uploaded to the **private** or **public** bucket according to `is_private`. The File row is updated with the cloud `file_url` and `content_hash` (stored as `private:key` or `public:key`). The local file is removed.
- **Private files**: Stored in the private bucket; `file_url` is `/api/method/multi_cloud_storage.controller.generate_file?key=...`, which redirects to a signed URL.
- **Public files**: Stored in the public bucket with public read; `file_url` is the bucket's public URL.
- **Delete**: On File `on_trash`, if "Delete file from cloud" is enabled, the object is deleted from the correct bucket.
- **Migrate**: Same logic; each file is uploaded to the private or public bucket by its `is_private` flag.

Object keys use a path like `{folder_prefix}/{YYYY}/{MM}/{DD}/{doctype}/{random}_{filename}`.

## Customisation

- **Ignore doctypes**: In `site_config.json`, set `ignore_multi_cloud_storage_doctype` to a list of doctypes whose attachments should not be uploaded (e.g. `["Data Import", "Prepared Report"]`). "Prepared Report" is always ignored.
- **Custom key generator**: In your app's `hooks.py`, set `multi_cloud_storage_key_generator = ["your_app.utils.your_key_function"]`. The function receives `file_name`, `parent_doctype`, `parent_name` and should return the object key.

## License

MIT — Copyright (c) 2026 GARP ERP (Aiconec)
