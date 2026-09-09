#!/usr/bin/env python3
"""
install-rpms.py - Download loongarch64 RPMs from a yum repo and extract to sysroot.

Pure Python implementation — no rpm2cpio or qemu needed.
Runs on x64, downloads loongarch64 RPMs, and extracts them.

Usage:
    python3 install-rpms.py \
        --repo https://pkg.loongnix.cn/loongnix-server/23/os/loongarch64 \
        --rootfsdir /crossrootfs/loongarch64 \
        --packages glibc glibc-devel kernel-headers ...
"""

import argparse
import gzip
import lzma
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen
from xml.etree import ElementTree as ET

NS_COMMON = "http://linux.duke.edu/metadata/common"
NS_REPO = "http://linux.duke.edu/metadata/repo"


# ─── RPM repository metadata ────────────────────────────────────────────────

def _urlopen_retry(url, timeout=60, max_retries=5):
    """urlopen with exponential backoff retry."""
    import time
    for attempt in range(max_retries):
        try:
            return urlopen(url, timeout=timeout)
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                print(f"    Connection error: {e}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def fetch_repomd(repo_base):
    """Download repomd.xml and return the primary.xml.gz URL."""
    repomd_url = urljoin(repo_base.rstrip("/") + "/", "repodata/repomd.xml")
    print(f"  Fetching: {repomd_url}")

    with _urlopen_retry(repomd_url, timeout=60) as resp:
        root = ET.fromstring(resp.read())

    for data in root:
        if data.tag == f"{{{NS_REPO}}}data" and data.get("type") == "primary":
            for loc in data:
                if loc.tag == f"{{{NS_REPO}}}location":
                    return loc.get("href")
    return None


def load_package_index(repo_base, primary_rel_url):
    """Download primary.xml.gz and return {pkg_name: (version, href)}."""
    primary_url = urljoin(repo_base.rstrip("/") + "/", primary_rel_url)
    print(f"  Fetching: {primary_url}")

    with _urlopen_retry(primary_url, timeout=180) as resp:
        data = gzip.decompress(resp.read())

    print(f"  Parsing {len(data) / 1024 / 1024:.0f} MB metadata...")
    root = ET.fromstring(data)

    packages = {}
    for pkg in root:
        if pkg.tag != f"{{{NS_COMMON}}}package" or pkg.get("type") != "rpm":
            continue

        name_el = pkg.find(f"{{{NS_COMMON}}}name")
        ver_el = pkg.find(f"{{{NS_COMMON}}}version")
        loc_el = pkg.find(f"{{{NS_COMMON}}}location")

        if name_el is None or loc_el is None:
            continue

        name = name_el.text
        ver = ver_el.get("ver") if ver_el is not None else "0"
        href = loc_el.get("href")

        # Keep latest version
        if name not in packages or _ver_gt(ver, packages[name][0]):
            packages[name] = (ver, href)

    print(f"  Indexed {len(packages)} packages")
    return packages


def _ver_gt(v1, v2):
    """Return True if v1 > v2, simple version-string comparison."""
    return v1 > v2  # Good enough for our purposes


# ─── RPM file extraction ────────────────────────────────────────────────────

def extract_rpm(rpm_path, dest_dir, tmp_dir):
    """Extract an RPM file to dest_dir using 7z + zstd + cpio.

    RPM payloads vary by distro:
      lns23   → zstd-compressed CPIO (7z yields .cpio.zstd)
      openEuler → xz-compressed CPIO (7z yields plain .cpio)
    Three code paths:
      A) 7z extracts to a *.cpio file → cpio -idm (decompress xz/zstd first if needed)
      B) 7z recursively extracts everything → copy files directly
    """
    os.makedirs(dest_dir, exist_ok=True)

    rpm_name = os.path.basename(rpm_path).replace(".rpm", "")
    work_dir = os.path.join(tmp_dir, f"ext-{rpm_name}")
    os.makedirs(work_dir, exist_ok=True)

    try:
        # Step 0: 优先用 libarchive 的 bsdtar —— 一步解出 RPM 文件树
        # （libarchive 3.7 原生支持 zstd/xz/gzip payload，无需中间 cpio 步骤；
        #   对 lns23(zstd) 和 openEuler(xz) 的 RPM 均已实测可用）
        if shutil.which("bsdtar"):
            try:
                subprocess.run(
                    ["bsdtar", "-xf", rpm_path, "-C", dest_dir],
                    capture_output=True,
                    check=True,
                )
                return True
            except subprocess.CalledProcessError:
                pass  # 回退到 7z 路径

        # Step 1: 7z extracts the RPM
        subprocess.run(
            ["7z", "x", "-o" + work_dir, rpm_path, "-y"],
            capture_output=True,
            check=True,
        )

        # Check what 7z produced
        cpio_file = None
        has_extracted_content = False
        for f in os.listdir(work_dir):
            if f.endswith(".cpio") or f.endswith(".cpio.zstd") or f.endswith(".cpio.zst") or f.endswith(".cpio.xz"):
                cpio_file = os.path.join(work_dir, f)
            if f in ("usr", "lib64", "lib", "etc", "bin", "sbin", "opt"):
                has_extracted_content = True

        if cpio_file:
            # Path A: decompress payload if needed → cpio → extract
            # Detect compression by magic (7z output naming is inconsistent across distros)
            with open(cpio_file, "rb") as fh:
                magic = fh.read(6)
            if magic[:3] == b"\xfd7zXZ":  # xz
                decompressed = cpio_file + ".raw"
                with lzma.open(cpio_file, "rb") as src, open(decompressed, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.unlink(cpio_file)
                cpio_file = decompressed
            elif magic[:4] == b"\x28\xb5\x2f\xfd":  # zstd
                decompressed = cpio_file + ".raw"
                subprocess.run(
                    ["zstd", "-d", cpio_file, "-o", decompressed, "--rm"],
                    capture_output=True,
                    check=True,
                )
                cpio_file = decompressed
            with open(cpio_file, "rb") as f:
                subprocess.run(
                    ["cpio", "-idm", "--quiet"],
                    stdin=f,
                    cwd=dest_dir,
                    capture_output=True,
                )
            os.unlink(cpio_file)
        elif has_extracted_content:
            # Path B: 7z already extracted everything, move files
            for item in os.listdir(work_dir):
                src = os.path.join(work_dir, item)
                dst = os.path.join(dest_dir, item)
                if os.path.isdir(src):
                    if not os.path.exists(dst):
                        shutil.move(src, dst)
                    else:
                        _merge_dirs(src, dst)
                else:
                    if not os.path.exists(dst):
                        shutil.move(src, dst)
        else:
            print(f"    WARNING: unrecognized 7z output for {rpm_name}")
            print(f"    Contents: {os.listdir(work_dir)}")
            return False

        # Cleanup
        shutil.rmtree(work_dir, ignore_errors=True)
        return True

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if e.stderr else str(e)
        print(f"    ERROR extracting {rpm_name}: {stderr[:100]}")
        return False
    except Exception as e:
        print(f"    ERROR: {e}")
        return False


def _merge_dirs(src, dst):
    """Merge src directory into dst directory."""
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            os.makedirs(d, exist_ok=True)
            _merge_dirs(s, d)
        elif not os.path.exists(d):
            shutil.move(s, d)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download loongarch64 RPMs and extract to sysroot"
    )
    parser.add_argument("--repo", action="append", required=True, help="RPM repository base URL (may be given multiple times; earlier repos take precedence)")
    parser.add_argument("--rootfsdir", required=True, help="Target sysroot directory")
    parser.add_argument("--packages", nargs="+", required=True, help="Package names to install")
    parser.add_argument("--tmpdir", default=None, help="Temporary directory for RPM downloads")
    args = parser.parse_args()

    rootfs_dir = Path(args.rootfsdir)
    rootfs_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = args.tmpdir or tempfile.mkdtemp(prefix="rpms-")
    rpm_dir = os.path.join(tmp_dir, "rpms")
    os.makedirs(rpm_dir, exist_ok=True)

    print(f"Repositories: {', '.join(args.repo)}")
    print(f"Sysroot:    {rootfs_dir}")
    print(f"Requested:  {len(args.packages)} packages")
    print()

    # 1. Load package indexes from all repos. Earlier repos take precedence,
    #    later repos only fill in packages missing from earlier ones.
    print("[1/3] Loading package indexes...")
    all_packages = {}
    pkg_repo = {}
    for repo_base in args.repo:
        repo_base = repo_base.rstrip("/")
        print(f"  Repo: {repo_base}")
        primary_rel = fetch_repomd(repo_base)
        if not primary_rel:
            print("ERROR: could not find primary metadata", file=sys.stderr)
            sys.exit(1)

        index = load_package_index(repo_base, primary_rel)
        added = 0
        for name, entry in index.items():
            if name not in all_packages:
                all_packages[name] = entry
                pkg_repo[name] = repo_base
                added += 1
        print(f"  Indexed {len(index)} packages ({added} new)")
    print()

    # 2. Match requested packages
    print()
    print("[2/3] Matching packages...")
    found = []
    missing = []

    for req in args.packages:
        if not req.strip():
            continue  # blank lines from a package-list file must not fuzzy-match everything
        if req in all_packages:
            ver, href = all_packages[req]
            found.append((req, ver, href, pkg_repo[req]))
        else:
            # Fuzzy match
            matches = [n for n in all_packages if n.startswith(req)]
            if len(matches) == 1:
                ver, href = all_packages[matches[0]]
                found.append((matches[0], ver, href, pkg_repo[matches[0]]))
                print(f"  {req} -> {matches[0]}-{ver}")
            elif len(matches) > 1:
                # Pick exact match first, otherwise first
                exact = [m for m in matches if m == req]
                pick = exact[0] if exact else matches[0]
                ver, href = all_packages[pick]
                found.append((pick, ver, href, pkg_repo[pick]))
                print(f"  {req} -> {pick}-{ver}")
            else:
                missing.append(req)

    if missing:
        print(f"\n  ERROR: {len(missing)} requested package(s) not found in any repo: {', '.join(missing)}", file=sys.stderr)
        print("  Add them to the package list or pass additional --repo arguments.", file=sys.stderr)
        sys.exit(1)

    # 3. Download RPMs
    print()
    print(f"[3/3] Downloading {len(found)} RPMs...")

    def _download(url, path, max_attempts=5):
        """Download with exponential backoff (some mirrors intermittently drop connections)."""
        for attempt in range(max_attempts):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
                with urlopen(req, timeout=180) as resp, open(path, "wb") as f:
                    shutil.copyfileobj(resp, f)
                return True
            except Exception as e:
                if attempt < max_attempts - 1:
                    wait = min(5 * (2 ** attempt), 60)
                    print(f"    attempt {attempt+1}/{max_attempts} failed ({e}); retry in {wait}s")
                    time.sleep(wait)
        return False

    failed_downloads = []
    for i, (name, ver, href, repo) in enumerate(found):
        rpm_url = urljoin(repo + "/", href)
        rpm_path = os.path.join(rpm_dir, os.path.basename(href))

        if os.path.exists(rpm_path):
            print(f"  [{i+1}/{len(found)}] {name} (cached)")
            continue
        print(f"  [{i+1}/{len(found)}] {name}-{ver}")
        if _download(rpm_url, rpm_path):
            time.sleep(2)
        else:
            failed_downloads.append((name, rpm_url, rpm_path))

    # Slow second pass for packages the mirror dropped mid-transfer
    if failed_downloads:
        print(f"\n  {len(failed_downloads)} downloads failed; slow retry pass...")
        time.sleep(30)
        still_failed = []
        for name, rpm_url, rpm_path in failed_downloads:
            print(f"  retrying {name}")
            if _download(rpm_url, rpm_path):
                time.sleep(3)
            else:
                still_failed.append(name)
        if still_failed:
            print(f"  ERROR: {len(still_failed)} packages could not be downloaded: {', '.join(still_failed)}", file=sys.stderr)
            sys.exit(1)

    # 4. Extract RPMs
    print()
    print(f"Extracting to {rootfs_dir}...")

    extracted = 0
    failed = []
    for name, ver, href, repo in found:
        rpm_path = os.path.join(rpm_dir, os.path.basename(href))
        if not os.path.exists(rpm_path):
            continue
        if extract_rpm(rpm_path, str(rootfs_dir), tmp_dir):
            extracted += 1
        else:
            failed.append(name)

    print(f"  Extracted: {extracted}/{len(found)}")
    if failed:
        print(f"  ERROR: {len(failed)} packages could not be extracted: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)

    # 5. Summary
    print()
    print("═══ Sysroot Summary ═══")

    # Find and show libc version (pure Python — `strings` may not exist in minimal containers)
    for libc_path in [
        rootfs_dir / "lib64" / "libc.so.6",
        rootfs_dir / "usr" / "lib64" / "libc.so.6",
        rootfs_dir / "usr" / "lib" / "loongarch64-linux-gnu" / "libc.so.6",
    ]:
        if libc_path.exists():
            data = libc_path.read_bytes()
            match = re.search(rb"GNU C Library[^\x00]*", data)
            if match:
                print(f"  glibc: {match.group(0).decode(errors='replace')}")
            else:
                print("  glibc: (version string not found)")
            break

    total_files = sum(1 for _ in rootfs_dir.rglob("*") if _.is_file())
    total_size = sum(_.stat().st_size for _ in rootfs_dir.rglob("*") if _.is_file())
    print(f"  Files: {total_files} ({total_size / 1024 / 1024:.0f} MB)")
    print(f"  Done:  {rootfs_dir}")

    # Cleanup
    # Note: do NOT re-import shutil here - the nested _download() function
    # references the module-level shutil and a local import would shadow it,
    # breaking all downloads ("cannot access free variable 'shutil'").
    if not args.tmpdir:
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    main()
