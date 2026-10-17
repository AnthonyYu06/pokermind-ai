"""Small dependency-free helpers shared by independent research programs."""
import hashlib
from packages.pokermind_core.contracts import canonical


def immutable_json(path, value):
    data = canonical(value).encode(); sha = hashlib.sha256(data).hexdigest()
    destination = path.with_name(path.stem + '-' + sha[:16] + '.json')
    if not destination.exists(): destination.write_bytes(data)
    return destination, sha
