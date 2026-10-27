#!/usr/bin/env python3
"""Explicit one-time installation only. This installer is never pre-authorized."""
import argparse
import datetime
import pwd
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import secrets
import fcntl


def require(condition,message):
    if not condition: raise SystemExit(message)


def ensure_dir(path,home):
    for p in (path,*path.parents):
        require(not p.is_symlink(),'Symlink installation directory')
        if p==home:break
    path.mkdir(parents=True,exist_ok=True)


def sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def atomic_write(path,data,mode):
    require(not path.is_symlink(),'Symlink destination')
    fd,name=tempfile.mkstemp(prefix=path.name+'.install-',dir=path.parent)
    tmp=Path(name)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fchmod(output.fileno(),mode);os.fsync(output.fileno())
        os.replace(tmp,path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def validate(source,role,expected,user,data=None):
    digest=hashlib.sha256(source.read_bytes() if data is None else data).hexdigest()
    require(re.fullmatch('[a-f0-9]{64}',expected) and expected==digest,'Reviewed worker hash mismatch')
    require(user==('keithmbpm2' if role=='home' else 'fastshower'),'Wrong host user')
    return digest


def install(role, expected):
    home=Path(pwd.getpwuid(os.getuid()).pw_dir);state=home/'.local/state/k2b'
    ensure_dir(state,home);lock=state/'reel-install.lock'
    require(not lock.is_symlink() and (not lock.exists() or lock.is_file()),'Unsafe install lock')
    with lock.open('a') as held:
        try:fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Another Reel installation is active; wait for its result')
        _install(role,expected)


def _install(role, expected):
    account=pwd.getpwuid(os.getuid());home=Path(account.pw_dir)
    source=Path(__file__).with_name('reel-worker.py')
    data=source.read_bytes();digest=validate(source,role,expected,account.pw_name,data)
    require(Path('/opt/homebrew/bin/python3').is_file(),'Expected Home/Mini Python missing: /opt/homebrew/bin/python3; verify the intended host runtime before retrying')
    require(home.stat().st_uid==os.getuid(),'Home directory ownership mismatch')
    root=home/'.local/libexec'; ensure_dir(root,home)
    release=root/'k2b-reel'/digest
    for path in (root,release,*release.parents):
        require(not path.is_symlink(),'Symlink install path')
        if path==home: break
    ensure_dir(release.parent,home)
    worker=release/'worker.py'; cfg=release/'config.json'
    require(not worker.is_symlink() and not cfg.is_symlink(),'Symlink release file')
    config={'role':role,'worker_sha256':digest}
    if release.exists():
        require(worker.is_file(),'Committed release incomplete; preserve it and inspect before reinstalling: '+str(release))
        require(hashlib.sha256(worker.read_bytes()).hexdigest()==digest,'Installed release changed')
        require(cfg.is_file() and not cfg.is_symlink(),'Partial release config missing or unsafe; preserve release and inspect before reinstalling')
        require(json.loads(cfg.read_text())==config,'Installed config changed')
    else:
        staged=Path(tempfile.mkdtemp(prefix='staging-'+digest+'-',dir=release.parent))
        atomic_write(staged/'worker.py',data,0o444)
        atomic_write(staged/'config.json',(json.dumps(config)+'\n').encode(),0o444)
        sync_directory(staged)
        require(not release.exists(),'Concurrent release publication; preserve staging: '+str(staged))
        staged.rename(release);sync_directory(release.parent)
    release.chmod(0o555);sync_directory(release)
    launcher=root/'k2b-reel-v1'
    require(not launcher.is_symlink(),'Symlink launcher')
    previous=launcher.read_bytes() if launcher.exists() else None
    text='#!/bin/sh\nexec /opt/homebrew/bin/python3 -I '+shlex.quote(str(worker))+' "$@"\n'
    backups=home/'.local/state/k2b/reel-install-backups';ensure_dir(backups,home)
    rules=home/'.codex/rules';ensure_dir(rules,home)
    rule=rules/'k2b-reel-v1.rules'
    require(not rule.is_symlink(),'Symlink rule')
    for p in (rules,*rules.parents):
        require(not p.is_symlink(),'Symlink rule directory')
        if p==home:break
    # Variable URLs/run IDs follow this action prefix. The reviewed worker's strict
    # argparse grammar and value validators own that boundary, never the rule parser.
    commands=['version','status','inventory']+(['capture'] if role=='mini' else ['process','save-digest'])
    body=''.join('prefix_rule(pattern='+json.dumps([str(launcher),x])+', decision="allow")\n' for x in commands)
    previous_rule=rule.read_bytes() if rule.exists() else None
    for p in (backups,*backups.parents):
        require(not p.is_symlink(),'Symlink backup directory')
        if p==home:break
    stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(6)
    if previous is not None and previous!=text.encode():atomic_write(backups/('launcher-'+stamp),previous,0o600)
    if previous_rule is not None and previous_rule!=body.encode():atomic_write(backups/('rules-'+stamp),previous_rule,0o600)
    try:
        if previous!=text.encode():atomic_write(launcher,text.encode(),0o555)
        output=subprocess.check_output([str(launcher),'version'],text=True,timeout=10)
        require(json.loads(output)['worker_sha256']==digest,'Version check mismatch')
        atomic_write(rule,body.encode(),0o600)
    except BaseException:
        if previous is not None:atomic_write(launcher,previous,0o555)
        else:launcher.unlink(missing_ok=True)
        if previous_rule is not None:atomic_write(rule,previous_rule,0o600)
        else:rule.unlink(missing_ok=True)
        raise
    print(json.dumps({'status':'installed','role':role,'launcher':str(launcher),'worker_sha256':digest,'rules':str(rule),'version_result':json.loads(output)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role',required=True,choices=['home','mini'])
    p.add_argument('--expected-sha256',required=True)
    a=p.parse_args();os.umask(0o077)
    try:install(a.role,a.expected_sha256)
    except (Exception,SystemExit) as error:
        raise SystemExit('Installation stopped safely ('+type(error).__name__+'): '+str(error)+'. Preserve release/staging and backups, inspect the named path, then retry the same reviewed installer.') from None
