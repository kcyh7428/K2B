#!/usr/bin/env python3
"""Explicit reviewed media-package installation, never a routine worker action."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import stat
import subprocess
import sys

FILES = ('video-extract.py','lib/video_extract.py','lib/video_legacy.py','media/record.swift')
DOWNLOADER_SHA256 = 'e51e69e89489f199530f13d32a4342da3ddb2cf34842f364603a066db05afbf3'
OWNERS = {'home':'keithmbpm2','sjm':'keithcheung','mini':'fastshower'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def validate_owner(role,user):
    if OWNERS.get(role) != user: raise ValueError('installation role does not match owning user')
    return role


def package_digest(files):
    return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def release_identity(files,role,pins):
    return package_digest({'files':files,'role':role,'host_pins':pins})


def host_pins(role,home,downloader):
    if role != 'mini': return {}
    if downloader is None or downloader.is_symlink() or digest(downloader) != DOWNLOADER_SHA256:
        raise ValueError('official pinned 2026.10.26 downloader required')
    pins = {'downloader_sha256':DOWNLOADER_SHA256,'deno_sha256':digest(Path('/opt/homebrew/bin/deno').resolve()),
            'swift_version_sha256':hashlib.sha256(subprocess.check_output(['/usr/bin/swiftc','--version'],timeout=20)).hexdigest()}
    profile_receipt = home/'.local/state/k2b/video-v2/browser-profile.json'; no_symlinks(profile_receipt)
    if profile_receipt.is_file():
        import re
        profile = json.loads(profile_receipt.read_text())
        if profile.get('control') != 'native_chrome' or not isinstance(profile.get('basename'),str) or not re.fullmatch(r'Default|Profile [0-9]{1,3}',profile['basename']):
            raise ValueError('selected native Chrome profile receipt invalid')
        pins['browser_profile'] = profile['basename']
    return pins


def validate_acceptance(receipt, package, role):
    if receipt.get('package') != package or receipt.get('role') != role or receipt.get('live_examples') != list(range(1,9)) or receipt.get('independent_review') != 'approve':
        raise ValueError('activation requires matching independent approval and all live examples; fixtures are insufficient')


def no_symlinks(path):
    for p in (path,*path.parents):
        if p.is_symlink(): raise ValueError('symlink installation path')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('role',choices=OWNERS)
    p.add_argument('--downloader',type=Path)
    p.add_argument('--activate',action='store_true')
    a = p.parse_args(); os.umask(0o077)
    user = pwd.getpwuid(os.getuid()).pw_name; validate_owner(a.role,user)
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    source = Path(__file__).resolve().parent
    hashes = {name:digest(source/name) for name in FILES}
    pins = host_pins(a.role,home,a.downloader)
    identity = release_identity(hashes,a.role,pins)
    releases = home/'.local/libexec/k2b-video'; no_symlinks(releases)
    releases.mkdir(parents=True,exist_ok=True,mode=0o700)
    release = releases/identity; no_symlinks(release)
    if release.exists():
        c = json.loads((release/'config.json').read_text())
        if c['files'] != hashes or c['role'] != a.role or c.get('host_pins') != pins: raise ValueError('existing release configuration mismatch')
        for name, expected in hashes.items():
            no_symlinks(release/name)
            if digest(release/name) != expected: raise ValueError('existing release changed')
    else:
        stage = releases/(identity+'.partial-'+secrets.token_hex(8)); stage.mkdir(mode=0o700)
        for name in FILES:
            target = stage/name; target.parent.mkdir(exist_ok=True,parents=True,mode=0o700)
            shutil.copyfile(source/name,target); target.chmod(0o400)
            if digest(target) != hashes[name]: raise ValueError('installed source hash mismatch')
        c = {'version':'2','role':a.role,'package':identity,'files':hashes,'host_pins':pins,'recorder_accepted':False}
        if a.role == 'mini':
            if a.downloader is None or a.downloader.is_symlink() or digest(a.downloader) != DOWNLOADER_SHA256:
                raise ValueError('official pinned 2026.10.26 downloader required')
            tools = stage/'tools'; tools.mkdir(mode=0o700)
            shutil.copyfile(a.downloader,tools/'yt-dlp'); (tools/'yt-dlp').chmod(0o500)
            c['downloader_sha256'] = DOWNLOADER_SHA256
            subprocess.run(['/usr/bin/swiftc','-parse-as-library',str(stage/'media/record.swift'),'-o',str(tools/'record')],check=True,timeout=120)
            (tools/'record').chmod(0o500); c['recorder_sha256'] = digest(tools/'record')
            c.update(pins)
        (stage/'config.json').write_text(json.dumps(c,sort_keys=True,indent=2)+'\n'); (stage/'config.json').chmod(0o400)
        for folder in sorted((d for d in stage.rglob('*') if d.is_dir()),key=lambda d:len(d.parts),reverse=True): folder.chmod(0o500)
        # Some supported macOS filesystems require owner-write on the source
        # directory for rename. Freeze its root only after atomic publication.
        stage.rename(release); release.chmod(0o500)
    launcher = home/'.local/libexec/k2b-video-v2'; no_symlinks(launcher)
    if a.activate:
        receipt = home/'.local/state/k2b/video-v2/acceptance.json'; no_symlinks(receipt)
        validate_acceptance(json.loads(receipt.read_text()),identity,a.role)
        # Preserve any old launcher. No deletion or overwrite of an old release.
        if launcher.exists():
            old = launcher.with_name(launcher.name+'.previous-'+secrets.token_hex(4)); shutil.copyfile(launcher,old); old.chmod(0o500)
        temporary = launcher.with_name(launcher.name+'.partial-'+secrets.token_hex(4))
        import shlex
        temporary.write_text('#!/bin/sh\nexec /opt/homebrew/bin/python3 -I '+shlex.quote(str(release/'video-extract.py'))+' "$@"\n')
        temporary.chmod(0o500); os.replace(temporary,launcher)
    print(json.dumps({'status':'activated' if a.activate else 'candidate_installed','role':a.role,'package':identity,'path':str(release),'recorder_accepted':c['recorder_accepted']}))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        # Installer diagnostics identify types, never credential or subprocess output.
        print(json.dumps({'status':'blocked','reason':str(error) if isinstance(error,ValueError) else type(error).__name__})); sys.exit(2)
