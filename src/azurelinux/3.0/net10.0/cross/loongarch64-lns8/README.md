# azurelinux-3.0-net10.0-cross-loongarch64-lns8

交叉编译镜像：在 x64 上为 **LoongArch64 / Loongnix Server 8.4（lns8，旧世界 ABI，glibc 2.28）**
构建 .NET 10。

与官方 `cross/loongarch64`（Debian sid sysroot，glibc 2.42）的差异有两处：

1. **sysroot 来源**：本镜像从 **Loongnix Server 8.4 RPM 仓库**构建 sysroot（glibc 2.28），
   因此产物二进制只引用 ≤ 2.28 的 GLIBC 符号，可运行于 lns8 等旧世界 LoongArch 发行版。
2. **宿主工具链**：lns8 是旧世界 ABI，上游 LLVM 只支持新世界，因此必须使用
   **Loongson 分支 LLVM 22.1.8**（`llvm-project_22.1.8-1.src.tar.gz`，从源码编译；
   2026-09-18 发布，LoongArch 支持覆盖 clang/llvm/lld/compiler-rt/libc++/libc++abi/openmp）。

因此本镜像不再是单镜像，而是复用仓库现有 `crossdeps-builder → crossdeps-llvm → cross`
三段结构，依赖顺序构建三个镜像：

| 镜像 | 内容 |
|---|---|
| `...-crossdeps-builder-lns8-amd64` | fork LLVM 22.1.8 源码编译至 `/opt/llvm`（构建最久） |
| `...-crossdeps-llvm-lns8-amd64` | 承载 fork LLVM 工具链 |
| `...-cross-loongarch64-lns8` | 最终交叉构建镜像（lns8 sysroot + fork 工具链 + compiler-rt builtins） |

## 一、构建镜像

### 方式 A：用仓库构建脚本（推荐，与 CI 一致）

仓库根目录的 `build.ps1` 会自动把 `eng/common` 暂存进各 Dockerfile 的构建上下文，
并由 ImageBuilder 按 manifest.json 的依赖图依次构建三个镜像：

```bash
pwsh build.ps1 -Paths \
    'src/azurelinux/3.0/net10.0/crossdeps-builder/lns8/amd64/Dockerfile' \
    'src/azurelinux/3.0/net10.0/crossdeps-llvm/lns8/amd64/Dockerfile' \
    'src/azurelinux/3.0/net10.0/cross/loongarch64-lns8/Dockerfile'
```

### 方式 B：直接 docker build（按依赖顺序）

> Dockerfile 里的 `FROM` 写的是完整的 `mcr.microsoft.com/dotnet-buildtools/prereqs:...`
> 名字。BuildKit 解析 `FROM` 时只认与引用**完全同名**的本地镜像，所以本地构建必须
> 用完整的 mcr 名字打 tag（发布后与 CI 的引用方式也一致）。

```bash
# 1. crossdeps-builder（fork LLVM 全量编译，约 30-90 分钟）
#    该 Dockerfile 有 COPY eng/common/cross/，先把 eng/common 暂存进构建上下文：
cp -r eng/common src/azurelinux/3.0/net10.0/crossdeps-builder/lns8/amd64/eng
docker build \
    -f src/azurelinux/3.0/net10.0/crossdeps-builder/lns8/amd64/Dockerfile \
    -t mcr.microsoft.com/dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-crossdeps-builder-lns8-amd64 \
    src/azurelinux/3.0/net10.0/crossdeps-builder/lns8/amd64/

# 2. crossdeps-llvm（FROM 上一步本地构建的 builder 镜像，全名 tag 匹配）
docker build \
    -f src/azurelinux/3.0/net10.0/crossdeps-llvm/lns8/amd64/Dockerfile \
    -t mcr.microsoft.com/dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-crossdeps-llvm-lns8-amd64 \
    src/azurelinux/3.0/net10.0/crossdeps-llvm/lns8/amd64/

# 3. cross（sysroot 构建 + runtimes 交叉编译 + 自检）
docker build \
    -f src/azurelinux/3.0/net10.0/cross/loongarch64-lns8/Dockerfile \
    -t mcr.microsoft.com/dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-cross-loongarch64-lns8 \
    src/azurelinux/3.0/net10.0/cross/loongarch64-lns8/
```

> 步骤 2、3 的 `FROM` 依赖步骤 1、2 本地构建出的同名 tag；若在隔离环境中构建，
> 需先拉取已发布的基础镜像或重 tag。构建产物也可以按习惯另加短名 tag。

### 第 3 步 sysroot 的两种来源

#### 来源 1：直接从 RPM 仓库生成（默认，纯 x64，无需 qemu/硬件）

- 阶段 1 从 `https://pkg.loongnix.cn/loongnix-server/8.4/BaseOS/loongarch64/release`
  与 `https://pkg.loongnix.cn/loongnix-server/8.4/AppStream/loongarch64/release` 下载
  `lns8-packages.txt` 列出的 loongarch64 RPM 并解包为 sysroot（BaseOS 优先）。
- 若构建环境无法访问 pkg.loongnix.cn，可用
  `--build-arg LNS_REPO_BASE=<镜像地址> --build-arg LNS_REPO_APPSTREAM=<镜像地址>` 覆盖。
- 注意：`install-rpms.py` 不做依赖解析，包列表需要与原生构建环境保持同步
  （含 libcurl/krb5/lttng-ust 等运行时依赖链，均已在包列表中显式列出）。

#### 来源 2：使用 qemu 导出的 sysroot（推荐，dnf 解析依赖 + %post 已执行，最忠实）

1. 在 amd64 上注册 loong64 模拟（需要 root docker + 内核 binfmt_misc）：

   ```bash
   docker run --privileged --rm cr.loongnix.cn/tonistiigi/binfmt:x86_add_loongarch --install loongarch64
   ```

   > ⚠ **旧世界 ABI 需要 Loongson 补丁版 qemu**。若宿主机已有发行版注册的上游 qemu
   > （如 Ubuntu 的 `qemu-loongarch64` 条目，仅支持新世界 ABI），安装器会检测到
   > "already registered" 而跳过注册，lns8 二进制随后报
   > `cannot stat shared object: Error 38`。此时先禁用旧条目再注册：
   >
   > ```bash
   > sudo sh -c 'echo -1 > /proc/sys/fs/binfmt_misc/qemu-loongarch64'
   > docker run --privileged --rm cr.loongnix.cn/tonistiigi/binfmt:x86_add_loongarch --install loongarch64
   > ```
   >
   > （重启后发行版条目会由 systemd-binfmt 重新注册，必要时再执行一次或写入
   > `/etc/binfmt.d/` 覆盖。）

2. 启动 lns8 容器并安装构建依赖、导出 sysroot：

   ```bash
   docker run -d --name lns8-export --platform linux/loong64 \
       cr.loongnix.cn/loongson/loongnix-server:8.4 sleep infinity

   # 容器内安装构建依赖（qemu 模拟下较慢；包列表参考 lns8-packages.txt）
   docker exec lns8-export dnf install -y \
       gcc gcc-c++ glibc-devel glibc-static binutils binutils-devel make cmake \
       zlib-devel openssl-devel libcurl-devel krb5-devel lttng-ust-devel elfutils-devel

   # 导出（bin/sbin/lib/lib64 是指向 usr 的符号链接，tar 会保留；
   # 包内的绝对符号链接由 fix-sysroot-symlinks.py 在镜像构建时修正）
   docker exec lns8-export bash -c 'cd / && tar -czf /tmp/lns8-sysroot.tar.gz usr bin sbin lib lib64 etc'
   ```

   > 注：lns8 的 dnf/tar 会派生子进程（rpm、python 等），必须经由内核 binfmt 逐次
   > 拉起 qemu 才能正常工作；只把 qemu 挂进容器当入口的方式无法执行子进程，
   > **导出 sysroot 前请确保上一步的 binfmt 修复已生效**（验证方法见上文 ⚠ 中的
   > `cannot stat shared object: Error 38` 说明）。

3. 把产物放入构建上下文再构建第 3 步镜像（存在该文件时自动优先使用）：

   ```bash
   docker cp lns8-export:/tmp/lns8-sysroot.tar.gz \
       src/azurelinux/3.0/net10.0/cross/loongarch64-lns8/sysroot/
   docker rm -f lns8-export

   docker build \
       -f src/azurelinux/3.0/net10.0/cross/loongarch64-lns8/Dockerfile \
       -t mcr.microsoft.com/dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-cross-loongarch64-lns8 \
       src/azurelinux/3.0/net10.0/cross/loongarch64-lns8/
   ```

   构建完成后删除该 tar.gz，避免污染后续构建（已在 .gitignore 排除）。

## 二、验证镜像

构建末段已自动执行 `verify-sysroot.sh`（glibc 版本与符号基线、GCC triple、
动态链接器、builtins、关键库、C++ 头文件、交叉编译冒烟测试）。可再次手动运行：

```bash
docker run --rm mcr.microsoft.com/dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-cross-loongarch64-lns8 \
    bash /usr/local/bin/verify-sysroot.sh
```

预期关键输出：

- `GNU C Library ... release version 2.28`
- `符号版本基线: GLIBC_2.28`
- `GCC triple: loongarch64-linux-gnu`（lns8 实际 triple，与 dotnet 构建系统硬编码一致）
- 动态链接器为 `ld.so.1`（旧世界特征；新世界是 `ld-linux-loongarch-lp64d.so.1`）
- `libclang_rt.builtins-loongarch64.a` 存在
- 冒烟测试 `✅ 编译成功`，ELF Machine 为 LoongArch，floor ≤ GLIBC_2.28

## 三、使用镜像交叉构建 .NET 10 SDK

```bash
docker run --platform linux/amd64 --rm \
    -v ~/dotnet:/dotnet -w /dotnet \
    -e ROOTFS_DIR=/crossrootfs/loongarch64 \
    mcr.microsoft.com/dotnet-buildtools/prereqs:azurelinux-3.0-net10.0-cross-loongarch64-lns8 \
    ./build.sh --clean-while-building --prep -sb \
        --os linux --rid linux-loongarch64 --arch loongarch64
```

在 lns8 上验证产物（qemu 模拟；binfmt 注意事项见上文来源 2）：

```bash
docker run --platform linux/loong64 --rm -v <artifacts-dir>:/out \
    cr.loongnix.cn/loongson/loongnix-server:8.4 /out/dotnet --info
```

宿主 binfmt 无法修改时的等价做法：

```bash
docker run --rm \
    -v /tmp/qemu-loongarch64:/qemu-loongarch64:ro \
    -v <artifacts-dir>:/out \
    --entrypoint /qemu-loongarch64 \
    cr.loongnix.cn/loongson/loongnix-server:8.4 -L / /out/dotnet --info
```

> ⚠ .NET runtime 对旧世界 ABI 的支持需端到端验证；若 runtime 产物仍为新世界格式，
> 需要 Loongson 运行时补丁（超出本镜像范围，需另行决策）。

## 四、目录内容

| 路径 | 说明 |
|---|---|
| `crossdeps-builder/lns8/amd64/Dockerfile` | fork LLVM 22.1.8 源码编译（sha256 固定校验，无 GPG 签名可用） |
| `crossdeps-llvm/lns8/amd64/Dockerfile` | 把 `/opt/llvm` 承载为 `/usr/local` |
| `cross/loongarch64-lns8/Dockerfile` | 三阶段：sysroot 构建 → LLVM runtimes 交叉编译 → 最终镜像 |
| `cross/loongarch64-lns8/install-rpms.py` | 解析 yum repomd、下载 loongarch64 RPM 并解包（纯 Python + bsdtar） |
| `cross/loongarch64-lns8/fix-sysroot-symlinks.py` | 将 RPM 包内的绝对符号链接改写为 sysroot 内的相对链接 |
| `cross/loongarch64-lns8/lns8-packages.txt` | sysroot 包列表（无依赖解析，需手工维护） |
| `cross/loongarch64-lns8/verify-sysroot.sh` | 镜像内自检脚本 |
| `cross/loongarch64-lns8/sysroot/` | 可选：放置 qemu 导出的 `lns8-sysroot.tar.gz`（gitignore） |

## 五、注意事项

- **旧世界 ABI**：lns8 产物只能在旧世界系统（lns8/lnd20 等）运行；新世界系统请用
  `cross-loongarch64-lns23` 镜像。这也是必须使用 Loongson 分支 LLVM 的原因——上游
  LLVM（任何版本）只生成新世界二进制。
- **`-mcmodel=large`**：旧世界目标代码使用 large code model（Loongson 官方构建文档
  要求，compiler-rt 等已在镜像内按此构建）。若手动用本镜像的 clang 编译目标代码，
  同样需要 `-mcmodel=large`。
- **三元组**：lns8 的实际 triple 就是 `loongarch64-linux-gnu`，与 dotnet 构建系统
  硬编码一致，无需 lns23 那样的 triple 兼容符号链接（Dockerfile 阶段 1 自动检测跳过）。
- **包差异**：lns8 三个仓库（BaseOS/AppStream/PowerTools）均无 `libunwind`、`libomp`
  （8.4 无公共 EPOL），包清单已相应移除；`libbrotli` 在 lns8 名为 `brotli`。若 dotnet
  构建因缺少 libunwind 报错，后备方案是从 Loongson 交叉工具链
  `loongson-gnu-toolchain-8.3-x86_64-loongarch64-linux-gnu-rc1.6.tar.xz` 提取补入 sysroot。
- **仓库限流**：pkg.loongnix.cn 对大量下载会限流（连接失败/500），`install-rpms.py`
  内置重试与退避；若持续失败，改用来源 2（qemu 导出 sysroot）。
- **fork 编译**：builder 镜像编译 fork LLVM 耗时与核数强相关——32 核实测 20.1.8 约
  12.5 分钟（22.1.8 源码包大 ~14%，略久）；4 核 CI runner 上按核数外推约 1.5-2.5 小时，
  故 workflow 给了 350 分钟超时并预先清理磁盘。fork 未提供 GPG 签名，仅以 sha256 固定
  校验；若 fork 在 Azure Linux 3.0 宿主下编译失败，可参照 Loongson 官方文档补
  `-G Ninja`、`-DLLVM_BUILD_LLVM_DYLIB=ON`、`-DLLVM_ENABLE_RTTI=ON`。
- **基线差异**：本线 fork 基线为 22.1.8，而 `crossdeps-builder/amd64` 与
  `crossdeps-amd64`（MCR 上游镜像）内的 LLVM 仍是 20.1.8；两者只在 lns8 链里以
  `/usr/local` 覆盖的方式叠加，互不影响。
- **已知上游问题（已实测）**：LTTng 的 STAP 风格内联汇编会触发 LoongArch 后端崩溃
  （`LoongArchDAGToDAGISel::SelectBaseAddr`，rc=139）。最小复现（`TRACEPOINT_EVENT` +
  `tracepoint()`，以及 `tracef()` 便捷宏）实测结果：fork 20.1.8、fork 22.1.8 与**上游**
  20.1.8 均崩溃，且与 `-mcmodel=large`、`-O0/-O2` 都无关 → 属上游 bug，不是 22.1.8
  升级引入的回归。lns23 线此前用上游 clang 编译 coreclr `eventtrace.cpp` 实测通过，
  说明真实文件的用法未必触发；lns8 侧是否受影响需以实际 .NET 构建为准（本线端到端
  构建尚未验证，见上文第三节末尾）。
