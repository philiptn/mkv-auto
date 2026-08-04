#!/usr/bin/env python3
"""Answer output-path lookups from a shared folder.

The qBittorrent live-copy integration needs to know where MKV-Auto will put a
file *before* it finishes downloading, so it can stream the in-progress file
straight to its final destination. The two services already share the input
folder (a 1:1 bind mount, NFS/SMB when they are on different hosts), so the
lookup rides that mount instead of a network port or the Docker socket.

Protocol, per request:

    requester   write <uuid>.req.tmp, rename -> <uuid>.req   {"v":1,"path":...}
    worker      resolve, write <uuid>.res.tmp, rename -> <uuid>.res, drop .req
    requester   read <uuid>.res, delete it

Both sides publish by rename so a half-written file is never observed - the
same trick the qBittorrent integration uses for its own copies.

Resolution shells out to `mkv-auto.py --resolve-path` rather than importing the
modules, because modules/misc.py reads defaults.ini + user.ini at *import* time
relative to the CWD and service-entrypoint-inner.sh refreshes user.ini from the
config mount every 5 seconds. A fresh subprocess therefore always sees the
service's current config, so the preview cannot drift from what a real run
produces.
"""

import json
import os
import subprocess
import sys
import time

MKV_AUTO_DIR = os.environ.get('MKV_AUTO_DIR', '/mkv-auto')
QUEUE_DIR = os.environ.get(
    'RESOLVE_QUEUE_DIR', os.path.join(MKV_AUTO_DIR, 'files/input/.mkv-auto-resolve'))
POLL_INTERVAL = float(os.environ.get('RESOLVE_POLL_INTERVAL', '1'))
RESOLVE_TIMEOUT = float(os.environ.get('RESOLVE_TIMEOUT', '180'))

# A request whose requester died before we answered, and an answer nobody ever
# claimed, both have to be reaped or the queue folder grows without bound.
REQ_MAX_AGE = 600
RES_MAX_AGE = 3600
GC_INTERVAL = 60


def log(message):
    print(f"[resolve-worker] {message}", flush=True)


def resolve(path):
    """Return the resolution dict for `path`, or raise."""
    result = subprocess.run(
        [sys.executable, os.path.join(MKV_AUTO_DIR, 'mkv-auto.py'),
         '--resolve-path', path, '--relative', '--json'],
        cwd=MKV_AUTO_DIR, capture_output=True, text=True, timeout=RESOLVE_TIMEOUT,
    )
    if result.returncode != 0 or not result.stdout.strip():
        stderr = (result.stderr or '').strip().splitlines()
        detail = stderr[-1] if stderr else f"exit {result.returncode}"
        raise RuntimeError(detail)
    return json.loads(result.stdout)


def publish(directory, name, payload):
    """Write payload as `name`, atomically."""
    final = os.path.join(directory, name)
    tmp = os.path.join(directory, f".{name}.tmp")
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(tmp, final)


def handle(directory, req_name):
    uuid = req_name[:-len('.req')]
    req_path = os.path.join(directory, req_name)

    try:
        with open(req_path, 'r', encoding='utf-8') as handle:
            request = json.load(handle)
        path = request.get('path', '')
    except Exception as e:
        log(f"unreadable request {req_name}: {e}")
        _unlink(req_path)
        return

    # The path names a file under the input root and is used to build a
    # destination; refuse anything that could escape it.
    if not path or path.startswith('/') or '..' in path.replace('\\', '/').split('/'):
        payload = {'ok': False, 'error': f"invalid path: {path!r}"}
    else:
        try:
            target = resolve(path)
            payload = {'ok': True}
            payload.update(target)
        except Exception as e:
            log(f"failed to resolve {path!r}: {e}")
            payload = {'ok': False, 'error': str(e)}

    try:
        publish(directory, f"{uuid}.res", payload)
    except Exception as e:
        log(f"failed to write response for {req_name}: {e}")
    finally:
        _unlink(req_path)

    if payload.get('ok'):
        log(f"{path!r} -> {payload.get('relative_path')!r}")


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def collect_garbage(directory):
    now = time.time()
    for name in _listdir(directory):
        if name.endswith('.req'):
            max_age = REQ_MAX_AGE
        elif name.endswith('.res') or name.endswith('.tmp'):
            max_age = RES_MAX_AGE
        else:
            continue
        path = os.path.join(directory, name)
        try:
            if now - os.path.getmtime(path) > max_age:
                _unlink(path)
                log(f"reaped stale {name}")
        except OSError:
            pass


def _listdir(directory):
    try:
        return os.listdir(directory)
    except OSError:
        return []


def main():
    log(f"watching {QUEUE_DIR}")
    last_gc = time.monotonic()

    while True:
        # Created here rather than once at startup so the worker recovers if the
        # queue is removed underneath it - by a user clearing out the input
        # folder, or a mount coming back.
        try:
            os.makedirs(QUEUE_DIR, exist_ok=True)
        except OSError as e:
            log(f"cannot create {QUEUE_DIR}: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        for name in sorted(_listdir(QUEUE_DIR)):
            if name.endswith('.req'):
                handle(QUEUE_DIR, name)

        if time.monotonic() - last_gc > GC_INTERVAL:
            collect_garbage(QUEUE_DIR)
            last_gc = time.monotonic()

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
