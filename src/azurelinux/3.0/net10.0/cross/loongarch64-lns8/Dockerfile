# =============================================================================
# Image: dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-cross-loongarch64-lns8
#
# Cross-compilation image for building .NET 10 targeting LoongArch64 on
# Loongnix Server 8.4 (lns8, "old world" ABI, glibc 2.28).
#
# Same design as cross/loongarch64-lns23, but the sysroot is built from the
# Loongnix Server 8.4 RPM repositories. lns8 is an old-world ABI system, so:
#   * the host toolchain must be the Loongson LLVM 20.1.8 fork (built in
#     crossdeps-builder-amd64-lns8; upstream LLVM 20.1.8 is new-world only), and
#   * target code is built with -mcmodel=large per the Loongson build docs.
# Binaries produced with this image only require GLIBC symbols <= 2.28 and
# therefore run on lns8 and other old-world LoongArch distros.
#
# Sysroot sources (both supported; the prebuilt tarball takes precedence):
#   1. Prebuilt tarball (recommended, validated): drop a sysroot exported from
#      a real/emulated lns8 system at sysroot/lns8-sysroot.tar.gz.
#      Dependencies are resolved by yum and %post scripts have run, so this is
#      the most faithful sysroot. See README.md for the QEMU export procedure.
#   2. Direct RPM download (default when no tarball is present): downloads the
#      loongarch64 RPMs listed in lns8-packages.txt from the Loongnix
#      repository and extracts them with install-rpms.py. Runs on plain x64
#      with no QEMU/hardware, suitable for CI. There is no dependency
#      resolution - the package list is maintained manually and should be kept
#      in sync with the native build environment.
# =============================================================================

ARG ROOTFS_DIR=/crossrootfs/loongarch64

# ── Stage 1: build the lns8 sysroot ──────────────────────────────────────────
FROM mcr.microsoft.com/azurelinux/base/core:3.0 AS sysroot-builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG ROOTFS_DIR
# Override if pkg.loongnix.cn is unreachable from the build environment.
# BaseOS = core repo, AppStream = extra packages (lttng-ust/systemtap-sdt/... live there)
ARG LNS_REPO_BASE=https://pkg.loongnix.cn/loongnix-server/8.4/BaseOS/loongarch64/release
ARG LNS_REPO_APPSTREAM=https://pkg.loongnix.cn/loongnix-server/8.4/AppStream/loongarch64/release

# python3 (RPM metadata parsing/download), libarchive's bsdtar (single-step RPM
# extraction with native zstd/xz support), p7zip/zstd/cpio (install-rpms.py
# fallback path; azurelinux's p7zip only ships 7za, provide a 7z alias)
RUN tdnf update -y && \
    tdnf install -y python3 libarchive p7zip zstd cpio tar ca-certificates coreutils && \
    ln -s /usr/bin/7za /usr/local/bin/7z

WORKDIR /build
COPY install-rpms.py fix-sysroot-symlinks.py lns8-packages.txt scripts/
COPY sysroot/ /build/sysroot/

RUN set -euo pipefail && \
    mkdir -p "$ROOTFS_DIR" && \
    if [[ -f /build/sysroot/lns8-sysroot.tar.gz ]]; then \
        echo "==> Using prebuilt sysroot tarball (exported from lns8)" && \
        tar -xzf /build/sysroot/lns8-sysroot.tar.gz -C "$ROOTFS_DIR" && \
        echo "==> Fixing absolute symlinks" && \
        python3 -u scripts/fix-sysroot-symlinks.py "$ROOTFS_DIR"; \
    else \
        echo "==> Building sysroot from $LNS_REPO_BASE + $LNS_REPO_APPSTREAM" && \
        python3 -u scripts/install-rpms.py \
            --repo "$LNS_REPO_BASE" \
            --repo "$LNS_REPO_APPSTREAM" \
            --rootfsdir "$ROOTFS_DIR" \
            --packages $(grep -vE '^\s*(#|$)' scripts/lns8-packages.txt) && \
        echo "==> Fixing absolute symlinks" && \
        python3 -u scripts/fix-sysroot-symlinks.py "$ROOTFS_DIR"; \
    fi && \
    echo "==> usr-merge layout symlinks (normally provided by the filesystem RPM, whose payload our extractor cannot parse)" && \
    for entry in "bin:usr/bin" "sbin:usr/sbin" "lib:usr/lib" "lib64:usr/lib64"; do \
        link="${entry%%:*}"; \
        if [[ ! -e "$ROOTFS_DIR/$link" && ! -L "$ROOTFS_DIR/$link" ]]; then \
            ln -s "${entry#*:}" "$ROOTFS_DIR/$link" && \
            echo "  /$link -> ${entry#*:}"; \
        fi; \
    done && \
    echo "==> GCC triple compatibility (dotnet build system hardcodes loongarch64-linux-gnu)" && \
    ACTUAL_TRIPLE=$(ls "$ROOTFS_DIR/usr/lib/gcc/" | head -1) && \
    DEBIAN_TRIPLE=loongarch64-linux-gnu && \
    echo "  Actual triple: $ACTUAL_TRIPLE" && \
    if [[ "$ACTUAL_TRIPLE" != "$DEBIAN_TRIPLE" ]]; then \
        ln -s "$ACTUAL_TRIPLE" "$ROOTFS_DIR/usr/lib/gcc/$DEBIAN_TRIPLE" && \
        echo "  $ROOTFS_DIR/usr/lib/gcc/$DEBIAN_TRIPLE -> $ACTUAL_TRIPLE"; \
        CXX_VER=$(ls "$ROOTFS_DIR/usr/include/c++/" 2>/dev/null | grep -E '^[0-9]+' | head -1); \
        if [[ -n "$CXX_VER" && ! -e "$ROOTFS_DIR/usr/include/c++/$CXX_VER/$DEBIAN_TRIPLE" ]]; then \
            ln -s "$ACTUAL_TRIPLE" "$ROOTFS_DIR/usr/include/c++/$CXX_VER/$DEBIAN_TRIPLE" && \
            echo "  $ROOTFS_DIR/usr/include/c++/$CXX_VER/$DEBIAN_TRIPLE -> $ACTUAL_TRIPLE"; \
        fi; \
        for subdir in lib usr/lib; do \
            parent="$ROOTFS_DIR/$subdir"; \
            phys_parent=$(realpath -m "$parent"); \
            if [[ ! -e "$phys_parent/$DEBIAN_TRIPLE" && ! -L "$phys_parent/$DEBIAN_TRIPLE" ]]; then \
                mkdir -p "$phys_parent" && \
                rel=$(realpath -m --relative-to="$phys_parent" "$ROOTFS_DIR/usr/lib64") && \
                ln -s "$rel" "$phys_parent/$DEBIAN_TRIPLE" && \
                echo "  $phys_parent/$DEBIAN_TRIPLE -> $rel"; \
            fi; \
        done; \
    else \
        echo "  Target already uses the Debian triple convention, skipping."; \
    fi

# ── Stage 2: cross-build compiler-rt builtins + static libc++ against the sysroot ──
FROM mcr.microsoft.com/dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-crossdeps-builder-amd64-lns8 AS runtimes-builder

ARG ROOTFS_DIR

COPY --from=sysroot-builder $ROOTFS_DIR $ROOTFS_DIR

RUN TARGET_TRIPLE="loongarch64-linux-gnu" && \
    GCC_VER=$(basename "$(find "$ROOTFS_DIR/usr/include/c++/" -mindepth 1 -maxdepth 1 -type d | sort -V | head -n1)") && \
    CPP_INCLUDES="$ROOTFS_DIR/usr/include/c++/$GCC_VER" && \
    TRIPLET_INCLUDES=$([ -e "$CPP_INCLUDES/$TARGET_TRIPLE" ] && echo "$CPP_INCLUDES/$TARGET_TRIPLE" || echo "$(realpath "$CPP_INCLUDES/../../$TARGET_TRIPLE");$(realpath "$CPP_INCLUDES/../../$TARGET_TRIPLE/c++/$GCC_VER")") && \
    echo "Using C++ includes $CPP_INCLUDES and $TRIPLET_INCLUDES to build for $TARGET_TRIPLE" && \
    CLANG_MAJOR_VERSION=$(clang --version | grep -oP "(?<=version )\d+") && \
    cmake -S llvm-project.src/runtimes -B runtimes \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_ASM_COMPILER=clang \
        -DCMAKE_C_COMPILER=clang \
        -DCMAKE_CXX_COMPILER=clang++ \
        -DCMAKE_ASM_COMPILER_TARGET="$TARGET_TRIPLE" \
        -DCMAKE_C_COMPILER_TARGET="$TARGET_TRIPLE" \
        -DCMAKE_CXX_COMPILER_TARGET="$TARGET_TRIPLE" \
        # Old-world ABI target code uses the large code model (see Loongson's
        # compiler-rt build docs at docs.loongnix.cn/llvm/Build/).
        -DCMAKE_ASM_FLAGS="-mcmodel=large" \
        -DCMAKE_C_FLAGS="-mcmodel=large" \
        -DCMAKE_CXX_FLAGS="-mcmodel=large" \
        # We're going to link the static libraries we build here into PIC images, so build the static libraries as PIC.
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
        -DCMAKE_SYSROOT="$ROOTFS_DIR" \
        # Specify linker to use for exes directly for CMake toolchain detection
        -DCMAKE_EXE_LINKER_FLAGS="-fuse-ld=lld -mcmodel=large" \
        # Don't search for tools in the sysroot as we're cross-compiling
        -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM="NEVER" \
        -DLLVM_USE_LINKER=lld \
        -DLLVM_ENABLE_RUNTIMES="libcxx;compiler-rt" \
        -DLIBCXX_ENABLE_SHARED=OFF \
        -DLIBCXX_CXX_ABI=libstdc++ \
        -DLIBCXX_CXX_ABI_INCLUDE_PATHS="$CPP_INCLUDES;$TRIPLET_INCLUDES" \
        -DCOMPILER_RT_CXX_LIBRARY="libcxx" \
        -DCOMPILER_RT_STATIC_CXX_LIBRARY=ON \
        -DCOMPILER_RT_BUILD_SANITIZERS=OFF \
        -DCOMPILER_RT_BUILD_MEMPROF=OFF \
        -DCOMPILER_RT_BUILD_XRAY=OFF \
        -DCOMPILER_RT_BUILD_CTX_PROFILE=OFF \
        -DCOMPILER_RT_BUILD_LIBFUZZER=OFF \
        -DCOMPILER_RT_DEFAULT_TARGET_ONLY=ON \
        -DCOMPILER_RT_INSTALL_PATH="/usr/local/lib/clang/$CLANG_MAJOR_VERSION" && \
    cmake --build runtimes -j $(getconf _NPROCESSORS_ONLN) && \
    cmake --install runtimes --prefix "$ROOTFS_DIR/usr"

# ── Stage 3: final image (lns8 crossdeps-llvm + lns8 sysroot + builtins) ────
FROM mcr.microsoft.com/dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-crossdeps-llvm-amd64-lns8

ARG ROOTFS_DIR

COPY --from=runtimes-builder /usr/local/lib/clang /usr/local/lib/clang/
COPY --from=runtimes-builder $ROOTFS_DIR $ROOTFS_DIR

COPY verify-sysroot.sh /usr/local/bin/verify-sysroot.sh
RUN tdnf install -y findutils && \
    chmod +x /usr/local/bin/verify-sysroot.sh && \
    GLIBC_EXPECTED=2.28 bash /usr/local/bin/verify-sysroot.sh

ENV ROOTFS_DIR=$ROOTFS_DIR \
    GLIBC_EXPECTED=2.28 \
    PKG_CONFIG_PATH=$ROOTFS_DIR/usr/lib64/pkgconfig:$ROOTFS_DIR/usr/share/pkgconfig \
    PKG_CONFIG_SYSROOT_DIR=$ROOTFS_DIR
