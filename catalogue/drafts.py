import json
from hashlib import sha256


def build_revision(build):
    return sha256(json.dumps(build, sort_keys=True).encode()).hexdigest()
