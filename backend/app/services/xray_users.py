"""Update VLESS users without restarting unrelated connections."""
import copy
import json
import os
import re
import tempfile
from app.services.shell import run_cmd


def user_changes(previous, desired):
    """Return operations only when every change is a VLESS user change."""
    old, new = copy.deepcopy(previous), copy.deepcopy(desired)
    changes = []
    if "HandlerService" not in old.get("api", {}).get("services", []):
        return None
    old_inbounds, new_inbounds = old.get("inbounds", []), new.get("inbounds", [])
    if len(old_inbounds) != len(new_inbounds):
        return None
    for before, after in zip(old_inbounds, new_inbounds):
        if before.get("protocol") != "vless":
            continue
        old_users = before.get("settings", {}).pop("clients", [])
        new_users = after.get("settings", {}).pop("clients", [])
        if old_users == new_users:
            continue
        tag = before.get("tag")
        if not tag:
            return None
        a, b = {}, {}
        for users, target in ((old_users, a), (new_users, b)):
            for user in users:
                email = user.get("email", "")
                if not re.fullmatch(r"c\d+-[a-z0-9_]+@md-next", email) or email in target:
                    return None
                target[email] = user
        removed = [u for email, u in a.items() if b.get(email) != u]
        added = [u for email, u in b.items() if a.get(email) != u]
        changes.append((tag, removed, added))
    if old != new:
        return None
    return changes


async def apply_user_changes(previous, desired):
    changes = user_changes(previous, desired)
    # rmu rejects new authentication but leaves existing streams alive.
    # Any removal/replacement must use the full restart path to revoke access.
    if changes is None or any(removed for _, removed, _ in changes):
        return False
    endpoint = next((i for i in previous["inbounds"] if i.get("tag") == "api-in"), None)
    if not endpoint or endpoint.get("listen") != "127.0.0.1":
        return False
    server = f"--server=127.0.0.1:{int(endpoint['port'])}"
    completed = []

    async def operation(tag, users, add):
        if not users:
            return
        if add:
            # HandlerService needs users only; avoid reading root-owned TLS keys
            # or copying the transport's private Reality key into a CLI file.
            inbound = {"tag": tag, "protocol": "vless",
                       "port": next(i["port"] for i in desired["inbounds"] if i.get("tag") == tag),
                       "settings": {"clients": users, "decryption": "none"}}
            fd, path = tempfile.mkstemp(prefix="md-next-users-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf8") as stream:
                    json.dump({"inbounds": [inbound]}, stream)
                code, output, _ = await run_cmd("xray", "api", "adu", server, path, timeout=15)
            finally:
                os.unlink(path)
            expected = f"Added {len(users)} user(s) in total."
        else:
            code, output, _ = await run_cmd("xray", "api", "rmu", server, f"-tag={tag}", *[u["email"] for u in users], timeout=15)
            expected = f"Removed {len(users)} user(s) in total."
        # Xray CLI can return zero even if an individual operation failed.
        if code or expected not in output:
            raise RuntimeError("Xray не подтвердил изменение пользователей через API")

    try:
        # One user per call gives a precise rollback journal.
        for tag, removed, added in changes:
            for add, users in ((False, removed), (True, added)):
                for user in users:
                    await operation(tag, [user], add)
                    completed.append((tag, user, add))
    except Exception:
        for tag, user, add in reversed(completed):
            await operation(tag, [user], not add)
        raise
    return True
