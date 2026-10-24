from __future__ import annotations

import base64
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.crypto.envelope import EnvelopeCrypto
from app.push import PushService


class PushServiceTests(unittest.TestCase):
    def test_public_application_key_is_derived_from_private_key_secret(self):
        private = ec.derive_private_key(7, ec.SECP256R1())
        scalar = private.private_numbers().private_value.to_bytes(32, "big")
        private_value = base64.urlsafe_b64encode(scalar).rstrip(b"=").decode("ascii")
        expected_public = base64.urlsafe_b64encode(
            private.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        ).rstrip(b"=").decode("ascii")
        settings = SimpleNamespace(
            web_push_secret_ocid="ocid1.secret",
            web_push_subject="mailto:admin@example.org",
            app_origin="https://example.org",
            app_base_path="/kiedymam",
        )

        with patch.object(EnvelopeCrypto, "load_secret_value", return_value=private_value.encode()) as load:
            service = PushService(repository=None, crypto=None, settings=settings)

        load.assert_called_once_with("ocid1.secret")
        self.assertTrue(service.configured)
        self.assertEqual(service.private_key, private_value)
        self.assertEqual(service.public_key, expected_public)

    def test_accepts_pem_private_key_secret_and_derives_the_public_key(self):
        private = ec.derive_private_key(9, ec.SECP256R1())
        pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        private_value, public_value = PushService.derive_vapid_keys(pem)
        self.assertEqual(len(base64.urlsafe_b64decode(private_value + "=")), 32)
        self.assertEqual(len(base64.urlsafe_b64decode(public_value + "=")), 65)
        self.assertEqual(public_value[0], "B")

    def test_rejects_invalid_vapid_private_secret(self):
        with self.assertRaisesRegex(ValueError, "VAPID secret"):
            PushService.derive_vapid_keys(b"not-a-private-key")


if __name__ == "__main__":
    unittest.main()
