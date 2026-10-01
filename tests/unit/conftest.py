"""Provide ephemeral authentication material for isolated unit-test processes."""

import os
import secrets

os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
