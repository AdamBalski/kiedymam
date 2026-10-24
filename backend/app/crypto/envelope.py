import base64
import json
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.db.database import Database

LOG = logging.getLogger(__name__)


class EnvelopeCrypto:
    def __init__(self, database: Database, master_key: bytes):
        if len(master_key) != 32:
            raise ValueError("OCI Secret must decode to exactly 32 bytes")
        self.database = database
        self.master_key = master_key

    @staticmethod
    def load_secret_value(secret_ocid: str) -> bytes:
        if not secret_ocid:
            raise RuntimeError("OCI secret OCID is required")
        try:
            import oci
        except ImportError as exc:
            raise RuntimeError("OCI SDK is required to retrieve OCI secrets") from exc

        config_file = os.environ.get("OCI_CONFIG_FILE")
        if config_file:
            config = oci.config.from_file(config_file, os.environ.get("OCI_CONFIG_PROFILE", "DEFAULT"))
            client = oci.secrets.SecretsClient(config)
        else:
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            client = oci.secrets.SecretsClient(config={}, signer=signer)
        response = client.get_secret_bundle(secret_id=secret_ocid)
        content = response.data.secret_bundle_content.content
        return base64.b64decode(content, validate=True)

    @staticmethod
    def load_master_key(secret_ocid: str) -> bytes:
        stored_content = EnvelopeCrypto.load_secret_value(secret_ocid)
        if len(stored_content) == 32:
            key = stored_content
        else:
            try:
                key = base64.b64decode(stored_content.strip(), validate=True)
            except Exception as exc:
                raise RuntimeError("OCI secret content must be Base64 for a 32-byte AES-256 key") from exc
        if len(key) != 32:
            raise RuntimeError("OCI secret content must be Base64 for a 32-byte AES-256 key")
        LOG.info("Loaded envelope-encryption key from OCI Secret")
        return key

    def encrypt_for_user(self, user_id: str, value: dict) -> None:
        plaintext = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        with self.database.transaction(immediate=True) as db:
            row = db.execute("SELECT nonce, wrapped_key FROM leaf_keys WHERE user_id=?", (user_id,)).fetchone()
            if row:
                wrap_nonce = bytes(row["nonce"])
                leaf_key = AESGCM(self.master_key).decrypt(
                    wrap_nonce, bytes(row["wrapped_key"]), user_id.encode("utf-8")
                )
            else:
                leaf_key = AESGCM.generate_key(bit_length=256)
                wrap_nonce = os.urandom(12)
                wrapped_key = AESGCM(self.master_key).encrypt(
                    wrap_nonce, leaf_key, user_id.encode("utf-8")
                )
                db.execute(
                    "INSERT INTO leaf_keys(user_id, nonce, wrapped_key, created_at) VALUES (?, ?, ?, datetime('now'))",
                    (user_id, wrap_nonce, wrapped_key),
                )
            nonce = os.urandom(12)
            ciphertext = AESGCM(leaf_key).encrypt(nonce, plaintext, user_id.encode("utf-8"))
            db.execute(
                """INSERT INTO oauth_credentials(user_id, nonce, ciphertext, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(user_id) DO UPDATE SET nonce=excluded.nonce,
                     ciphertext=excluded.ciphertext, updated_at=excluded.updated_at""",
                (user_id, nonce, ciphertext),
            )

    def decrypt_for_user(self, user_id: str) -> dict | None:
        with self.database.read() as db:
            key_row = db.execute("SELECT nonce, wrapped_key FROM leaf_keys WHERE user_id=?", (user_id,)).fetchone()
            data_row = db.execute("SELECT nonce, ciphertext FROM oauth_credentials WHERE user_id=?", (user_id,)).fetchone()
        if not key_row or not data_row:
            return None
        leaf_key = AESGCM(self.master_key).decrypt(
            bytes(key_row["nonce"]), bytes(key_row["wrapped_key"]), user_id.encode("utf-8")
        )
        plaintext = AESGCM(leaf_key).decrypt(
            bytes(data_row["nonce"]), bytes(data_row["ciphertext"]), user_id.encode("utf-8")
        )
        return json.loads(plaintext)

    def encrypt_payload(self, user_id: str, context: str, value: dict) -> tuple[bytes, bytes]:
        """Encrypt per-user auxiliary data with the existing wrapped leaf key."""
        with self.database.read() as db:
            row = db.execute("SELECT nonce, wrapped_key FROM leaf_keys WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            raise RuntimeError("User encryption key is not initialized")
        leaf_key = AESGCM(self.master_key).decrypt(
            bytes(row["nonce"]), bytes(row["wrapped_key"]), user_id.encode("utf-8")
        )
        nonce = os.urandom(12)
        aad = f"{user_id}:{context}".encode("utf-8")
        plaintext = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return nonce, AESGCM(leaf_key).encrypt(nonce, plaintext, aad)

    def decrypt_payload(self, user_id: str, context: str, nonce: bytes, ciphertext: bytes) -> dict:
        with self.database.read() as db:
            row = db.execute("SELECT nonce, wrapped_key FROM leaf_keys WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            raise RuntimeError("User encryption key is not initialized")
        leaf_key = AESGCM(self.master_key).decrypt(
            bytes(row["nonce"]), bytes(row["wrapped_key"]), user_id.encode("utf-8")
        )
        aad = f"{user_id}:{context}".encode("utf-8")
        return json.loads(AESGCM(leaf_key).decrypt(nonce, ciphertext, aad))
