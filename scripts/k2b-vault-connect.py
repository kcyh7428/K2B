#!/usr/bin/env python3
"""Restore and verify the approved NAS mount for active K2B work."""

import argparse
import fcntl
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import tempfile
import time

SOURCE = "//cheung@100.75.5.21/Keith-Work"
MOUNT_POINTS = {
    "keithmbpm2": Path("/Users/keithmbpm2/Projects/K2B-NAS"),
    "keithcheung": Path("/Volumes/Keith-Work"),
}


def mounted(mount: Path) -> bool:
    try:
        lines = subprocess.check_output(["/sbin/mount"], text=True, timeout=2).splitlines()
    except subprocess.TimeoutExpired as exc:
        raise ValueError("the mount-table check timed out; mount state is unknown") from exc
    at_target = [line for line in lines if f" on {mount} (" in line]
    if not at_target:
        if mount == MOUNT_POINTS["keithcheung"] and any(
            line.startswith(f"{SOURCE} on {mount}-") for line in lines
        ):
            raise ValueError("the NAS share is mounted at a numbered SJM volume path; "
                             "eject that copy normally before reconnecting")
        return False
    if len(at_target) != 1 or not at_target[0].startswith(f"{SOURCE} on {mount} (smbfs,"):
        raise ValueError(f"{mount} is occupied by a mount that does not match {SOURCE}")
    if "read-only" in at_target[0]:
        raise ValueError(f"{mount} is occupied by a read-only SMB mount")
    return True


def reconnect(mount: Path, actor: str) -> None:
    if mount.is_symlink():
        raise ValueError("the mount point is a symlink")
    if actor == "keithmbpm2":
        mount.mkdir(exist_ok=True)
        if mount.resolve(strict=True) != mount or next(mount.iterdir(), None) is not None:
            raise ValueError("the Home mount point must be a real empty directory")
    else:
        if mount.exists():
            raise ValueError("the unmounted SJM volume path is occupied")
        if Path("/dev/console").stat().st_uid != os.getuid():
            raise ValueError("sign in to the SJM Mac to reconnect the Finder mount")

    endpoint = SOURCE.split("@", 1)[1].split("/", 1)[0]
    try:
        with socket.create_connection((endpoint, 445), timeout=1.5):
            pass
    except OSError as exc:
        raise ValueError(f"cannot reach the NAS over Tailscale at {endpoint}:445: {exc}") from exc
    source = Path(__file__).resolve().parent / "lib/k2b-vault-mount.c"
    with tempfile.TemporaryDirectory(prefix="k2b-vault-connect-") as scratch:
        executable = str(Path(scratch) / "mount")
        try:
            subprocess.run(
                ["/usr/bin/clang", str(source), "-framework", "NetFS",
                 "-framework", "CoreFoundation", "-o", executable],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=15,
            )
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"could not build the macOS SMB helper; check Command Line Tools: "
                             f"{exc.stderr.strip()[:500]}") from exc
        if mounted(mount):
            return
        if actor == "keithmbpm2":
            if mount.resolve(strict=True) != mount or next(mount.iterdir(), None) is not None:
                raise ValueError("the Home mount point changed while preparing the reconnect")
        elif mount.exists() or mount.is_symlink():
            raise ValueError("the SJM mount point became occupied before the reconnect")
        command = [executable, "smb:" + SOURCE]
        if actor == "keithmbpm2":
            command.append(str(mount))
        mount_error = "the SMB mount did not appear"
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=20)
            if result.returncode:
                if not mounted(mount):
                    raise ValueError(f"{result.stderr.strip()}; check the Mac's saved SMB sign-in")
                return
        except subprocess.TimeoutExpired:
            mount_error = "macOS did not complete the SMB mount within the reconnect window"
    deadline = time.monotonic() + 8
    while not mounted(mount):
        if time.monotonic() >= deadline:
            raise ValueError(mount_error)
        time.sleep(0.25)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="verify the existing mount")
    args = parser.parse_args()
    actor = pwd.getpwuid(os.getuid()).pw_name
    home = Path("/Users") / actor
    mount = MOUNT_POINTS.get(actor)
    if mount is None or Path.home() != home:
        parser.exit(1, "K2B vault unavailable: unsupported Mac account.\n")
    alias = home / "Projects/K2B-Vault"
    target = mount / "K2B-Vault"
    try:
        if not alias.is_symlink() or alias.readlink() != target:
            raise ValueError("the usual vault shortcut has the wrong target")
        if not mounted(mount):
            if args.check_only:
                raise ValueError("the approved SMB mount is absent")
            state = home / ".local/state/k2b"
            state.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock = os.open(state / "vault-connect.lock",
                           os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ValueError("another K2B vault reconnect is in progress") from exc
                if not mounted(mount):
                    reconnect(mount, actor)
            finally:
                os.close(lock)
        probe = subprocess.run(
            [sys.executable, "-I", "-c",
             "from pathlib import Path; import sys\n"
             "p = Path(sys.argv[1])\n"
             "target = Path(sys.argv[2])\n"
             "index_path = p / 'wiki/index.md'\n"
             "try:\n"
             "    if p.resolve(strict=True) != target: raise SystemExit(2)\n"
             "    if not index_path.resolve(strict=True).is_relative_to(target): raise SystemExit(2)\n"
             "    with index_path.open('rb') as index:\n"
             "        if not index.read(4096): raise SystemExit(3)\n"
             "except OSError: raise SystemExit(4)",
             str(alias), str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=12,
        )
        if probe.returncode:
            raise ValueError({
                2: "the shortcut or index resolves outside the verified volume",
                3: "wiki/index.md is empty",
                4: "the shortcut or wiki/index.md is unreadable",
            }.get(probe.returncode, "the vault index probe failed"))
        if not mounted(mount):
            raise ValueError("the verified SMB mount disappeared during the index read")
    except subprocess.TimeoutExpired:
        print("K2B vault unavailable: the SMB helper or vault index probe timed out", file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"K2B vault unavailable: {exc}", file=sys.stderr)
        return 1
    print(f"K2B vault readable: {SOURCE} on {mount} via {alias}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
