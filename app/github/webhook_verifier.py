import hashlib
import hmac


class WebhookVerificationError(RuntimeError):
  """Raised when a webhook signature is missing, malformed, or invalid."""


def verify_signature(*, secret: str, body: bytes, signature_header: str | None) -> None:
  """Verify X-Hub-Signature-256 against the raw request body.

  Uses HMAC-SHA256 with constant-time comparison. Raises
  WebhookVerificationError on any failure; returns None on success.
  """
  if not signature_header:
    raise WebhookVerificationError("Missing X-Hub-Signature-256 header")

  if not signature_header.startswith("sha256="):
    raise WebhookVerificationError("Malformed signature header")

  expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
  provided = signature_header.removeprefix("sha256=")

  if not hmac.compare_digest(expected, provided):
    raise WebhookVerificationError("Signature mismatch")
