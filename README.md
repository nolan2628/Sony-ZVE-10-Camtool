# camtool — Sony ZV-E10 service-mode toolkit

**English** | [简体中文](README.zh-CN.md)

> **Disclaimer** — This is a research toolkit for the factory **service mode** (Senser / Jiritsu)
> of a camera **you own**, intended for backups, diagnostics and experiments on your own body.
> It writes raw block devices, it **can brick your camera**, and it comes with **no warranty**.
> Do not run it against someone else's hardware, and do not use it to circumvent copy protection.
> Sony firmware and trademarks remain the property of their owners; this repository contains
> no firmware and no device data. If you find a vulnerability, please also report it to Sony PSIRT.

`cam.py` is the daily driver (one command, one action). The other scripts are its accessories:
full-partition backup/restore, USB recovery, payload building, regression tests.
**Code lives here; data lives in `../senser_probe/`.**

```
<workspace>/
├── Sony-ZVE-10-Camtool/     ← the toolkit (this repo, self-contained)
│   ├── cam.py               main entry point, 40+ subcommands
│   ├── camdata.py           resolves code dir vs data dir (single source of truth)
│   ├── jiritsu_lib.py       transport layer: auth + packet I/O (shared by all scripts)
│   ├── test_cam.py          16-case regression (run it after touching cam.py)
│   ├── usb_recover.py       dev.reset() + re-auth when the handshake desyncs
│   ├── dump_flash.py        read whole partitions → backup_full/
│   ├── restore_flash.py     write whole partitions back (dangerous)
│   ├── verify_dump.py       re-read the device and compare md5 against the backup
│   ├── cleanup_jiritsu.py   whitelist-based cleanup of /tmp/jiritsu
│   ├── verify_ctor.py       minimal dlopen chain check (deploy payload, look for artifact)
│   ├── build_payload_v2.py  ★ the only correct payload builder
│   ├── build_exec_payload.py  thin wrapper around v2 (swap the code blob only)
│   └── payload/             .s sources / raw .bin blobs / .so builds / legacy/
└── senser_probe/            ← data + reverse-engineering notes (NOT in this repo)
    ├── backup_full/         1.5 GB of partition dumps + unpacked rootfs/usr/log
    ├── evidence/            snapshots / proto / setting / log captures
    └── .cam_state.json      cached capability probe results
```

---

## 0. Quick start

```bash
cd Sony-ZVE-10-Camtool
export MSYS_NO_PATHCONV=1        # required on Git Bash for Windows
P=python                         # whichever interpreter has pyusb installed

$P cam.py doctor                 # channel / firmware / backups / payload / write test
$P cam.py info                   # device + partition overview
$P cam.py sh "uname -a"          # remote execution
```

If `doctor` is all green, you are good to go. If any step fails, jump to **Troubleshooting**.

---

## 1. Environment & prerequisites

| Item | Requirement |
|---|---|
| Python | 3.8+, with `pyusb` + `libusb-package` (`pip install -r requirements.txt`). **The wrong interpreter gives `ModuleNotFoundError: usb`** — use a dedicated venv |
| Paths | On Git Bash, path arguments must be prefixed with `MSYS_NO_PATHCONV=1`, otherwise `/tmp/jiritsu` is rewritten to `C:/Program Files/Git/tmp/jiritsu` |
| Camera USB setting | Must be **Mass Storage**, not **Auto**. Otherwise authentication drifts to 0x0994 (HID) / 0x0D96 (MTP) and everything after that fails |
| Connection model | 0x0E8A → re-enumerate → 0x0D95 → 0x0336 second-stage auth (SHA1 + 512-byte key table). Every `cam.py` connection walks this path, ~1–2 s |
| Timeout | 15 s by default (`export CAM_USB_TIMEOUT=30000`). The driver's own default is only 5 s, which always times out on large reads |
| Camera hardware | armv7l, Linux 3.0.27_nl-rt106+ PREEMPT RT, **128 MB RAM total** — do not run memory-hungry things on it |

**One process, one connection.** Every `cam.py` process exit makes the device drop back to
MSC and re-enumerate (a few seconds). `cam.py` retries for up to 20 s, so running several
commands in a row is slow but reliable. For speed use `cam.py shell` (one connection, many commands).

---

## 2. cam.py command reference

Conventions:
- Always use absolute remote paths. `/tmp` is a **persistent vfat** on nflasha10 (not tmpfs) — it survives reboots.
- Every write path is verified by read-back or an explicit response; failures `die()` with a hint.
- Destructive commands require `--confirm`.

### 2.1 Device & backup

| Command | What it does | Notes |
|---|---|---|
| `cam.py info` | Firmware version, GET_VERSION, key files, all partitions | The "is the camera alive" command |
| `cam.py doctor` | 6 checks: channel / firmware / tmmc+libJit / backup integrity / payload / writability | Run after changing tools or switching bodies |
| `cam.py parts [--probe]` | Partition table (built-in measured values); `--probe` reads `/proc/partitions` live | The built-in table is more complete than the truncated procfs |
| `cam.py backup [dev...] [--all]` | Back up partitions into `backup_full/` | Defaults to 4/6/7/15; `--all` is ~1 GB and slow |
| `cam.py restore <dev> --confirm [--force]` | Write a partition back | **The most dangerous command.** Without `--force` it writes only up to the file length; `--force` rewrites the whole device. Always `verify` first |
| `cam.py verify [dev...]` | Re-read the device, compare md5 with local | Static partitions must match; nflasha4 is a live area, two dumps will differ |

### 2.2 Files

| Command | What it does | Notes |
|---|---|---|
| `cam.py cat <remote>` | Print a file to stdout | Don't use on large files — it reads everything into memory |
| `cam.py get <remote> [local]` | Download | Reads by `st_size`; **procfs files report size 0 and come back empty** — use `sh "cat ..."` for `/proc/*` |
| `cam.py put <local> <remote>` | Upload | Goes through FileControl(0xff01), any path |
| `cam.py rm <remote>` | Delete | **Deleting `/tmp/jiritsu/libJit.so` makes Jiritsu unmount nflasha6** (reads then return 130, data is intact). `jir 0x4002` — or any cam.py command — remounts it automatically |
| `cam.py hex <remote> [off] [len]` | Hex view | Files >8 MB switch to `dd` seek, no full read |
| `cam.py slice <dev> <off> <len> [local]` | Extract a range from a device | Avoids downloading 500 MB to look at 1 KB |
| `cam.py pull <rdir> <ldir>` | Download a directory (tar) | An order of magnitude faster than per-file |
| `cam.py push <ldir> <rdir>` | Upload a directory | Note the camera's busybox tar has no long options |

### 2.3 Execution

| Command | What it does | Notes |
|---|---|---|
| `cam.py sh [-t sec] "<cmd>"` | Remote exec, returns stdout + exit code | 25 s default timeout. **Chain commands with `;`** |
| `cam.py shf <local_script>` | Run a local script remotely | First line must be `#!/bin/sh` |
| `cam.py shell` | Interactive shell reusing one connection | Device re-enumerates on exit, as usual |
| `cam.py run <local_bin> [args]` | Upload to `/log/camtool/tool` and execute | Must be an ARM binary (static is safest) |
| `cam.py spawn "<cmd>" [log]` | Start a background daemon, return the PID immediately | See "spawn" below |
| `cam.py kill <pid\|name> [--confirm]` | Kill a process | Killing a daemon causes one momentary "channel lost"; cam.py reconnects — that's normal |
| `cam.py reboot [reboot\|poweroff] --confirm` | Reboot / power off | USB will drop |
| `cam.py watch "<cmd>" [interval] [count]` | Periodic execution | For watching values change |

**spawn** is implemented as "write `daemon.sh` → `sh daemon.sh &`". This box has no
`setsid`/`nohup`, and `command -v` doesn't exist (no such shell builtin). The payload
`close(3..255)` in the **child** after `fork`, so the daemon's fd table holds only 0/1/2 —
it **never inherits im.elf's USB/video handles**. That was the key fix on 2026-10-18;
older builds made the camera disappear from USB right after `spawn`.

### 2.4 Queries

| Command | What it does | Notes |
|---|---|---|
| `cam.py ls [-l] [path]` | List a directory | Only `-l` shows time/size |
| `cam.py lsr [root] [filter]` | Recursive listing | busybox has **no find**; this replaces it. 2000-entry cap |
| `cam.py stat <path>` | File info | |
| `cam.py df` | Space | busybox has **no df**; prints partition sizes + mount point/type/rw instead |
| `cam.py ps` / `cam.py mem` | Processes / memory | Only 128 MB total; `MemFree` hovering at 3–4 MB is normal |
| `cam.py dmesg [lines] [filter]` | Kernel log tail | First stop for USB / liro / jiritsu issues |
| `cam.py irq [filter]` | Interrupt table | Check whether `liro-*` and `dma330` are actually running |
| `cam.py log [--pull]` | /log partition | `--pull` downloads it |
| `cam.py snapshot` | 12-item diagnostic snapshot → `evidence/snapshots/<time>/` | Take one before and after a change, then `snapdiff` |
| `cam.py snapdiff [A] [B]` | Diff two snapshots | No args = the two most recent |

### 2.5 Analysis

| Command | What it does | Notes |
|---|---|---|
| `cam.py diff <A> <B>` | Compare two remote files | Reads both fully; `/setting/Backup.bin` at 1.2 MB is fine |
| `cam.py tail <remote> [bytes]` | Tail a file | Uses `dd` seek for large files |
| `cam.py grep <pattern> <remote> [-i]` | Remote search | No full download |
| `cam.py md5 <remote>` | Remote md5 | Uses `md5sum` when present, otherwise downloads and hashes locally |

### 2.6 System

| Command | What it does | Notes |
|---|---|---|
| `cam.py caps [--refresh]` | Probe available commands, cache to `.cam_state.json` | **Discount the result**: `busybox --list` lists applets compiled into busybox, which does not mean they are symlinked into PATH |
| `cam.py dev [filter]` | /proc/devices + /proc/misc + /dev | |
| `cam.py modinfo <module>` | refcnt / holders / parameters | `modinfo liro` shows driver parameter entry points |
| `cam.py sysfs <path> [value] [--confirm]` | Read/write sysfs | **Writing requires `--confirm`**; `/sys/power/image_drop` is the warm-boot image switch, unknown risk |
| `cam.py setting [--backup]` | Inspect /setting; `--backup` downloads 5 files | |

### 2.7 Mounting

| Command | What it does | Notes |
|---|---|---|
| `cam.py mount <dev> [mountpoint]` | Auto-detect fs type and mount | Defaults to `/tmp/mnt_<dev>`; mounting `/system` or `/setting` may fight with the imaging process |
| `cam.py umount <mountpoint>` | Unmount | |
| `cam.py sd` | SD card status | Confirms whether the card is present when mmca10 is unmounted |

### 2.8 Protocol level (for RE work)

| Command | What it does | Notes |
|---|---|---|
| `cam.py raw <pfunc> [hex] [--save] [--full]` | Arbitrary Senser packet | `--save` drops it into `evidence/proto/`; `--full` prints the whole response |
| `cam.py cmd <block> <code> [hex]` | AdjustControl command | Entry point for the color-matrix work (block/code TBD) |
| `cam.py jir <code> [hex]` | Jiritsu command | Common: `0x400d` GET_VERSION, `0x4002` MOUNT |
| `cam.py pf` | pFunc / command-code cheat sheet | No need to dig through notes |

### 2.9 Maintenance

| Command | What it does | Notes |
|---|---|---|
| `cam.py reset` | USB hard reset | Already built into the transport; this is the manual fallback |
| `cam.py wait [sec]` | Wait for the device and report status | Cleaner than polling `info` after replugging or power-cycling |

---

## 3. The other scripts

### 3.1 `dump_flash.py` — whole-partition reads

```bash
python dump_flash.py                  # default 4/6/7/15
python dump_flash.py nflasha7 nflasha6
python dump_flash.py --all            # every known partition (~1 GB)
python dump_flash.py --mbr            # first 8 MB of /dev/nflasha (partition table / boot area)
```

Writes to `../senser_probe/backup_full/<dev>.bin` and prints md5 plus a filesystem guess.
**Note**: nflasha11 (500 MB) takes ~47 s; interrupting it desyncs the handshake — recover with `usb_recover.py`.

### 3.2 `restore_flash.py` — whole-partition writes (dangerous)

```bash
python restore_flash.py nflasha13              # preview
python restore_flash.py nflasha13 --confirm    # write up to file length only
python restore_flash.py nflasha13 --confirm --force   # full rewrite (zero-padded)
```

**Note**: this writes raw block bytes; the wrong partition means a brick. Run
`verify_dump.py` first to confirm the local file's md5 matches the device (proves the
backup is good and the partition isn't changing). `nflasha4` is a live rolling area — **never restore it**.

### 3.3 `verify_dump.py` — backup verification

```bash
python verify_dump.py            # verify static partitions, write manifest_static.txt
python verify_dump.py nflasha6
```

### 3.4 `cleanup_jiritsu.py` — whitelist cleanup of /tmp/jiritsu

```bash
python cleanup_jiritsu.py          # preview
python cleanup_jiritsu.py --confirm
```

Only removes our own artifacts (`libJit.so`, `run.sh`, `out.txt`, `ZZZ_*`, `*.log`) and
keeps firmware files such as `TMMC.JIT`, `DIAC.csv`, `jit.log`, `history.txt`.

### 3.5 `usb_recover.py` — recover from a desynced handshake

```bash
python usb_recover.py
```

After an interrupted large read, `authenticate` may fail with `NoneType` (no cmd).
This does `dev.reset()` + re-auth — **no battery pull needed**. Power-cycle only if that fails.

### 3.6 `verify_ctor.py` — minimal dlopen chain check

```bash
python verify_ctor.py                                  # defaults to libJit_ctor_v2.so
python verify_ctor.py payload/libJit_arm_ok.so /tmp/jiritsu/ARM_OK.txt
```

Pass criteria: `write(16) -> 0x0` and the marker file appears.
`write(16)` raising `GenericUsbException` means the payload crashed during dlopen:
im.elf dies and **the camera reboots itself** (~30–40 s). Wait 60 s and run `cam.py wait`.
(A true power cycle is only needed if it hasn't come back in 3 minutes — and you must
**unplug the USB cable first**, because the ZV-E10 can be bus-powered.)

### 3.7 `build_payload_v2.py` — ★ the only correct payload builder

```bash
python build_payload_v2.py <source.bin> <out.so> [pad_to_bytes] [thumb|arm]
python build_payload_v2.py payload/jit_exec_v11.bin payload/libJit_exec.so
python build_payload_v2.py payload/jit_arm_ok.bin payload/libJit_arm_ok.so 0 arm
```

The ELF layout it produces (paid for in blood — don't change it):

```
ph0  PT_LOAD  R+X   : 0 .. code_end        (ELF header + hash/sym/str + code)
ph1  PT_LOAD  R+W   : code_end .. +0x38    (.dynamic, vaddr offset by one page)
ph2  PT_DYNAMIC     : points at ph1
DT_INIT = ctor_vaddr | (thumb ? 1 : 0)
e_flags = 0x05000200
```

**Why it has to be this way**:
- If `.dynamic` lands in a read-only segment, glibc's `dlopen` writes `l_info`/`DT_DEBUG`
  into it → **SIGSEGV**, im.elf dies on the spot and the camera vanishes from USB.
  The 528-byte `libJit.so` from 2026-10-17 had exactly this bug and never worked.
- The `DT_INIT` Thumb bit must match the code mode: `.thumb` source → `|1`, `.arm` source → `|0`.
  Get it backwards and the CPU decodes in the wrong mode and crashes immediately.
- Use `DT_INIT`, not `DT_INIT_ARRAY`.

### 3.8 `build_exec_payload.py` — thin wrapper

```bash
python build_exec_payload.py                       # jit_exec_v11.bin -> libJit_exec.so
python build_exec_payload.py <src.bin> <out.so> [arm|thumb]
```

Just calls `build_payload_v2.py` (older versions carried a broken skeleton of their own; deprecated).

### 3.9 `test_cam.py` — 16-case regression

```bash
python test_cam.py          # all
python test_cam.py df       # only cases whose name contains "df"
```

Runs md5/hex/grep/tail/lsr/irq/dev/sysfs/diff/setting/jir/raw/ps/mem/sd/df over a single
connection. Run it after every `cam.py` change; current baseline is **16/16 passing**.

### 3.10 `camdata.py` — data directory resolution

Code lives in this repo, data lives in `../senser_probe/` (or `$CAM_DATA_ROOT`).
To relocate: `export CAM_DATA_ROOT=/some/dir`. Every script takes `BACKUP_DIR` /
`EVIDENCE_DIR` from here.

### 3.11 `jiritsu_lib.py` — transport layer

```python
from jiritsu_lib import connect
j = connect()                 # authenticate, return a Jiritsu instance
j.jiritsu(0x400d)             # send a Jiritsu command
j.file_read('/dev/nflasha6')  # FileControl read
j.file_write(path, blob)      # FileControl write
j.select(path)                # select a path (required before writing libJit.so)
j.stop()
```

- `pkt(..., retries=2)` retries twice by default; **pass `retries=0` for destructive
  commands (FORMAT/FIRMUP)**.
- Timeout via `CAM_USB_TIMEOUT` (milliseconds), default 15000.

---

## 4. Payloads (where the landmines are)

### 4.1 What's in the box

| File | Size | Purpose |
|---|---|---|
| `payload/libJit_exec.so` | 440 B | **Production payload.** fork → child closes fd 3..255 → `execve("/bin/sh", run.sh)`, output goes to `out.txt` |
| `payload/libJit_arm_ok.so` | 340 B | ARM-mode verification payload, writes `/tmp/jiritsu/ARM_OK.txt` |
| `payload/legacy/libJit_v9.so` | 1820 B | The first payload that actually ran (layout reference) |
| `payload/legacy/libJit.so` | 528 B | **Broken — never deploy** (`.dynamic` in a read-only segment) |
| `payload/legacy/libJit_ctor_v2.so` | 548 B | Legacy ctor rebuilt with v2. **The ctor does run** (it produces `RCE_MARK.txt` + `ver.txt`, confirming `Uid: 0 0 0 0` and `CapEff: ffffffffffffffff`) but **crashes on return** — it `read`s 1024 B straight onto the thread stack and smashes the frame. Archived only, do not deploy |

### 4.2 Assembler toolchain

There is no global `arm-none-eabi-*` here; use the one bundled with Arduino:

```bash
# Windows default location; the version suffix may differ
export PATH="$HOME/AppData/Local/Arduino15/packages/arduino/tools/arm-none-eabi-gcc/7-2017q4/bin:$PATH"
cd payload
arm-none-eabi-as -o jit_exec_v11.o jit_exec_v11.s
arm-none-eabi-objcopy -O binary -j .text jit_exec_v11.o jit_exec_v11.bin
cd .. && python build_exec_payload.py payload/jit_exec_v11.bin payload/libJit_exec.so
```

### 4.3 Deployment rules

- **Always go through Jiritsu `write(16)`** (cam.py does this internally). Writing the same
  path via FileControl `0xff01` **does not trigger dlopen**.
- dlopen caches by name: once a ctor has crashed, every later write in the same session hits
  the cache and the ctor never runs again. Only a real restart of im.elf clears it (in
  practice im.elf's death makes the camera reboot itself, 30–40 s).
- Before swapping in a new ctor, run `verify_ctor.py` with a known-good payload to confirm the chain.

---

## 5. Non-negotiable rules

1. **Until you have a working bootROM recovery, do not touch `nflasha3`** (/system, contains av-cam.bin).
2. Changing `/usr` or `/` requires "dump whole partition → edit locally → restore whole partition".
   Never write partial data into a mounted ext2 filesystem (page-cache mismatch → ext2 errors).
3. `FORMAT(0x4003)` / `FIRMUP(0x4006)` will hang the service. **Do not send them.**
4. "Really powered off" means: the ZV-E10 can be USB bus-powered, so **unplug USB first**,
   then switch off / pull the battery.
5. Write `libJit.so` with `write(16)`; write everything else with `0xff01`.
6. Deleting `libJit.so` unmounts nflasha6 — remount with `0x4002`.
7. Reading `/proc/*` cannot use `get` (size 0) — use `sh "cat ..."`.
8. `tail/wc/head/tr/awk/find/id/vi` have **no `/bin` symlinks** in busybox; call `busybox tail`
   etc. Count lines with `grep -c . <file>`.
9. The camera has 128 MB of RAM — no big programs on it, no pulling 500 MB into memory.
10. A failed long read (auth reports `NoneType`) → `usb_recover.py`, don't rush to reboot.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: usb` | Wrong interpreter | Use the venv that has pyusb |
| Device shows up as 0x0994 / 0x0D96 after auth | Camera USB mode isn't Mass Storage | Change the setting, replug |
| Auth fails with `NoneType` / no cmd | Interrupted large read desynced the handshake | `python usb_recover.py` |
| `write(16)` returns `0x10000000` | Empty file / invalid ELF — dlopen failed cleanly | Check the payload isn't empty |
| `write(16)` raises `GenericUsbException` | Payload crashed in dlopen, im.elf died | Wait 30–40 s for the auto-reboot; use a correct payload |
| `sh` times out, no `out.txt` | dlopen cache hit (already crashed this session) | Restart the camera; don't retry in place |
| `/tmp/jiritsu` reads return 130 | nflasha6 unmounted | `jir 0x4002` (cam.py does this automatically) |
| `get /proc/...` returns 0 bytes | procfs `st_size=0` | Use `sh "cat /proc/..."` |
| `tail: not found` | busybox applet not symlinked | `busybox tail` |
| Path becomes `C:/Program Files/Git/...` | Git Bash path conversion | `export MSYS_NO_PATHCONV=1` |
| Commands time out at 5 s | Driver default timeout | `export CAM_USB_TIMEOUT=30000` |
| Camera disappears from USB after `spawn` | Old payload didn't close fds; the child inherited USB handles | Use the current 440 B payload (fixed) |

---

## 7. Data directory

```
../senser_probe/backup_full/     15 partition .bin files (1.5 GB) + unpacked rootfs/usr/log
../senser_probe/evidence/        snapshots/<time>/  proto/  setting/<time>/  log/
../senser_probe/.cam_state.json  cached caps probe
```

Measured partition map:

| Partition | Size | Purpose |
|---|---|---|
| nflasha1 | 7.9 MB | FAT12, unmounted |
| nflasha2 | 20 MB | /setting |
| nflasha3 | 48 MB | **/system — firmware itself (hands off)** |
| nflasha4 | 12 MB | MPR record area (live, never restore) |
| nflasha5 | 60 MB | WBI1 warm-boot image |
| nflasha6 | 16 MB | /tmp/jiritsu |
| nflasha7 | 8 MB | / rootfs, ext2 ro |
| nflasha10 | 12 MB | /tmp + /etc |
| nflasha11 | 500 MB | /log |
| nflasha12 | 40 MB | /cert |
| nflasha13 | 4 MB | update history |
| nflasha15 | 300 MB | /usr, ext2 ro |
| nflasha16 | 140 MB | all-zero blank area (good scratch space) |
| nflasha17 | 100 MB | empty FAT16 |
| nflasha18 | 20 MB | /lens |

---

## 8. Dependencies / license / what not to commit

### 8.1 Runtime dependencies

```bash
pip install pyusb libusb-package
```

The auth layer depends on **Sony-PMCA-RE** (ma1co, MIT). This repo does **not** vendor it;
point at your checkout instead:

```bash
git clone https://github.com/ma1co/Sony-PMCA-RE
export PMCA_ROOT=/path/to/Sony-PMCA-RE      # or a7c2-fl23-research/a7c2_fl23_preview
```

Lookup order: `$PMCA_ROOT` → `../ref_a7c2_fl23/a7c2_fl23_preview` → `./vendor`.
If none is found the import fails with an explanatory message.

### 8.2 License

No open-source license is attached. All rights reserved by the author — you may read and
study the code, but redistribution or reuse is not granted.

This project claims no rights over Sony firmware or trademarks, and contains none of it.
The protocol facts (pFunc numbers, Jiritsu command codes, frame layout) are independently
reverse-engineered observations.

### 8.3 What must **not** be committed

| Keep out | Why |
|---|---|
| `../senser_probe/backup_full/` | 1.5 GB of Sony firmware (vmlinux.bin, av-cam.bin, libJiritsu.so, im.elf) → copyright / DMCA |
| `../senser_probe/evidence/` | Device dumps, `/setting` backups, protocol captures — may contain body serial numbers |
| Firmware update packages `30110_*.exe/zip` | Sony copyright |
| Any keys / auth tables | The reverse-engineered 0x0336 key table is Sony's too |
| `.workbuddy-ai/`, `.codebuddy/`, `.trae/` | AI assistant working dirs: session logs, memory files, caches — they leak local paths and device details |
| `__pycache__/`, `*.o`, `*.pyc` | Build junk |

`.gitignore` covers all of the above. **Run `git status` and eyeball it before committing.**

### 8.4 Pre-publish checklist

```bash
grep -rn "D:/\|C:/Users\|senser_probe" --include=*.py .   # no personal absolute paths
git status                                                # no large .bin / backups
du -sh .                                                  # should be a few hundred KB
```
