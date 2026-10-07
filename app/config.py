import os
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./certificates.db")
CERTIFICATE_DIR = Path(os.getenv("CERTIFICATE_DIR", "./generated"))
MAX_RECIPIENTS_PER_JOB = int(os.getenv("MAX_RECIPIENTS_PER_JOB", "1000"))
