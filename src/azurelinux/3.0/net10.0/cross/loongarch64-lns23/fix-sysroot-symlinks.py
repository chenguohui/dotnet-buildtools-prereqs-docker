#!/usr/bin/env python3
"""
fix-sysroot-symlinks.py - Rewrite absolute symlinks in a sysroot to relative ones.

RPM payloads contain absolute symlinks (e.g. /usr/lib64/libfoo.so -> /usr/lib64/libfoo.so.1)
which would escape the sysroot when cross-compiling. Rewrite them relative to the
sysroot directory so they resolve correctly under any mount point.

Usage:
    python3 fix-sysroot-symlinks.py /crossrootfs/loongarch64
"""

import os
import sys
from pathlib import Path

rootfs = Path(sys.argv[1] if len(sys.argv) > 1 else "/crossrootfs/loongarch64")
fixed = 0
for link in rootfs.rglob("*"):
    if link.is_symlink():
        try:
            target = os.readlink(str(link))
            if target.startswith("/"):
                new_target = (rootfs / target.lstrip("/")).resolve()
                new_rel = os.path.relpath(str(new_target), str(link.parent))
                link.unlink()
                link.symlink_to(new_rel)
                fixed += 1
        except (OSError, ValueError):
            pass
print(f"  Fixed {fixed} absolute symlinks")
