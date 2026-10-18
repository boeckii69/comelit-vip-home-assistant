"""One-use remote audio permission, granted only by an authenticated HA action."""
import hmac
import re
import time


def valid_gateway_key(value):
    return isinstance(value, str) and (not value or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value))


class RemoteGate:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.until = 0.0
        self.granted_at = None
        self.consumed_at = None

    @property
    def available(self):
        return self.clock() < self.until

    def grant(self):
        self.until = self.clock() + 30.0
        self.granted_at = time.time()

    def revoke(self):
        self.until = 0.0

    def consume(self):
        if not self.available:
            return False
        self.revoke()
        self.consumed_at = time.time()
        return True

    @staticmethod
    def authenticate(message, configured_key):
        values = message.headers.get("x-comelit-gateway", [])
        return (bool(configured_key) and len(values) == 1
                and hmac.compare_digest(values[0].encode(), configured_key.encode()))

    def diagnostics(self):
        return {"remote_audio_authorized": self.available,
                "remote_audio_seconds_remaining": max(0, round(self.until - self.clock(), 1)),
                "remote_audio_granted_at": self.granted_at,
                "remote_audio_consumed_at": self.consumed_at}
