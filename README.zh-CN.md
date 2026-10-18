# camtool — Sony ZV-E10 服务模式调试工具箱

[English](README.md) | **简体中文**

> **免责声明**：本项目是**自有设备**的服务模式（Senser / Jiritsu）研究工具，
> 用于备份、诊断与自己机器的实验。它会写块设备、能变砖、**没有任何保修**。
> 不要在别人的设备上用，不要用于规避版权保护。Sony 固件与商标权利属原权利人，
> 本仓库不包含任何固件或设备数据。发现漏洞建议同时向 Sony PSIRT 报告。

`cam.py` 是日常操作入口（一条命令一个动作），其余脚本是它的补件：整盘备份/还原、
USB 恢复、载荷构建、回归测试。**代码在本目录，数据在 `../senser_probe/`。**

```
<workspace>/
├── camtool/                 ← 工具箱（本目录，可整体拷走 / 单独建仓）
│   ├── cam.py               主入口，40+ 子命令
│   ├── camdata.py           代码目录 / 数据目录解析（唯一来源）
│   ├── jiritsu_lib.py       连接层：认证 + 包收发（被 cam.py 与各脚本复用）
│   ├── test_cam.py          16 项回归（改完工具先跑它）
│   ├── usb_recover.py       认证错位时 dev.reset() 重认证（免拔电池）
│   ├── dump_flash.py        整段读分区 → backup_full/
│   ├── restore_flash.py     整段写回分区（危险）
│   ├── verify_dump.py       重读设备校验备份 md5
│   ├── cleanup_jiritsu.py   /tmp/jiritsu 白名单清理
│   ├── verify_ctor.py       最小 dlopen 链路验证（部署载荷看产物）
│   ├── build_payload_v2.py  ★ 唯一正确的载荷构建器
│   ├── build_exec_payload.py  v2 的薄包装（只换源码）
│   └── payload/             .s 源码 / .bin 裸代码 / .so 成品 / legacy/ 历史
└── senser_probe/            ← 数据 + 逆向分析笔记（**不入库**，见第 8 节）
    ├── backup_full/         1.5GB 全盘备份（15 个 .bin + rootfs/usr/log 解包）
    ├── evidence/            snapshots / proto / setting / log 采样
    └── .cam_state.json      caps 等探测结果缓存
```

---

## 0. 30 秒上手

```bash
cd Sony-ZVE-10-Camtool          # 克隆后的目录名
export MSYS_NO_PATHCONV=1                      # Git Bash 必须
P=python            # 换成你装了 pyusb 的那个解释器（见 1. 环境）

$P cam.py doctor                               # 一条命令看通道/固件/备份/载荷/可写
$P cam.py info                                 # 设备 + 分区概览
$P cam.py sh "uname -a"                        # 远程执行
```

`doctor` 全绿就可以干活。任何一步失败，先看文末「故障排查」。

---

## 1. 环境与前提

| 项目 | 要求 |
|---|---|
| Python | 3.8+，需装 `pyusb` + `libusb-package`（`pip install -r requirements.txt`）。**装错解释器就会 `ModuleNotFoundError: usb`**，建议单独建 venv |
| 路径 | Git Bash 下 CLI 传路径参数必须 `MSYS_NO_PATHCONV=1`，否则 `/tmp/jiritsu` 会被改写成 `C:/Program Files/Git/tmp/jiritsu` |
| 相机 USB 设置 | 必须是「**大容量存储**」，不能是「自动」。否则认证后会被带偏到 0x0994(HID) / 0x0D96(MTP)，后续全部失败 |
| 连接模型 | 0x0E8A → 重枚举 → 0x0D95 → 0x0336 二次认证（SHA1 + 512B 密钥表）。`cam.py` 每建一次连接都要走一遍，约 1~2s |
| 超时 | 默认 15s（`export CAM_USB_TIMEOUT=30000` 可改）。驱动原始默认只有 5s，读大文件必超时 |
| 相机硬件 | armv7l，Linux 3.0.27_nl-rt106+ PREEMPT RT，**MemTotal 仅 128MB**，别在机上跑吃内存的东西 |

**一个进程一条连接**：每个 `cam.py` 进程结束会让设备回 MSC 并重新枚举（几秒），
`cam.py` 内置 20s 等待重试，所以连续跑多条命令会慢但不会失败。要快就用
`cam.py shell`（一条连接跑多条）。

---

## 2. cam.py 子命令手册

通用约定：
- 远端路径一律写绝对路径；`/tmp` 是 nflasha10 上的**持久 vfat**（不是 tmpfs），重启不清。
- 所有「写」类命令都有回读校验或明确 resp；失败会 `die()` 并给下一步提示。
- 破坏性命令必须带 `--confirm`。

### 2.1 设备与备份

| 命令 | 作用 | 注意 |
|---|---|---|
| `cam.py info` | 固件版本、GET_VERSION、关键文件、全部分区 | 最常用的「相机还活着吗」 |
| `cam.py doctor` | 6 项自检：通道/固件/tmmc+libJit/备份完整性/载荷/可写 | 改完工具或换机器先跑 |
| `cam.py parts [--probe]` | 分区表（内置实测值）；`--probe` 现场读 `/proc/partitions` | 内置表比被截断的 procfs 更全 |
| `cam.py backup [dev...] [--all]` | 备份分区到 `backup_full/` | 默认只备 4/6/7/15；`--all` 约 1GB，慢 |
| `cam.py restore <dev> --confirm [--force]` | 整段写回分区 | **最危险**。不带 `--force` 只写文件长度内；`--force` 全量重写。写前先 `verify` |
| `cam.py verify [dev...]` | 重读设备算 md5 与本地比对 | 静态分区应全一致；nflasha4 是活跃区，两次 dump 会不同 |

### 2.2 文件

| 命令 | 作用 | 注意 |
|---|---|---|
| `cam.py cat <remote>` | 读文件到 stdout | 大文件别用，会整读进内存 |
| `cam.py get <remote> [local]` | 下载 | 按 `st_size` 读；**procfs 文件 size=0，读回 0 字节**，要读 `/proc/*` 用 `sh "cat ..."` |
| `cam.py put <local> <remote>` | 上传 | 走 FileControl(0xff01)，任意路径 |
| `cam.py rm <remote>` | 删除 | **删 `/tmp/jiritsu/libJit.so` 会让 Jiritsu 卸载 nflasha6**（读返回 130，数据不丢），随后 `jir 0x4002` 或任何 cam.py 命令会自动重挂 |
| `cam.py hex <remote> [off] [len]` | 十六进制 | >8MB 自动改 `dd` 定位，不整读 |
| `cam.py slice <dev> <off> <len> [local]` | 只取设备一段 | 避免为看 1KB 下载 500MB |
| `cam.py pull <rdir> <ldir>` | 整目录下载（tar） | 比逐文件快一个数量级 |
| `cam.py push <ldir> <rdir>` | 整目录上传 | 注意相机 busybox 的 tar 不支持长选项 |

### 2.3 执行

| 命令 | 作用 | 注意 |
|---|---|---|
| `cam.py sh [-t 秒] "<cmd>"` | 远程执行，返回 stdout + exit code | 默认超时 25s。**复合命令用 `;` 串**，不要用 `&&` 之外的花活 |
| `cam.py shf <本地脚本>` | 把本地脚本整体丢上去执行 | 脚本第一行写 `#!/bin/sh` |
| `cam.py shell` | 交互式 shell，复用一条连接 | 退出后设备照例重枚举 |
| `cam.py run <本地bin> [args]` | 上传到 `/log/camtool/tool` 并执行 | 必须是 ARM 可执行文件（静态链接最稳） |
| `cam.py spawn "<cmd>" [日志]` | 后台常驻，立即返回 PID | 见下方「spawn 说明」 |
| `cam.py kill <pid\|名字> [--confirm]` | 杀进程 | 杀守护进程会触发一次瞬时「通道中断」，cam.py 自动重连，属正常 |
| `cam.py reboot [reboot\|poweroff] --confirm` | 重启/关机 | USB 会断开 |
| `cam.py watch "<cmd>" [间隔] [次数]` | 周期执行 | 观察参数变化用 |

**spawn 说明**：实现是「写 `daemon.sh` → `sh daemon.sh &`」。
这台机器没有 `setsid`/`nohup`，也不能用 `command -v`（sh 无此内建）。
载荷在 fork 后的子进程里 `close(3..255)`，所以守护进程的 fd 表只有 0/1/2，
**不会攥住 im.elf 的 USB/视频句柄**——这是 10-18 才修好的关键点，
之前的版本 spawn 之后相机会从 USB 消失。

### 2.4 查询

| 命令 | 作用 | 注意 |
|---|---|---|
| `cam.py ls [-l] [path]` | 列目录 | 传 `-l` 才会带时间/大小 |
| `cam.py lsr [root] [过滤]` | 递归列目录 | busybox **没有 find**，这是替代品；默认上限 2000 条 |
| `cam.py stat <path>` | 文件信息 | |
| `cam.py df` | 空间 | busybox **没有 df**，这里输出分区容量 + 挂载点/类型/读写 |
| `cam.py ps` / `cam.py mem` | 进程 / 内存 | mem 只有 128MB 总量，`MemFree` 常年 3~4MB 属正常 |
| `cam.py dmesg [行数] [过滤词]` | 内核日志尾 | 排查 USB/liro/jiritsu 第一入口 |
| `cam.py irq [过滤词]` | 中断表 | 看 `liro-*`、`dma330` 是否真在跑 |
| `cam.py log [--pull]` | /log 分区 | `--pull` 下载 |
| `cam.py snapshot` | 12 项诊断快照 → `evidence/snapshots/<时间>/` | 改机前后各来一次，配 `snapdiff` |
| `cam.py snapdiff [A] [B]` | 对比两份快照 | 不带参数自动取最近两次 |

### 2.5 分析

| 命令 | 作用 | 注意 |
|---|---|---|
| `cam.py diff <A> <B>` | 比较两个远端文件 | 会整读两个文件；`/setting/Backup.bin` 1.2MB 没问题 |
| `cam.py tail <remote> [字节]` | 看文件尾部 | 大文件用 `dd` 定位，不整读 |
| `cam.py grep <模式> <remote> [-i]` | 远端搜索 | 不下载整文件 |
| `cam.py md5 <remote>` | 远端 md5 | 有 `md5sum` 就用它，否则下载后本地算 |

### 2.6 系统

| 命令 | 作用 | 注意 |
|---|---|---|
| `cam.py caps [--refresh]` | 探测可用命令，缓存到 `.cam_state.json` | **结果要打折看**：`busybox --list` 列的是编译进 busybox 的 applet，不等于 PATH 里有软链 |
| `cam.py dev [过滤词]` | /proc/devices + /proc/misc + /dev | |
| `cam.py modinfo <模块>` | refcnt / holders / parameters | `modinfo liro` 看驱动参数入口 |
| `cam.py sysfs <路径> [值] [--confirm]` | 读/写 sysfs | **写必须 `--confirm`**；`/sys/power/image_drop` 是暖启动镜像开关，风险未知 |
| `cam.py setting [--backup]` | 看 /setting；`--backup` 下载 5 个文件 | |

### 2.7 挂载

| 命令 | 作用 | 注意 |
|---|---|---|
| `cam.py mount <dev> [挂载点]` | 自动探测 fs 类型挂载 | 默认挂到 `/tmp/mnt_<dev>`；挂 `/system`、`/setting` 可能与影像进程冲突 |
| `cam.py umount <挂载点>` | 卸载 | |
| `cam.py sd` | SD 卡状态 | mmca10 未挂载时用它确认卡在不在 |

### 2.8 协议级（逆向用）

| 命令 | 作用 | 注意 |
|---|---|---|
| `cam.py raw <pfunc> [hex] [--save] [--full]` | 任意 Senser 包 | `--save` 落 `evidence/proto/`；`--full` 打印全部响应 |
| `cam.py cmd <block> <code> [hex]` | AdjustControl 命令 | 色彩矩阵那条线的入口（block/code 待定） |
| `cam.py jir <code> [hex]` | Jiritsu 命令 | 常用：`0x400d` GET_VERSION、`0x4002` MOUNT |
| `cam.py pf` | pFunc / 命令码速查 | 不用翻记忆 |

### 2.9 维护

| 命令 | 作用 | 注意 |
|---|---|---|
| `cam.py reset` | USB 硬复位 | 连接层已内置，这是手动兜底 |
| `cam.py wait [秒]` | 等设备出现并报状态 | 重插线 / 开关机后用它，比反复 info 干净 |

---

## 3. 其余脚本

### 3.1 `dump_flash.py` — 整段读分区

```bash
python dump_flash.py                  # 默认 4/6/7/15
python dump_flash.py nflasha7 nflasha6
python dump_flash.py --all            # 全部已知分区（约 1GB）
python dump_flash.py --mbr            # /dev/nflasha 前 8MB（分区表/引导区）
```

输出到 `../senser_probe/backup_full/<dev>.bin`，同时打印 md5 与文件系统识别结果。
**注意**：500MB 的 nflasha11 约 47s，中途断开会造成认证错位，用 `usb_recover.py` 恢复。

### 3.2 `restore_flash.py` — 整段写回（危险）

```bash
python restore_flash.py nflasha13              # 预览
python restore_flash.py nflasha13 --confirm    # 只写文件长度内
python restore_flash.py nflasha13 --confirm --force   # 全量重写（含补零）
```

**注意**：写的是块设备原始字节，写错分区 = 变砖。写前必须
`verify_dump.py` 确认本地文件 md5 与设备一致（说明备份是好的、分区没在变）。
`nflasha4` 是运行时滚动写入区，**不要还原**。

### 3.3 `verify_dump.py` — 备份校验

```bash
python verify_dump.py            # 校验静态分区并写 manifest_static.txt
python verify_dump.py nflasha6
```

### 3.4 `cleanup_jiritsu.py` — 白名单清理 /tmp/jiritsu

```bash
python cleanup_jiritsu.py          # 预览
python cleanup_jiritsu.py --confirm
```

只删我们自己的产物（`libJit.so`、`run.sh`、`out.txt`、`ZZZ_*`、`*.log`），
保留 `TMMC.JIT`、`DIAC.csv`、`jit.log`、`history.txt` 等固件文件。

### 3.5 `usb_recover.py` — 认证错位恢复

```bash
python usb_recover.py
```

读大文件/中途打断后，`authenticate` 可能报 `NoneType`（无 cmd）。这时跑它：
`dev.reset()` + 重新认证，**不用拔电池**。恢复不了再断电。

### 3.6 `verify_ctor.py` — 最小 dlopen 链路验证

```bash
python verify_ctor.py                                  # 默认 libJit_ctor_v2.so
python verify_ctor.py payload/libJit_arm_ok.so /tmp/jiritsu/ARM_OK.txt
```

判定：`write(16) -> 0x0` 且 marker 出现 = 链路正常。
`write(16)` 抛 `GenericUsbException` = 载荷在 dlopen 阶段崩了，
im.elf 死 → **相机会自己重启**（约 30~40s），等 60s 再 `cam.py wait`。
（真断电只在 3 分钟还不回来时才需要，且必须**先拔 USB 线**，ZV-E10 可由 USB 供电。）

### 3.7 `build_payload_v2.py` — ★ 唯一正确的载荷构建器

```bash
python build_payload_v2.py <源码.bin> <输出.so> [补零到字节数] [thumb|arm]
python build_payload_v2.py payload/jit_exec_v11.bin payload/libJit_exec.so
python build_payload_v2.py payload/jit_arm_ok.bin payload/libJit_arm_ok.so 0 arm
```

生成的 ELF 结构（这是血泪换来的，别改）：

```
ph0  PT_LOAD  R+X   : 0 .. code_end        （ELF 头 + hash/sym/str + 代码）
ph1  PT_LOAD  R+W   : code_end .. +0x38    （.dynamic，vaddr 偏移一页）
ph2  PT_DYNAMIC     : 指向 ph1
DT_INIT = ctor_vaddr | (thumb ? 1 : 0)
e_flags = 0x05000200
```

**为什么必须这样**：
- `.dynamic` 若落在只读段，glibc `dlopen` 往里写 `l_info`/`DT_DEBUG` 时 **SIGSEGV**，
  im.elf 当场死、相机从 USB 消失。10-17 那个 528B `libJit.so` 就是这个病，从来没成功过。
- `DT_INIT` 的 Thumb 位必须和代码模式一致：`.thumb` 源码 → `|1`，`.arm` 源码 → `|0`。
  弄反了 CPU 用错模式解码，立刻崩。
- 用 `DT_INIT` 而不是 `DT_INIT_ARRAY`。

### 3.8 `build_exec_payload.py` — 薄包装

```bash
python build_exec_payload.py                       # jit_exec_v11.bin -> libJit_exec.so
python build_exec_payload.py <src.bin> <out.so> [arm|thumb]
```

只是 `build_payload_v2.py` 的调用封装（老版本自带坏骨架，已废弃）。

### 3.9 `test_cam.py` — 16 项回归

```bash
python test_cam.py          # 全部
python test_cam.py df       # 只跑名字含 df 的
```

一条连接里跑完 md5/hex/grep/tail/lsr/irq/dev/sysfs/diff/setting/jir/raw/ps/mem/sd/df。
改完 `cam.py` 必跑，当前基线 **16/16 通过**。

### 3.10 `camdata.py` — 数据目录解析

代码在本仓库，数据在 `../senser_probe/`（或 `$CAM_DATA_ROOT`）。想换数据位置：
`export CAM_DATA_ROOT=D:/某个目录`。所有脚本都从这里取 `BACKUP_DIR` / `EVIDENCE_DIR`。

### 3.11 `jiritsu_lib.py` — 连接层

```python
from jiritsu_lib import connect
j = connect()                 # 认证 + 返回 Jiritsu 实例
j.jiritsu(0x400d)             # 发 Jiritsu 命令
j.file_read('/dev/nflasha6')  # FileControl 读
j.file_write(path, blob)      # FileControl 写
j.select(path)                # 选路径（写 libJit.so 前的必要一步）
j.stop()
```

- `pkt(..., retries=2)` 默认失败重试 2 次；**破坏性命令（FORMAT/FIRMUP）传 `retries=0`**。
- 超时 `CAM_USB_TIMEOUT` 毫秒，默认 15000。

---

## 4. 载荷（最容易踩坑的地方）

### 4.1 现有成品

| 文件 | 大小 | 用途 |
|---|---|---|
| `payload/libJit_exec.so` | 440B | **生产载荷**。fork → 子进程关 fd 3..255 → `execve("/bin/sh", run.sh)`，输出进 `out.txt` |
| `payload/libJit_arm_ok.so` | 340B | ARM 模式验证载荷，产出 `/tmp/jiritsu/ARM_OK.txt` |
| `payload/legacy/libJit_v9.so` | 1820B | 今天最早跑通的参考实现（结构基准） |
| `payload/legacy/libJit.so` | 528B | **坏的，永远别部署**（`.dynamic` 在只读段） |
| `payload/legacy/libJit_ctor_v2.so` | 548B | 用 v2 重建的 legacy ctor。**ctor 确实会执行**（产出 `RCE_MARK.txt` + `ver.txt`，证实 `Uid: 0 0 0 0`、`CapEff: ffffffffffffffff`），但**返回时崩**——它把 1024B 直接 `read` 到线程栈上，撑爆栈帧。仅留档，别部署 |

### 4.2 组装工具链

没有全局 `arm-none-eabi-*`，用 Arduino 包里的：

```bash
# 用 Arduino 自带的 arm-none-eabi-gcc（Windows 默认在这个位置，版本号可能不同）
export PATH="$HOME/AppData/Local/Arduino15/packages/arduino/tools/arm-none-eabi-gcc/7-2017q4/bin:$PATH"
cd payload
arm-none-eabi-as -o jit_exec_v11.o jit_exec_v11.s
arm-none-eabi-objcopy -O binary -j .text jit_exec_v11.o jit_exec_v11.bin
cd .. && python build_exec_payload.py payload/jit_exec_v11.bin payload/libJit_exec.so
```

### 4.3 部署规则

- **必须走 Jiritsu `write(16)`**（`cam.py` 内部已做）。用 FileControl `0xff01` 写
  同一路径**不会触发 dlopen**。
- dlopen 按名缓存：ctor 崩过一次后，同一会话里再写任何库都命中缓存、ctor 不再执行。
  只能等 im.elf 真正重启（实测 im.elf 死后相机会自动重启，30~40s）。
- 每次改 ctor 前先 `verify_ctor.py` 用已知good载荷确认链路，再换新的。

---

## 5. 铁律

1. **打通 bootROM 救砖前，不碰 `nflasha3`（/system、含 av-cam.bin）**。
2. 改 `/usr` 或 `/` 必须「整盘 dump → 本地改 → restore 整盘写回」，禁止运行中局部写
   （页缓存不一致会触发 ext2 错误）。
3. `FORMAT(0x4003)` / `FIRMUP(0x4006)` 会卡死服务，**不要发**。
4. 判断「真的断电」：ZV-E10 可由 USB 供电，**先拔 USB 线**，再关电源/取电池。
5. 写 `libJit.so` 用 `write(16)`；写其它路径用 `0xff01`。
6. 删 `libJit.so` 会卸载 nflasha6，用 `0x4002` 重挂。
7. 读 `/proc/*` 不能用 `get`（size=0），要用 `sh "cat ..."`。
8. busybox 里 `tail/wc/head/tr/awk/find/id/vi` 都**没有 `/bin` 软链**，
   要用写 `busybox tail`；数行数用 `grep -c . <file>`。
9. 相机只有 128MB 内存，别在机上跑大程序、别整读 500MB 到内存。
10. 长读取出错（认证报 NoneType）→ `usb_recover.py`，不要急着重启。

---

## 6. 故障排查

| 现象 | 原因 | 处置 |
|---|---|---|
| `ModuleNotFoundError: usb` | 用了系统 python | 换受管 venv 的 python.exe |
| 认证后设备变成 0x0994 / 0x0D96 | 相机 USB 模式不是「大容量存储」 | 改设置，拔插 |
| 认证报 `NoneType` 无 cmd | 上次读大文件被打断，握手错位 | `python usb_recover.py` |
| `write(16)` 返回 `0x10000000` | 空文件/非法 ELF，dlopen 干净失败 | 检查载荷是否为空 |
| `write(16)` 抛 `GenericUsbException` | 载荷在 dlopen 阶段崩，im.elf 死 | 等 30~40s 相机自动重启；换正确载荷 |
| `sh` 超时、`out.txt` 不生成 | dlopen 命中缓存（本会话已崩过） | 重启相机，别在原地重试 |
| `/tmp/jiritsu` 读返回 130 | nflasha6 被卸载 | `jir 0x4002`（cam.py 已自动） |
| `get /proc/...` 得到 0 字节 | procfs `st_size=0` | 用 `sh "cat /proc/..."` |
| `tail: not found` | busybox 未软链 | `busybox tail` |
| 路径变成 `C:/Program Files/Git/...` | Git Bash 路径转换 | `export MSYS_NO_PATHCONV=1` |
| 命令超时 5s | 驱动默认超时 | `export CAM_USB_TIMEOUT=30000` |
| spawn 后相机从 USB 消失 | 旧载荷没关 fd，子进程继承了 USB 句柄 | 用当前 440B 载荷（已修） |

---

## 7. 数据目录

```
../senser_probe/backup_full/     15 个分区 .bin（1.5GB）+ rootfs/ usr/ log/ n13/ 解包
../senser_probe/evidence/        snapshots/<时间>/  proto/  setting/<时间>/  log/
../senser_probe/.cam_state.json  caps 缓存
```

分区速查（实测）：

| 分区 | 大小 | 用途 |
|---|---|---|
| nflasha1 | 7.9MB | FAT12 未挂载 |
| nflasha2 | 20MB | /setting |
| nflasha3 | 48MB | **/system 固件本体（别碰）** |
| nflasha4 | 12MB | MPR 记录区（活跃，别还原） |
| nflasha5 | 60MB | WBI1 暖启动镜像 |
| nflasha6 | 16MB | /tmp/jiritsu |
| nflasha7 | 8MB | / rootfs ext2ro |
| nflasha10 | 12MB | /tmp + /etc |
| nflasha11 | 500MB | /log |
| nflasha12 | 40MB | /cert |
| nflasha13 | 4MB | 更新历史 |
| nflasha15 | 300MB | /usr ext2ro |
| nflasha16 | 140MB | 全零空白（可当试验田） |
| nflasha17 | 100MB | 空 FAT16 |
| nflasha18 | 20MB | /lens |

---

## 8. 依赖 / 许可 / 入库注意

### 8.1 运行依赖

```bash
pip install pyusb libusb-package
```

认证层依赖 **Sony-PMCA-RE**（ma1co，MIT）。本仓库**不附带**第三方副本，用的时候指路径：

```bash
git clone https://github.com/ma1co/Sony-PMCA-RE
export PMCA_ROOT=/path/to/Sony-PMCA-RE      # 或 a7c2-fl23-research/a7c2_fl23_preview
```

查找顺序：`$PMCA_ROOT` → `../ref_a7c2_fl23/a7c2_fl23_preview` → `./vendor`。
找不到会直接报错并给出提示。

### 8.2 许可

本项目**未附带开源许可**，作者保留所有权利——可以阅读和学习代码，但未授予再分发或复用权。

本项目不对 Sony 的固件与商标主张任何权利，也不包含任何此类内容。
协议事实（pFunc 号、Jiritsu 命令码、帧格式）是独立逆向得出的观察结果。

### 8.3 哪些东西**不能**提交

| 不要入库 | 原因 |
|---|---|
| `../senser_probe/backup_full/` | 1.5GB 索尼固件（vmlinux.bin、av-cam.bin、libJiritsu.so、im.elf）→ 版权 / DMCA |
| `../senser_probe/evidence/` | 设备 dump、`/setting` 备份、协议抓包，可能含机身序列号 |
| 固件更新包 `30110_*.exe/zip` | 索尼版权 |
| 任何密钥 / 认证表 | 逆向出来的 0x0336 密钥表同样属于索尼 |
| `.workbuddy-ai/`、`.codebuddy/`、`.trae/` | AI 助手的工作目录：会话记录、记忆文件、缓存，含本地路径与设备信息 |
| `__pycache__/`、`*.o`、`*.pyc` | 构建垃圾 |

`.gitignore` 已覆盖以上路径。**提交前跑一遍 `git status` 目视确认。**

### 8.4 发布前自检

```bash
grep -rn "D:/\|C:/Users\|senser_probe" --include=*.py .   # 不该有个人绝对路径
git status                                                # 不该有 .bin 大文件 / 备份
du -sh .                                                  # 应在几百 KB 量级
```

---

## 9. 参考与致谢

本项目的实现直接建立在下面这些公开研究之上。除特别说明外，本项目**没有**内联这些代码，
底层发现的功劳都属于原作者。

| 项目 | 作者 | 在本项目里的作用 | 许可 |
|---|---|---|---|
| [ma1co/Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE) | ma1co | USB 认证与传输层。`jiritsu_lib.py` 直接使用它的 `SonySenserAuthDevice`、`SonySenserDevice`、`GenericUsbDriver` | MIT |
| [YuuLuo/a7c2-service-preload](https://github.com/YuuLuo/a7c2-service-preload) | YuuLuo | 指出 Sony 工厂服务（Senser / Jiritsu）的路径穿越与 `libJit.so` 加载链，是整条服务模式路线的起点 | 见仓库 |
| [DavidBuchanan314/ILCE-7M4-RE](https://github.com/DavidBuchanan314/ILCE-7M4-RE) | David Buchanan | bootROM 串口启动协议的参考（48000 8E1、载波脉宽、unlock word、record + CRC32），本项目的 bootROM 后备路线据此实现 | 见仓库 |
| [QAQLeonard/a7c2-fl23-research](https://github.com/QAQLeonard/a7c2-fl23-research) | QAQLeonard | 跑通的 Jiritsu 线格式实现（`transport.py`），同时是 `PMCA_ROOT` 的默认回退路径 | 见仓库 |

如果本项目的内容对你有帮助，引用时也请一并注明上面这些项目——
协议细节不是凭空发现的。
