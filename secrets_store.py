"""Token storage: OS keyring preferred (encrypted at rest by the OS), JSON-file fallback.

Token *values* are never logged. A legacy plaintext JSON store is detected and migrated
into the keyring with a clear warning.
"""
import json
import os

SERVICE = "who-stresses-me-out"
_warned = False


def _kr():
    """Return a usable keyring module, or None if there is no real backend."""
    try:
        import keyring
        backend = keyring.get_keyring()
        if "fail" in type(backend).__module__.lower():  # chainer/null backend
            return None
        return keyring
    except Exception:
        return None


def get_blob(key, file_fallback=None):
    """Return the stored dict for `key`. If a keyring is available and a legacy file
    exists, migrate it into the keyring and advise deleting the file."""
    kr = _kr()
    if kr:
        v = kr.get_password(SERVICE, key)
        if v:
            return json.loads(v)
        if file_fallback and os.path.exists(file_fallback):
            with open(file_fallback, encoding="utf-8") as f:
                data = json.load(f)
            kr.set_password(SERVICE, key, json.dumps(data))
            print(f"[secrets] Migrated {file_fallback} into the OS keyring. "
                  f"You can safely delete {file_fallback} now.")
            return data
        return None
    if file_fallback and os.path.exists(file_fallback):
        with open(file_fallback, encoding="utf-8") as f:
            return json.load(f)
    return None


def set_blob(key, data, file_fallback=None):
    """Persist a dict for `key`. Returns 'keyring' or 'file'."""
    kr = _kr()
    if kr:
        kr.set_password(SERVICE, key, json.dumps(data))
        return "keyring"
    global _warned
    if not _warned:
        print("[secrets] WARNING: no OS keyring backend available — storing tokens in plaintext "
              f"{file_fallback}. Install `keyring` for encrypted storage.")
        _warned = True
    if not file_fallback:
        raise RuntimeError("No keyring and no file fallback configured.")
    with open(file_fallback, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(file_fallback, 0o600)
    except OSError:
        pass
    return "file"
