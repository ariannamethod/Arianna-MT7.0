import os

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
CACHE_TTL = float(os.getenv("CACHE_TTL", "300"))
