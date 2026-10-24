from __future__ import annotations

import base64
from hashlib import sha256
import json
import logging
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.crypto.envelope import EnvelopeCrypto
from app.db.repository import Repository

LOG = logging.getLogger(__name__)


class PushService:
    def __init__(self, repository: Repository, crypto: EnvelopeCrypto, settings):
        self.repository = repository
        self.crypto = crypto
        self.private_key = None
        self.public_key = None
        if settings.web_push_secret_ocid:
            secret = EnvelopeCrypto.load_secret_value(settings.web_push_secret_ocid)
            self.private_key, self.public_key = self.derive_vapid_keys(secret)
        self.subject = settings.web_push_subject
        self.public_url = settings.app_origin + settings.app_base_path + "/?tab=notifications"

    @staticmethod
    def derive_vapid_keys(secret: bytes) -> tuple[str, str]:
        """Read a P-256 private key secret and derive its public application key."""
        secret = secret.strip()
        if secret.startswith(b"-----BEGIN"):
            private = serialization.load_pem_private_key(secret, password=None)
            if not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(
                private.curve, ec.SECP256R1
            ):
                raise ValueError("VAPID PEM secret must contain a P-256 private key")
        else:
            try:
                encoded = secret.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ValueError("VAPID secret must be a Base64URL P-256 private key or PEM") from exc
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}=?", encoded):
                raise ValueError("VAPID secret must be a Base64URL P-256 private key or PEM")
            encoded += "=" * ((4 - len(encoded) % 4) % 4)
            try:
                scalar = base64.b64decode(encoded, altchars=b"-_", validate=True)
            except ValueError as exc:
                raise ValueError("VAPID secret must be a Base64URL P-256 private key or PEM") from exc
            if len(scalar) != 32:
                raise ValueError("VAPID secret must be a Base64URL P-256 private key or PEM")
            private = ec.derive_private_key(int.from_bytes(scalar, "big"), ec.SECP256R1())

        private_bytes = private.private_numbers().private_value.to_bytes(32, "big")
        public_bytes = private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        private_value = base64.urlsafe_b64encode(private_bytes).rstrip(b"=").decode("ascii")
        public_value = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
        return private_value, public_value

    @property
    def configured(self) -> bool:
        return bool(self.public_key and self.private_key)

    def send_notifications(self, notifications: list[dict]) -> None:
        if not notifications or not self.configured:
            return
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            LOG.warning("Push is configured but pywebpush is unavailable")
            return
        for notification in notifications:
            for record in self.repository.push_subscriptions(notification["user_id"], self.crypto):
                endpoint = record["subscription"].get("endpoint", "")
                try:
                    webpush(
                        subscription_info=record["subscription"],
                        data=json.dumps({
                            "id": notification["id"],
                            "title": "KiedyMam",
                            "body": notification["message"],
                            "url": self.public_url,
                        }, ensure_ascii=False),
                        vapid_private_key=self.private_key,
                        vapid_claims={"sub": self.subject},
                        timeout=10,
                    )
                except WebPushException as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status in (404, 410):
                        self.repository.delete_push_subscription_by_hash(
                            sha256(endpoint.encode("utf-8")).hexdigest()
                        )
                    LOG.info("Push delivery failed status=%s", status)
                except Exception:
                    LOG.exception("Push delivery failed")
