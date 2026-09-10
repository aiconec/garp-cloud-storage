# Copyright (c) 2026, Bhushan Barbuddhe and contributors
# For license information, please see license.txt

import string
from abc import ABC, abstractmethod

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
