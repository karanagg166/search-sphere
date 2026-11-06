"""Credential-shaped and configured secrets must not enter inference or responses."""

import re

from src.config import settings

_SECRET = re.compile(r"(?:sb_secret_[A-Za-z0-9_-]{8,}|\bsk-[A-Za-z0-9_-]{16,}|\bBearer\s+[A-Za-z0-9_.-]{16,}|postgres(?:ql)?(?:\+asyncpg)?://[^\s]+:[^\s]+@|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)", re.I)


def contains_secrets(text):
    if _SECRET.search(text):
        return True
    for name in ["COHERE_API_KEY","JWT_SECRET_KEY","QUICK_CLINIC_SERVICE_SECRET","SUPABASE_SECRET_KEY",
                 "DATABASE_URL","GOOGLE_CLIENT_SECRET","GITHUB_CLIENT_SECRET"]:
        value=getattr(settings,name,None)
        if isinstance(value,str) and len(value)>=8 and value in text:
            return True
    return False
