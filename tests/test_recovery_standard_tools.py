"""Synthetic component proofs for the bounded standard-tool recovery method.

Scope: command/control behavior only over invented ``tmp_path`` fixtures.
All subprocesses use ``LC_ALL=C`` and ``COPYFILE_DISABLE=1``. No production
paths, real roots, NAS or provider calls occur here.

Explicitly OUT OF SCOPE for this suite: path/member INPUT ADMISSION safety
checks and privacy scope checks.  Those are P1 integration obligations and
are not tested here.  ``normalize_member`` is intended only for the known,
invented fixture members below; it is not a production validation routine.
"""

import os
import signal
import stat
import subprocess
import sys
import tarfile
import time
import tempfile
import threading
from pathlib import PurePosixPath, Path
import select

import pytest

TOOL_ENV_EXTRA = {"LC_ALL": "C", "COPYFILE_DISABLE": "1"}
TAR = "/usr/bin/tar"
XARGS = "/usr/bin/xargs"
SHASUM = "/usr/bin/shasum"

BIG_FILE_SIZE = 17735133


def tool_env():
    env = dict(os.environ)
    for key in ("PERL5OPT", "PERL5LIB", "PERLLIB"):
        env.pop(key, None)
    env.update(TOOL_ENV_EXTRA)
    return env


def require_tools():
    missing = [p for p in (TAR, XARGS, SHASUM) if not os.path.exists(p)]
    if missing:
        pytest.skip("required standard tools missing: %r" % (missing,))


@pytest.fixture
def umask077():
    old = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(old)


def exclusive_mkdir(path, mode=0o700):
    os.mkdir(str(path), mode)
    return path


def exclusive_file(path, mode, data=b""):
    p = str(path)
    if os.path.islink(p):
        raise FileExistsError("refusing symlink: %s" % p)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        if data:
            os.write(fd, data)
    finally:
        os.close(fd)
    return path


def write_bytes(path, data, mode):
    path = type(path)(path) if not isinstance(path, type(path)) else path
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)
    return path


def make_binary(size, seed):
    block = bytes(((seed + i) * 131 % 256) for i in range(4096))
    return (block * (size // len(block) + 1))[:size]


def set_ns_mtime(path, ns):
    os.utime(path, ns=(ns, ns))


def source_signature(path):
    st = os.lstat(path)
    return {"dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns,
            "mode": st.st_mode & 0o7777, "nlink": st.st_nlink}


def admit_regular(path):
    st = os.lstat(path)
    admit_mode(path)
    if os.path.islink(path):
        raise ValueError("symlink refused")
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("not regular")
    if st.st_nlink != 1:
        raise ValueError("hard link refused")
    return st


def admit_mode(path):
    if stat.S_IMODE(os.lstat(path).st_mode) & 0o7000:
        raise ValueError("special permission bits refused")


def run_argv(cmd, timeout=60, stdin_bytes=None):
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=tool_env(), start_new_session=True)
    try:
        out, err = proc.communicate(stdin_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate(timeout=5)
        raise
    if proc.returncode != 0:
        raise AssertionError("command failed %r rc=%d stderr=%r" %
                             (cmd, proc.returncode, err))
    if err:
        raise AssertionError("unexpected stderr %r from %r" % (err, cmd))
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def tar_create(archive, source_root, entries_nul):
    if os.path.lexists(archive):
        raise FileExistsError("archive path occupied")
    run_argv([TAR, "--format=pax", "--no-acls", "--no-xattrs", "--no-fflags",
              "-cpf", str(archive), "-C", str(source_root),
              "--no-recursion", "--null", "-T", str(entries_nul)])
    created = os.lstat(archive)
    assert stat.S_ISREG(created.st_mode)
    assert created.st_uid == os.getuid()
    assert created.st_nlink == 1


def tar_extract(archive, dest):
    run_argv([TAR, "-xpf", str(archive), "-C", str(dest),
              "--preserve-permissions", "--no-same-owner", "--no-acls",
              "--no-xattrs", "--no-mac-metadata", "--no-fflags"])


def shasum_files(paths):
    if not paths:
        raise ValueError("empty selection refused before dispatch")
    nul = b"".join(os.fsencode(p) + b"\0" for p in paths)
    proc = run_argv([XARGS, "-0", "-n", "128", "-s", "32768",
                     SHASUM, "-a", "256", "--"], stdin_bytes=nul, timeout=120)
    return proc.stdout.decode("utf-8")


def shasum_one(path):
    proc = run_argv([SHASUM, "-a", "256", "--", str(path)])
    return proc.stdout.decode("utf-8").split("  ", 1)[0]


def parse_ledger(text, expected_paths):
    expected = set(os.fsencode(p) for p in expected_paths)
    got = {}
    if not expected_paths:
        raise ValueError("empty list refused")
    for raw in text.split("\n"):
        if not raw:
            continue
        if len(raw) < 66 or raw[64:66] != "  ":
            raise ValueError("malformed shasum line: %r" % raw)
        hexpart = raw[:64]
        if any(c not in "0123456789abcdef" for c in hexpart):
            raise ValueError("bad digest: %r" % hexpart)
        pathbytes = os.fsencode(raw[66:])
        if pathbytes not in expected:
            raise ValueError("unknown path: %r" % raw[66:])
        if pathbytes in got:
            raise ValueError("duplicate path: %r" % raw[66:])
        got[pathbytes] = hexpart
    missing = expected - set(got)
    if missing:
        raise ValueError("missing paths: %r" % sorted(missing))
    return got


def compare_ledgers(expected_text, actual_text, expected_paths):
    exp = parse_ledger(expected_text, expected_paths)
    act = parse_ledger(actual_text, expected_paths)
    if exp != act:
        raise AssertionError("ledger digest mismatch")
    return True


def normalize_member(name):
    p = PurePosixPath(name)
    return "." if str(p) in (".", "") else str(p)


def build_fixture(run_root):
    source_root = exclusive_mkdir(run_root / "source_root")
    tree = {
        "assets/root/legacy/hidden/big.bin": ("bin", BIG_FILE_SIZE),
        "assets/root/legacy/notes.txt": ("t", b"legacy notes invented\n"),
        "assets/root/hidden/version.json": ("t", b'{"version":"1.2.3"}\n'),
        "assets/root/pending/todo.md": ("t", b"- pending invented item\n"),
        "private/receipts/r1.receipt": ("t", b"invented receipt alpha\n"),
        "private/outbox/o1.msg": ("t", b"invented outbox message\n"),
        "root_note.txt": ("t", b"root-level invented note\n"),
        ".stversions/secret.txt": ("t", b"invented hidden version\n"),
    }
    base_ns = 1_600_000_000_000_000_000
    files = []
    for i, (rel, spec) in enumerate(sorted(tree.items())):
        data = make_binary(spec[1], i) if spec[0] == "bin" else spec[1]
        p = write_bytes(source_root / rel, data, 0o664)
        set_ns_mtime(p, base_ns + i * 1_000_000_007)
        files.append(p)
    dirs = []
    for dirpath, dirnames, _ in os.walk(source_root):
        for d in dirnames:
            dp = type(source_root)(dirpath) / d
            os.chmod(dp, 0o775)
            set_ns_mtime(dp, base_ns + 500 + len(dirs) * 1_000_003)
            dirs.append(dp)
    os.chmod(source_root, 0o775)
    set_ns_mtime(source_root, base_ns + 1)
    entries = [str(d.relative_to(source_root)) for d in dirs]
    entries.append(".")
    entries.extend(str(p.relative_to(source_root)) for p in files)
    return source_root, files, sorted(set(entries)), dirs


def expected_dir_set(files_rel):
    dirs = set()
    for rel in files_rel:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return dirs


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD tar proof is macOS-specific")
def test_tar_roundtrip_three_ledgers(tmp_path, umask077):
    require_tools()
    run_root = exclusive_mkdir(tmp_path / "run")
    control = exclusive_mkdir(run_root / "control")
    source_root, source_files, entries, source_dirs = build_fixture(run_root)

    entries_nul = exclusive_file(control / "entries.nul", 0o600,
                                 b"".join(os.fsencode(e) + b"\0" for e in entries))

    src_paths = [str(p) for p in source_files]
    r1_text = shasum_files(src_paths)
    r1 = parse_ledger(r1_text, src_paths)
    assert len(r1) == len(source_files)
    src_sigs = {str(p): source_signature(p) for p in source_files}
    for sig in src_sigs.values():
        assert sig["nlink"] == 1 and sig["mode"] == 0o664
        assert (os.stat(source_root).st_mode & 0o7777) == 0o775

    archive = control / "transfer.tar"
    tar_create(archive, source_root, entries_nul)
    sha1 = shasum_one(archive)

    with tarfile.open(archive, "r:") as tf:
        members = tf.getmembers()
    names = [normalize_member(m.name) for m in members]
    assert len(names) == len(set(names))
    expected_files = {str(p.relative_to(source_root)) for p in source_files}
    expected_dirs = expected_dir_set(expected_files) | {"."}
    got_files, got_dirs = set(), set()
    for m in members:
        n = normalize_member(m.name)
        if m.isdir():
            got_dirs.add(n)
        elif m.isreg():
            got_files.add(n)
        else:
            pytest.fail("unexpected member type: %s" % n)
    assert got_files == expected_files
    assert got_dirs == expected_dirs

    dest = exclusive_mkdir(run_root / "inactive_restore")
    assert shasum_one(archive) == sha1
    tar_extract(archive, dest)
    sha2 = shasum_one(archive)
    assert sha1 == sha2

    out_files = []
    out_dirs = set()
    for dirpath, dirnames, filenames in os.walk(dest):
        rel_d = str(type(dest)(dirpath).relative_to(dest))
        if rel_d != ".":
            out_dirs.add(rel_d)
        for d in dirnames:
            out_dirs.add(str(type(dest)(dirpath).relative_to(dest) / d))
        for f in filenames:
            fp = type(dest)(dirpath) / f
            assert stat.S_ISREG(os.lstat(fp).st_mode)
            out_files.append(fp)
    assert {str(p.relative_to(dest)) for p in out_files} == expected_files
    assert out_dirs == expected_dirs - {"."}

    out_text = shasum_files([str(p) for p in out_files])
    exp_map = {os.fsencode(str(p.relative_to(source_root))): r1[os.fsencode(str(p))]
               for p in source_files}
    absolute_output = parse_ledger(out_text, [str(p) for p in out_files])
    act_parse = {os.fsencode(str(p.relative_to(dest))): absolute_output[os.fsencode(str(p))]
                 for p in out_files}
    assert exp_map == act_parse

    sha3 = shasum_one(archive)
    assert sha3 == sha1

    for src in source_files:
        rel = str(src.relative_to(source_root))
        sst, dst = os.lstat(src), os.lstat(dest / rel)
        assert sst.st_size == dst.st_size
        assert (sst.st_mode & 0o7777) == (dst.st_mode & 0o7777) == 0o664
        assert sst.st_mtime_ns == dst.st_mtime_ns
        assert dst.st_uid == os.getuid()
        assert (sst.st_dev, sst.st_ino) != (dst.st_dev, dst.st_ino)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o775
    for d in source_dirs:
        rel = str(d.relative_to(source_root))
        assert (os.lstat(d).st_mode & 0o7777) == (os.lstat(dest / rel).st_mode & 0o7777) == 0o775

    r2_text = shasum_files(src_paths)
    compare_ledgers(r1_text, r2_text, src_paths)
    for p in source_files:
        assert source_signature(p) == src_sigs[str(p)]
    assert os.lstat(dest / ".stversions/secret.txt").st_size == len(
        b"invented hidden version\n")


def test_known_answer_and_malformed_digests(tmp_path, umask077):
    root = exclusive_mkdir(tmp_path / "k")
    f = write_bytes(root / "abc.txt", b"abc", 0o644)
    text = shasum_files([str(f)])
    with pytest.raises(ValueError, match="before dispatch"):
        shasum_files([])
    assert text.startswith("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    with pytest.raises(ValueError):
        compare_ledgers(text, text, [])
    with pytest.raises(ValueError):
        parse_ledger("Z" * 64 + "  " + str(f) + "\n", [str(f)])
    with pytest.raises(ValueError):
        parse_ledger(text + text, [str(f)])
    with pytest.raises(ValueError):
        parse_ledger(text, [str(f), str(root / "missing")])
    with pytest.raises(ValueError):
        parse_ledger(text + "0" * 64 + "  " + str(root / "extra") + "\n", [str(f)])
    with pytest.raises(AssertionError):
        compare_ledgers(text, "0" * 64 + text[64:], [str(f)])


def test_exclusive_creation_reservation_and_private_lists(tmp_path):
    old = os.umask(0o022)
    try:
        ctl = exclusive_mkdir(tmp_path / "ctl")
        assert (os.stat(ctl).st_mode & 0o777) == 0o700
        with pytest.raises(FileExistsError):
            exclusive_mkdir(ctl)
        listing = exclusive_file(ctl / "paths.nul", 0o600, b"a\0b\0")
        with pytest.raises(FileExistsError):
            exclusive_file(listing, 0o600)
        priv = exclusive_file(ctl / "private.nul", 0o600, b"secret\0")
        second = exclusive_file(ctl / "entries.nul", 0o600, b"public\0")
        assert (os.stat(priv).st_mode & 0o777) == 0o600
        assert (os.stat(second).st_mode & 0o777) == 0o600
        target = exclusive_file(ctl / "real", 0o600, b"orig")
        os.symlink(str(target), str(ctl / "link"))
        with pytest.raises(FileExistsError):
            exclusive_file(ctl / "link", 0o600)
    finally:
        os.umask(old)

    occupied = exclusive_file(ctl / "occupied.tar", 0o600, b"keep-bytes")
    before = occupied.read_bytes()
    dispatched = {"tar": False, "extract": False}

    def reserve(path):
        if os.path.lexists(str(path)):
            raise FileExistsError("occupied")
        dispatched["tar"] = True

    def guarded_extract(path, expected):
        if shasum_one(path) != expected:
            raise AssertionError("digest changed")
        dispatched["extract"] = True

    with pytest.raises(FileExistsError):
        reserve(occupied)
    assert occupied.read_bytes() == before
    assert dispatched["tar"] is False

    empty = exclusive_file(ctl / "empty.nul", 0o600, b"")
    with pytest.raises(ValueError):
        parse_ledger("", [str(empty)])
    expected = shasum_one(occupied)
    occupied.write_bytes(b"tampered archive")
    with pytest.raises(AssertionError):
        guarded_extract(occupied, expected)
    assert not dispatched["extract"]


def test_profile_dimensions_and_budget(tmp_path, umask077):
    # Test-local arithmetic examples only; no production binding or enforcement
    # exists until P1 implements and independently verifies them.
    root = exclusive_mkdir(tmp_path / "cal")
    files = [write_bytes(root / "a.txt", b"abcd", 0o664),
             write_bytes(root / "sub" / "longer_name.txt", b"abcdefgh", 0o664)]
    member_paths = set()
    total = max_file = full_max = comp_max = cum = 0
    for p in files:
        rel = str(type(root)(str(p)).relative_to(root))
        st = os.lstat(p)
        total += st.st_size
        max_file = max(max_file, st.st_size)
        full_max = max(full_max, len(str(p).encode()))
        cum += len(str(p).encode())
        for comp in rel.split("/"):
            comp_max = max(comp_max, len(comp.encode()))
        member_paths.add(rel)
        for i in range(1, len(rel.split("/"))):
            member_paths.add("/".join(rel.split("/")[:i]))
    member_count = len(member_paths) + 1
    profile = {"size_per_file": max_file, "file_count": len(files),
               "member_count": member_count, "component_bytes": comp_max,
               "full_bytes": full_max, "total_bytes": total,
               "cumulative_path_bytes": cum}
    assert member_count == 4
    assert profile["total_bytes"] == 12
    assert profile["full_bytes"] == max(len(str(p).encode()) for p in files)
    assert profile["cumulative_path_bytes"] == sum(len(str(p).encode()) for p in files)
    dimensions = sorted((p.stat().st_size, len(str(p).encode())) for p in files)
    assert dimensions == sorted([(4, len(str(files[0]).encode())),
                                 (8, len(str(files[1]).encode()))])
    calibrated = dict(profile)
    for key, value in (("file_count", 1), ("member_count", 3),
                       ("total_bytes", 11), ("size_per_file", 7),
                       ("component_bytes", 3), ("cumulative_path_bytes", cum - 1)):
        tighter = dict(calibrated)
        tighter[key] = value
        assert any(profile[k] > tighter[k] for k in profile)

    clock = {"t": 1000.0}

    def step_timeout(allowance, window_end):
        if clock["t"] >= window_end:
            return None
        return min(allowance, window_end - clock["t"])

    assert step_timeout(60, clock["t"] + 100) == 60
    assert step_timeout(60, clock["t"] + 10) == 10
    assert step_timeout(60, clock["t"]) is None

    def may_start(window_end, measured, floor=5):
        remaining = window_end - clock["t"]
        return remaining > 0 and remaining >= 2 * measured + floor

    assert may_start(clock["t"] + 25, 10)
    assert not may_start(clock["t"] + 24, 10)
    def scan():
        for _ in range(100):
            if clock["t"] >= 1002:
                raise TimeoutError("whole-step window expired")
            clock["t"] += 0.5
    with pytest.raises(TimeoutError):
        scan()


@pytest.mark.parametrize("bits", [0o4755, 0o2755, 0o1755])
def test_special_bits_refused_on_file_and_parent(tmp_path, bits):
    selected = tmp_path / "selected"
    selected.mkdir()
    payload = selected / "payload"
    payload.write_bytes(b"invented")
    payload.chmod(bits)
    assert stat.S_IMODE(payload.stat().st_mode) == bits
    with pytest.raises(ValueError, match="special permission"):
        admit_regular(payload)
    payload.chmod(0o600)
    selected.chmod(bits)
    try:
        with pytest.raises(ValueError, match="special permission"):
            admit_mode(selected)
    finally:
        selected.chmod(0o700)


def test_private_mode_roundtrip_and_occupied_tar(tmp_path, umask077):
    require_tools()
    source = exclusive_mkdir(tmp_path / "source")
    target = exclusive_mkdir(tmp_path / "target")
    payload = exclusive_file(source / "payload", 0o600, b"invented private-mode fixture")
    entries = exclusive_file(tmp_path / "entries.nul", 0o600, b".\0payload\0")
    archive = tmp_path / "transfer.tar"
    tar_create(archive, source, entries)
    original_digest = shasum_one(archive)
    with pytest.raises(FileExistsError):
        tar_create(archive, source, entries)
    assert shasum_one(archive) == original_digest
    tar_extract(archive, target)
    assert (target / "payload").read_bytes() == payload.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert stat.S_IMODE((target / "payload").stat().st_mode) == 0o600


def test_ctime_drift_and_link_admission(tmp_path, umask077):
    root = exclusive_mkdir(tmp_path / "r4")
    f = write_bytes(root / "file.txt", b"unchanged-content\n", 0o664)
    digest_before = shasum_one(f)
    sig_before = source_signature(f)
    mtime_ns = sig_before["mtime_ns"]
    os.chmod(f, 0o640)
    os.utime(f, ns=(mtime_ns, mtime_ns))
    os.chmod(f, 0o664)
    os.utime(f, ns=(mtime_ns, mtime_ns))
    sig_after = source_signature(f)
    assert shasum_one(f) == digest_before
    assert sig_after["ctime_ns"] != sig_before["ctime_ns"]
    hard = root / "hard.txt"
    os.link(f, hard)
    with pytest.raises(ValueError):
        admit_regular(f)
    os.symlink(str(f), str(root / "soft.txt"))
    with pytest.raises(ValueError):
        admit_regular(root / "soft.txt")


def _proc_state(pid):
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=5).stdout.decode().strip()
    except Exception:
        return ""
    return out


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_process_group_timeout_kill_and_reap(tmp_path, umask077):
    root = exclusive_mkdir(tmp_path / "r5")
    marker = root / "late.marker"
    pidfile = root / "child.pid"
    read_fd, write_fd = os.pipe()
    child_code = (
        "import os,sys,time\n"
        "pid=os.fork()\n"
        "if pid==0:\n"
        "    time.sleep(2)\n"
        "    open(sys.argv[1],'w').write('late')\n"
        "    os._exit(0)\n"
        "open(sys.argv[2],'w').write(str(pid))\n"
        "os.write(int(sys.argv[3]),b'x')\n"
        "os.waitpid(pid,0)\n"
    )
    cmd = [sys.executable, "-c", child_code, str(marker), str(pidfile), str(write_fd)]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, pass_fds=(write_fd,), env=tool_env())
    os.close(write_fd)
    child_pid = None
    try:
        assert select.select([read_fd], [], [], 5)[0], "child startup timed out"
        assert os.read(read_fd, 1) == b"x"
        child_pid = int(pidfile.read_text().strip())
        assert child_pid != proc.pid
        with pytest.raises(subprocess.TimeoutExpired):
            proc.communicate(timeout=0.2)
    finally:
        os.close(read_fd)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate(timeout=5)
    assert proc.returncode != 0
    assert child_pid is not None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        state = _proc_state(child_pid)
        if not state or state.startswith("Z"):
            break
        time.sleep(0.02)
    else:
        raise AssertionError("child still alive: %s" % _proc_state(child_pid))
    assert not marker.exists()


def test_actual_xargs_shasum_timeout_reaps_group(tmp_path, monkeypatch):
    require_tools()
    fifo = tmp_path / "blocked-invented-input"
    os.mkfifo(fifo, 0o600)
    real_popen = subprocess.Popen
    started = threading.Event()
    state = {}

    def observed_popen(argv, **kwargs):
        proc = real_popen(argv, **kwargs)
        if argv[0] == XARGS:
            state["pid"] = proc.pid
            state["proc"] = proc
            started.set()
        return proc

    monkeypatch.setattr(subprocess, "Popen", observed_popen)

    def phase():
        try:
            run_argv([XARGS, "-0", "-n", "128", "-s", "32768",
                      SHASUM, "-a", "256", "--"], timeout=1,
                     stdin_bytes=os.fsencode(fifo) + b"\0")
        except BaseException as exc:
            state["error"] = exc

    worker = threading.Thread(target=phase, daemon=True)
    worker.start()
    try:
        assert started.wait(2)
        pgid = state["pid"]
        descendants = set()
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline and not descendants:
            rows = subprocess.run(["ps", "-axo", "pid=,pgid=,stat=,comm="],
                                  capture_output=True, text=True, timeout=2,
                                  env=tool_env(), check=True).stdout.splitlines()
            for row in rows:
                fields = row.split(None, 3)
                if len(fields) == 4 and int(fields[1]) == pgid and int(fields[0]) != pgid:
                    descendants.add(int(fields[0]))
            if not descendants:
                time.sleep(0.01)
        assert descendants, "real xargs child was not observed"
        worker.join(6)
        assert not worker.is_alive()
        assert isinstance(state.get("error"), subprocess.TimeoutExpired)
        assert state["proc"].returncode is not None
        for pid in descendants:
            process_state = _proc_state(pid)
            assert not process_state or process_state.startswith("Z")
    finally:
        if "pid" in state:
            try:
                os.killpg(state["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        worker.join(6)


ENVELOPE_FILE_COUNT = 16384
ENVELOPE_CHILD_DIR_COUNT = 16383
ENVELOPE_MEMBER_COUNT = 32768
ENVELOPE_TOTAL_BYTES = 1024 * 1024 * 1024
ENVELOPE_FIRST_SIZE = 64 * 1024 * 1024
ENVELOPE_SECOND_SIZE = 20 * 1024 * 1024
ENVELOPE_MAX_FILE_SIZE = 64 * 1024 * 1024
ENVELOPE_LONG_DIR = "d" * 200
ENVELOPE_LONG_FILE = "f" * 200
ENVELOPE_FREE_MARGIN = 9 * 1024 * 1024 * 1024
ENVELOPE_WHOLE_LIMIT = 300.0
ENVELOPE_HASH_PHASE_LIMIT = 60.0
ENVELOPE_SHA_LIMIT = 30.0
ENVELOPE_BASE_NS = 1_620_000_000_000_000_000


def _ordered_envelope_sizes():
    rest_count = ENVELOPE_FILE_COUNT - 2
    remaining = (ENVELOPE_TOTAL_BYTES - ENVELOPE_FIRST_SIZE
                 - ENVELOPE_SECOND_SIZE)
    base = remaining // rest_count
    extra = remaining % rest_count
    assert base * rest_count + extra == remaining
    assert base >= 0
    sizes = [ENVELOPE_FIRST_SIZE, ENVELOPE_SECOND_SIZE]
    for i in range(rest_count):
        sizes.append(base + (1 if i < extra else 0))
    assert len(sizes) == ENVELOPE_FILE_COUNT
    assert sum(sizes) == ENVELOPE_TOTAL_BYTES
    assert max(sizes) == ENVELOPE_FIRST_SIZE
    assert max(sizes) <= ENVELOPE_MAX_FILE_SIZE
    return sizes


def _envelope_paths():
    files = []
    dirs = []
    root_file = "root_invented.bin"
    files.append(root_file)
    for i in range(1, ENVELOPE_FILE_COUNT):
        d = "%s_%05d" % (ENVELOPE_LONG_DIR, i)
        f = "%s_%05d" % (ENVELOPE_LONG_FILE, i)
        dirs.append(d)
        files.append(d + "/" + f)
    assert len(files) == ENVELOPE_FILE_COUNT
    assert len(dirs) == ENVELOPE_CHILD_DIR_COUNT
    assert len(files) + len(dirs) + 1 == ENVELOPE_MEMBER_COUNT
    return files, dirs


def _envelope_blocks():
    blocks = {}
    for size in _ordered_envelope_sizes():
        if size not in blocks:
            blocks[size] = make_binary(size, size % 251)
    return blocks


def _fresh_env_dir(base, name):
    p = base / name
    os.mkdir(str(p), 0o700)
    return p


def _require_envelope_space(tmp_path):
    st = os.statvfs(str(tmp_path))
    free = st.f_bavail * st.f_frsize
    if free < ENVELOPE_FREE_MARGIN:
        pytest.skip("insufficient free space on fixture volume: %d < %d" %
                    (free, ENVELOPE_FREE_MARGIN))


def _write_envelope_fixture(source_root, files_rel, sizes, blocks):
    ns = ENVELOPE_BASE_NS
    for i, (rel, size) in enumerate(zip(files_rel, sizes)):
        p = Path(str(source_root)) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(p.parent, 0o775)
        with open(p, "wb") as fh:
            fh.write(blocks[size])
        os.chmod(p, 0o664)
        os.utime(p, ns=(ns + i * 1_000_000_007, ns + i * 1_000_000_007))
    dirs = set()
    for rel in files_rel:
        parts = rel.split("/")
        for j in range(1, len(parts)):
            dirs.add("/".join(parts[:j]))
    for i, rel in enumerate(sorted(dirs)):
        p = Path(str(source_root)) / rel
        os.chmod(p, 0o775)
        os.utime(p, ns=(ns + 5_000_000 + i * 1_000_003,
                        ns + 5_000_000 + i * 1_000_003))
    os.chmod(source_root, 0o775)
    os.utime(source_root, ns=(ns + 1, ns + 1))
    return dirs


@pytest.mark.skipif(os.environ.get("K2B_RUN_UPPER_ENVELOPE") != "1",
                    reason="set K2B_RUN_UPPER_ENVELOPE=1 to run the full-envelope proof")
def test_full_envelope_standard_tools(tmp_path, umask077, request):
    require_tools()
    _require_envelope_space(tmp_path)

    timings = {}
    whole_start = time.monotonic()
    with tempfile.TemporaryDirectory(dir=str(tmp_path)) as tmpdir:
        try:
            run_root = Path(tmpdir)
            source_root = _fresh_env_dir(run_root, "source_root")
            ctl = _fresh_env_dir(run_root, "control")
            dest = _fresh_env_dir(run_root, "fresh_dest")

            files_rel, dirs_rel = _envelope_paths()
            sizes = _ordered_envelope_sizes()
            blocks = _envelope_blocks()
            _write_envelope_fixture(source_root, files_rel, sizes, blocks)

            entries = sorted(dirs_rel) + ["."] + list(files_rel)
            entries_nul = exclusive_file(ctl / "entries.nul", 0o600,
                                         b"".join(os.fsencode(e) + b"\0"
                                                  for e in entries))

            src_paths = [str(source_root / rel) for rel in files_rel]
            expected_files = set(files_rel)
            expected_dirs = set(dirs_rel) | {"."}

            phase_start = time.monotonic()
            r1_text = shasum_files(src_paths)
            timings["hash_phase_1"] = time.monotonic() - phase_start
            r1 = parse_ledger(r1_text, src_paths)
            assert len(r1) == ENVELOPE_FILE_COUNT
            src_sigs = {str(p): source_signature(p) for p in src_paths}
            for path, sig in src_sigs.items():
                assert sig["nlink"] == 1
                assert sig["mode"] == 0o664
                assert sig["size"] <= ENVELOPE_MAX_FILE_SIZE
            assert sum(sig["size"] for sig in src_sigs.values()) == ENVELOPE_TOTAL_BYTES

            archive = ctl / "envelope.tar"
            phase_start = time.monotonic()
            tar_create(archive, source_root, entries_nul)
            timings["tar_create"] = time.monotonic() - phase_start

            phase_start = time.monotonic()
            sha1 = shasum_one(archive)
            timings["archive_hash_pre_extract"] = time.monotonic() - phase_start

            with tarfile.open(archive, "r:") as tf:
                members = tf.getmembers()
            names = [normalize_member(m.name) for m in members]
            assert len(names) == ENVELOPE_MEMBER_COUNT
            assert len(names) == len(set(names))
            got_files, got_dirs = set(), set()
            for m in members:
                n = normalize_member(m.name)
                if m.isdir():
                    got_dirs.add(n)
                elif m.isreg():
                    got_files.add(n)
                else:
                    pytest.fail("unexpected member type: %s" % n)
            assert got_files == expected_files
            assert got_dirs == expected_dirs

            phase_start = time.monotonic()
            assert shasum_one(archive) == sha1
            timings["archive_hash_immediate_pre_extract"] = time.monotonic() - phase_start
            phase_start = time.monotonic()
            tar_extract(archive, dest)
            timings["tar_extract"] = time.monotonic() - phase_start

            phase_start = time.monotonic()
            sha2 = shasum_one(archive)
            timings["archive_hash_post_extract"] = time.monotonic() - phase_start
            assert sha1 == sha2

            out_files = []
            out_dirs = set()
            for dirpath, dirnames, filenames in os.walk(dest):
                rel_d = os.path.relpath(dirpath, dest)
                if rel_d != ".":
                    out_dirs.add(rel_d.replace(os.sep, "/"))
                for d in dirnames:
                    out_dirs.add(os.path.relpath(os.path.join(dirpath, d), dest)
                                 .replace(os.sep, "/"))
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    assert stat.S_ISREG(os.lstat(fp).st_mode)
                    out_files.append(fp)
            out_rel = {os.path.relpath(p, dest).replace(os.sep, "/") for p in out_files}
            assert out_rel == expected_files
            assert out_dirs == expected_dirs - {"."}

            phase_start = time.monotonic()
            out_text = shasum_files(out_files)
            timings["hash_phase_post_extract"] = time.monotonic() - phase_start
            absolute_output = parse_ledger(out_text, out_files)
            remapped = {os.path.relpath(p, dest).replace(os.sep, "/"):
                        absolute_output[os.fsencode(p)] for p in out_files}
            exp_map = {rel: r1[os.fsencode(str(source_root / rel))]
                       for rel in files_rel}
            assert remapped == exp_map

            for rel in files_rel:
                sst = os.lstat(source_root / rel)
                dst = os.lstat(dest / rel)
                assert sst.st_size == dst.st_size
                assert sst.st_size <= ENVELOPE_MAX_FILE_SIZE
                assert (sst.st_mode & 0o7777) == 0o664
                assert (dst.st_mode & 0o7777) == 0o664
                assert sst.st_mtime_ns == dst.st_mtime_ns
                assert dst.st_uid == os.getuid()
                assert (sst.st_dev, sst.st_ino) != (dst.st_dev, dst.st_ino)
            assert stat.S_IMODE(dest.stat().st_mode) == 0o775
            for rel in dirs_rel:
                assert (os.lstat(source_root / rel).st_mode & 0o7777) == 0o775
                assert (os.lstat(dest / rel).st_mode & 0o7777) == 0o775

            phase_start = time.monotonic()
            r2_text = shasum_files(src_paths)
            timings["hash_phase_2"] = time.monotonic() - phase_start
            compare_ledgers(r1_text, r2_text, src_paths)
            for p in src_paths:
                assert source_signature(p) == src_sigs[str(p)]
        finally:
            pass
    whole_elapsed = time.monotonic() - whole_start

    timings["whole_window"] = whole_elapsed
    request.node.user_properties = sorted(timings.items())

    assert timings["hash_phase_1"] < ENVELOPE_HASH_PHASE_LIMIT
    assert timings["hash_phase_post_extract"] < ENVELOPE_HASH_PHASE_LIMIT
    assert timings["hash_phase_2"] < ENVELOPE_HASH_PHASE_LIMIT
    assert timings["tar_create"] < ENVELOPE_SHA_LIMIT
    assert timings["tar_extract"] < ENVELOPE_SHA_LIMIT
    assert timings["archive_hash_pre_extract"] < ENVELOPE_SHA_LIMIT
    assert timings["archive_hash_post_extract"] < ENVELOPE_SHA_LIMIT
    assert timings["archive_hash_immediate_pre_extract"] < ENVELOPE_SHA_LIMIT
    assert whole_elapsed < ENVELOPE_WHOLE_LIMIT
