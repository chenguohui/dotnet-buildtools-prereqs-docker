# azurelinux-3.0-net10.0-cross-loongarch64-lns23

交叉编译镜像：在 x64 上为 **LoongArch64 / Loongnix Server 23.3 (lns23)** 构建 .NET 10。

与官方 `cross/loongarch64`（Debian sid sysroot，glibc 2.42）的唯一实质差异是 sysroot
来源：本镜像从 **Loongnix Server 23 RPM 仓库**构建 sysroot（glibc 2.38），因此产物二进制
只引用 ≤ 2.38 的 GLIBC 符号，可运行于 lns23 及更高 glibc 的新世界 LoongArch 发行版。
宿主工具链（clang/lld 20.1.8、cmake/ninja 等）与官方镜像完全一致。

## 一、构建镜像

### 方式 1：直接从 RPM 仓库生成 sysroot（默认，纯 x64，无需 qemu/硬件）

```bash
docker build \
    -f src/azurelinux/3.0/net10.0/cross/loongarch64-lns23/Dockerfile \
    -t dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-cross-loongarch64-lns23 \
    src/azurelinux/3.0/net10.0/cross/loongarch64-lns23/
```

- 阶段 1 从 `https://pkg.loongnix.cn/loongnix-server/23/os/loongarch64` 下载
  `lns23-packages.txt` 列出的 loongarch64 RPM 并解包为 sysroot。
- 若构建环境无法访问 pkg.loongnix.cn，可用
  `--build-arg LNS_REPO_BASE=<镜像地址>` 覆盖。
- 注意：`install-rpms.py` 不做依赖解析，包列表需要与原生构建环境保持同步。

### 方式 2：使用 qemu 导出的 sysroot（推荐，yum 解析依赖 + %post 已执行，最忠实）

1. 在 amd64 上注册 loong64 模拟（需要 root docker + 内核 binfmt_misc）：

   ```bash
   docker run --privileged --rm tonistiigi/binfmt:latest --install loong64
   ```

2. 进入 lns23 容器导出 sysroot（脚本来自 `custom/scripts/`，仅作参考工具）：

   ```bash
   docker run --rm -it --platform linux/loong64 \
       -v "$(pwd)/custom/scripts:/scripts" \
       -v "$(pwd)/custom/output:/custom/output" \
       lcr.loongnix.cn/loongnix-server/loongnix-server:23.3 bash

   # 容器内（qemu 模拟下跳过 dotnet 原生编译验证）
   /scripts/build-native-sysroot.sh --target lns23 --skip-dotnet-verify
   ```

3. 把产物放入构建上下文再构建（存在该文件时自动优先使用）：

   ```bash
   cp custom/output/lns23-sysroot.tar.gz \
       src/azurelinux/3.0/net10.0/cross/loongarch64-lns23/sysroot/
   docker build \
       -f src/azurelinux/3.0/net10.0/cross/loongarch64-lns23/Dockerfile \
       -t dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-cross-loongarch64-lns23 \
       src/azurelinux/3.0/net10.0/cross/loongarch64-lns23/
   ```

   构建完成后删除该 tar.gz，避免污染后续构建（已在 .gitignore 排除）。

## 二、验证镜像

构建末段已自动执行 `verify-sysroot.sh`（glibc 版本与符号基线、GCC triple、
动态链接器、builtins、关键库、C++ 头文件、交叉编译冒烟测试）。可再次手动运行：

```bash
docker run --rm dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-cross-loongarch64-lns23 \
    bash /usr/local/bin/verify-sysroot.sh
```

预期关键输出：

- `GNU C Library ... release version 2.38`
- `符号版本基线: GLIBC_2.38`
- 冒烟测试 `✅ 编译成功`，ELF Machine 为 LoongArch，floor ≤ GLIBC_2.38

## 三、使用镜像交叉构建 .NET 10 SDK

```bash
docker run --platform linux/amd64 --rm \
    -v ~/dotnet:/dotnet -w /dotnet \
    -e ROOTFS_DIR=/crossrootfs/loongarch64 \
    dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-cross-loongarch64-lns23 \
    ./build.sh --clean-while-building --prep -sb \
        --os linux --rid linux-loongarch64 --arch loongarch64
```

在 lns23.3 上验证产物（qemu 模拟）：

```bash
docker run --platform linux/loong64 --rm -v <artifacts-dir>:/out \
    lcr.loongnix.cn/loongnix-server/loongnix-server:23.3 \
    /out/dotnet --info
```

## 四、目录内容

| 文件 | 说明 |
|---|---|
| `Dockerfile` | 三阶段：sysroot 构建 → LLVM runtimes 交叉编译 → 最终镜像 |
| `install-rpms.py` | 解析 yum repomd、下载 loongarch64 RPM 并解包（纯 Python + bsdtar） |
| `fix-sysroot-symlinks.py` | 将 RPM 包内的绝对符号链接改写为 sysroot 内的相对链接 |
| `lns23-packages.txt` | sysroot 包列表（无依赖解析，需手工维护） |
| `verify-sysroot.sh` | 镜像内自检脚本 |
| `sysroot/` | 可选：放置 qemu 导出的 `lns23-sysroot.tar.gz`（gitignore） |

## 五、注意事项

- lns23 为**新世界 ABI**，与官方 LLVM 20.1.8 新世界工具链兼容；旧世界系统
  （lns8/lnd20 等）需要 Loongson 分支工具链，不适用本镜像。
- dotnet 构建系统硬编码 `loongarch64-linux-gnu` triple，而 lns23 的实际 triple 是
  `loongarch64-loongnix-linux`；Dockerfile 阶段 1 已通过符号链接完成兼容。
- glibc 实际版本以构建日志/verify 输出为准（23 系列流式仓库可能更新）。
