#!/bin/bash
set -euo pipefail
# =============================================================================
# verify-sysroot.sh
#
# Runs inside the lns23 cross image to verify sysroot completeness:
# host LLVM tools, glibc version/symbol floor, GCC triple, dynamic linker,
# compiler-rt builtins, key libraries, C++ headers, and a cross-compile
# smoke test. Executed at the end of the image build.
# =============================================================================

ROOTFS_DIR="${ROOTFS_DIR:-/crossrootfs/loongarch64}"
# Highest GLIBC_2.x symbol version the target lns23 system (glibc 2.38) provides.
# Safe under `set -u`; can be overridden from the environment.
GLIBC_EXPECTED="${GLIBC_EXPECTED:-2.38}"

echo "=== Sysroot 验证 ==="
echo ""
echo "--- 系统信息 ---"
echo "  ROOTFS_DIR=${ROOTFS_DIR}"
echo ""

echo "--- 宿主 clang / LLVM 工具 ---"
clang --version | head -1
for tool in llvm-objcopy llvm-strip llvm-ar llvm-nm llvm-readelf llvm-objdump; do
    path=$(command -v "$tool" 2>/dev/null || echo "MISSING")
    echo "  $tool: $path"
done
echo ""

echo "--- glibc 版本 ---"
LIBC_PATH=""
if [[ -f "${ROOTFS_DIR}/lib64/libc.so.6" ]]; then
    LIBC_PATH="${ROOTFS_DIR}/lib64/libc.so.6"
elif [[ -f "${ROOTFS_DIR}/usr/lib64/libc.so.6" ]]; then
    LIBC_PATH="${ROOTFS_DIR}/usr/lib64/libc.so.6"
fi
if [[ -n "$LIBC_PATH" ]]; then
    strings "$LIBC_PATH" 2>/dev/null | grep "GNU C Library" || echo "  ❌ 无法读取版本"
    # 符号版本基线：libc 定义的最高 GLIBC_2.x 版本 = 产物 floor 上限
    SYM_FLOOR=$(readelf -V "$LIBC_PATH" 2>/dev/null | grep -o 'GLIBC_2\.[0-9]*' | sort -Vu | tail -1)
    echo "  符号版本基线: ${SYM_FLOOR:-未知}"
else
    echo "  ❌ libc.so.6 未找到"
fi
echo ""

echo "--- GCC triple 检测 ---"
TRIPLE=""
if [[ -d "${ROOTFS_DIR}/usr/lib/gcc" ]]; then
    TRIPLE=$(ls "${ROOTFS_DIR}/usr/lib/gcc/" 2>/dev/null | head -1)
    if [[ -n "$TRIPLE" ]]; then
        echo "  GCC triple: $TRIPLE"
        GCC_VER=$(ls "${ROOTFS_DIR}/usr/lib/gcc/${TRIPLE}/" 2>/dev/null | head -1)
        echo "  GCC 版本:   ${GCC_VER:-未知}"
    else
        echo "  ⚠️  /usr/lib/gcc/ 为空"
    fi
else
    echo "  ⚠️  /usr/lib/gcc 目录不存在"
fi
echo ""

echo "--- 动态链接器 ---"
find "${ROOTFS_DIR}" -name "ld-linux-loongarch*" -type f 2>/dev/null | head -5 || echo "  ❌ 未找到"
echo ""

echo "--- compiler-rt builtins ---"
find /usr/local/lib/clang -name "libclang_rt.builtins-loongarch64.a" 2>/dev/null | head -3 || echo "  ❌ 未找到"
echo ""

echo "--- 关键库检查 ---"
for lib in libc.so.6 libstdc++.so.6 libgcc_s.so.1 libz.so.1 libssl.so libcrypto.so; do
    found=$(find "${ROOTFS_DIR}" -name "$lib" \( -type f -o -type l \) 2>/dev/null | head -1)
    if [[ -n "$found" ]]; then
        echo "  ✅ $lib → ${found#${ROOTFS_DIR}}"
    else
        echo "  ⚠️  $lib — 缺失"
    fi
done
echo ""

echo "--- C++ 标准库头文件 ---"
CXX_INCLUDE=$(find "${ROOTFS_DIR}/usr/include/c++" -maxdepth 0 -type d 2>/dev/null | head -1)
if [[ -n "$CXX_INCLUDE" ]]; then
    echo "  目录: ${CXX_INCLUDE#${ROOTFS_DIR}}"
    for hdr in cstdio iostream vector string algorithm; do
        found=$(find "${CXX_INCLUDE}" -name "$hdr" -type f 2>/dev/null | head -1)
        if [[ -n "$found" ]]; then
            echo "  ✅ $hdr"
        else
            echo "  ⚠️  $hdr — 缺失"
        fi
    done
else
    echo "  ❌ /usr/include/c++/ 目录不存在（C++ 头文件未导出！）"
fi
echo ""

echo "--- 交叉编译冒烟测试 ---"
DETECTED_TRIPLE="${TRIPLE:-loongarch64-linux-gnu}"
echo "  使用 triple: $DETECTED_TRIPLE"

cat > /tmp/test.c << 'SMOKE_EOF'
#include <stdio.h>
int main() { printf("hello from loongarch64\n"); return 0; }
SMOKE_EOF

if clang --target="$DETECTED_TRIPLE" --sysroot="${ROOTFS_DIR}" -fuse-ld=lld \
    -o /tmp/test /tmp/test.c 2>&1; then
    echo "  ✅ 编译成功"
    echo "  --- ELF 结构 ---"
    llvm-readelf -h /tmp/test 2>/dev/null | grep -E 'Machine|Class' || true
    echo "  --- 产物 glibc floor（引用符号的最高 GLIBC_2.x 版本）---"
    FLOOR=$(readelf -V /tmp/test 2>/dev/null | grep -o 'GLIBC_2\.[0-9]*' | sort -Vu | tail -1)
    echo "  floor: ${FLOOR:-未知}（目标系统 glibc 必须 ≥ 此版本）"
    FLOOR_VER="${FLOOR#GLIBC_}"
    if [[ -n "$GLIBC_EXPECTED" && -n "$FLOOR_VER" && "$FLOOR_VER" > "$GLIBC_EXPECTED" ]]; then
        echo "  ❌ floor 高于期望的 $GLIBC_EXPECTED"
    elif [[ -n "$FLOOR_VER" ]]; then
        echo "  ✅ floor ≤ 目标 glibc $GLIBC_EXPECTED，产物可在 lns23 上运行"
    fi
else
    echo "  ❌ 编译失败"
    echo "  --- 诊断信息 ---"
    echo "  clang 搜索路径:"
    clang --target="$DETECTED_TRIPLE" --sysroot="${ROOTFS_DIR}" -fuse-ld=lld \
        -o /tmp/test /tmp/test.c -v 2>&1 | grep -E 'search starts|Selected GCC|programs' || true
    echo "  crt 文件:"
    find "${ROOTFS_DIR}" -name 'crt[1in].o' 2>/dev/null | head -10
    echo "  libc:"
    find "${ROOTFS_DIR}" -name 'libc.so*' 2>/dev/null | head -5
fi

rm -f /tmp/test.c /tmp/test
echo ""
echo "=== 验证完成 ==="
