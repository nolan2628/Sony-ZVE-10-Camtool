#!/usr/bin/env python3
"""
cam.py - ZV-E10 调试工具集

用法:
  [设备与备份]
  cam.py info                      设备信息（固件版本、分区、挂载）
  cam.py doctor                    自检（连通性、备份完整性、载荷就绪）
  cam.py parts [--probe]           分区表
  cam.py backup [dev...] [--all]   备份分区
  cam.py restore <dev> [--confirm] [--force]   还原分区
  cam.py verify [dev...]           重读校验备份

  [文件]
  cam.py cat <remote>              读远程文件
  cam.py get <remote> [local]      下载
  cam.py put <local> <remote>      上传
  cam.py rm <remote>               删除
  cam.py hex <remote> [off] [len]  十六进制查看
  cam.py slice <dev> <off> <len> [local]   只取设备一段
  cam.py pull <remote_dir> <local_dir>     整目录下载（tar）
  cam.py push <local_dir> <remote_dir>     整目录上传（tar）

  [执行]
  cam.py sh <command...>           远程执行
  cam.py shf <local_script>        执行本地脚本
  cam.py run <local_bin> [args]    上传并执行 ARM 可执行文件
  cam.py shell                     交互式 shell（复用一条连接）
  cam.py spawn "<cmd>" [log]       后台常驻启动，立即返回
  cam.py kill <pid|名字> [--confirm]  杀进程
  cam.py reboot [reboot|poweroff] --confirm  重启/关机
  cam.py watch "<cmd>" [间隔] [次数]        周期执行

  [查询]
  cam.py ls [-l] [path]            列目录
  cam.py lsr [root] [name过滤]      递归列目录（替代 find）
  cam.py stat <path>               文件信息
  cam.py df                        空间（无 df applet 时回退分区表）
  cam.py ps                        进程
  cam.py mem                       内存
  cam.py dmesg [行数] [过滤词]      内核日志尾部
  cam.py irq [过滤词]               中断表
  cam.py log [--pull]              /log 分区
  cam.py snapshot                  采集诊断快照到 evidence/snapshots/
  cam.py snapdiff [A] [B]          对比两份快照

  [分析]
  cam.py diff <remoteA> <remoteB>  比较两个远端文件
  cam.py tail <remote> [字节数]     看文件尾部（大文件用 dd 定位）
  cam.py grep <模式> <remote>       远端搜索 [-i]
  cam.py md5 <remote>              远端 md5

  [系统]
  cam.py caps [--refresh]          探测 busybox 支持哪些 applet（缓存）
  cam.py dev [过滤词]               设备节点（/proc/devices + /proc/misc + /dev）
  cam.py modinfo <模块名>           模块 refcnt/holders/parameters
  cam.py sysfs <路径> [值]         读 sysfs；写需 --confirm
  cam.py setting [--backup]        查看/备份 /setting（Backup.bin）

  [挂载]
  cam.py mount <dev> [挂载点]      自动识别 fs 类型并挂载
  cam.py umount <挂载点>
  cam.py sd                        SD 卡状态

  [协议级（逆向用）]
  cam.py raw <pfunc> [hexdata]     任意 Senser 包 [--save] [--full]
  cam.py cmd <block> <code> [hex]  AdjustControl 命令 [--save] [--full]
  cam.py jir <code> [hex]          Jiritsu 命令 [--save] [--full]
  cam.py pf                        已知 pFunc / 命令码速查

  [维护]
  cam.py reset                     USB 硬复位（连接层已内置，手动兜底）
  cam.py wait [秒]                 等设备出现并报告状态

示例:
  cam.py sh "uname -a"
  cam.py shell
  cam.py caps --refresh
  cam.py dev liro
  cam.py modinfo liro
  cam.py dmesg 40 usb
  cam.py diff /setting/Backup.bin /setting/Backup.bak
  cam.py cmd 0x603 0x1 --save
  cam.py slice nflasha4 0x1ec00 0x400 ./mp.txt
  cam.py mount nflasha18
  cam.py get /version.txt ./ver.txt
"""
import hashlib
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from jiritsu_lib import connect  # noqa: E402
from camdata import BACKUP_DIR, DATA_ROOT, EVIDENCE_DIR, PAYLOAD_DIR  # noqa: E402

PAYLOAD = os.path.join(PAYLOAD_DIR, 'libJit_exec.so')

REMOTE_DIR = '/tmp/jiritsu'
R_RUN = f'{REMOTE_DIR}/run.sh'
R_OUT = f'{REMOTE_DIR}/out.txt'
R_LIB = f'{REMOTE_DIR}/libJit.so'

SENTINEL = '__CAM_DONE__'

# 实测分区表（大小单位 B）
PARTS = {
    'nflasha1':  (8323072,   'FAT12 未挂载'),
    'nflasha2':  (20971520,  '/setting'),
    'nflasha3':  (50331648,  '/system 固件本体'),
    'nflasha4':  (12582912,  'MPR Flash Backup'),
    'nflasha5':  (62914560,  'WBI1 暖启动'),
    'nflasha6':  (16777216,  '/tmp/jiritsu'),
    'nflasha7':  (8388608,   '/ rootfs ext2'),
    'nflasha10': (12582912,  '/tmp + /etc'),
    'nflasha11': (524288000, '/log'),
    'nflasha12': (41943040,  '/cert'),
    'nflasha13': (4194304,   '更新历史'),
    'nflasha15': (314572800, '/usr ext2'),
    'nflasha16': (146800640, '全零空白区'),
    'nflasha17': (104857600, '空 FAT16'),
    'nflasha18': (20971520,  '/lens'),
    'nflasha23': (67108864,  '未挂载（内容非空，用途待查）'),
    'nflashaB0': (2097152,   '未挂载（"EXBL" 头）'),
    'nflashaB1': (2097152,   '未挂载（全零）'),
}

# 禁止整盘还原的分区
RESTORE_BLOCKED = {'nflasha5'}

_conn = None


def _hard_recover():
    """USB 硬复位（等价于 usb_recover.py，不用拔电池）"""
    from jiritsu_lib import reset_devices
    return reset_devices(verbose=False)


def C():
    global _conn
    if _conn is None:
        import atexit
        from jiritsu_lib import connect_hardened
        # 上一条命令退出后服务会停止、设备重新枚举回 0x0D95，需要等几秒。
        # connect_hardened 内部已含：等枚举 → MSC auth → 等 0x0336 →
        # 服务 auth → 读 /version.txt 验证 → 失败则 USB reset 重试。
        print('（连接相机…）', file=sys.stderr)
        _conn = connect_hardened(attempts=4, timeout=60, verbose=False)
        if _conn is None:
            import jiritsu_lib as jl
            die(f'连接失败: {jl.LAST_ERROR or "未知原因"}\n'
                '  提示: 若提示 USB 栈挂死，拔插一次 USB 线即可；'
                '若是驱动问题，用 Zadig 给对应 PID 装 WinUSB/libusbK。')
        atexit.register(_release)
    return _conn


def _reconnect(attempts=3, timeout=45):
    """通道中途死掉时重连。输出文件在 /tmp/jiritsu（持久分区），重连后可继续读。"""
    global _conn
    from jiritsu_lib import connect_hardened
    print('（通道中断，重连中…）', file=sys.stderr)
    try:
        if _conn is not None:
            _conn.stop()
    except Exception:
        pass
    _conn = None
    _conn = connect_hardened(attempts=attempts, timeout=timeout, verbose=False)
    return _conn is not None


def _release():
    """进程退出时释放 USB，否则下次连接会握手失败"""
    global _conn
    if _conn is not None:
        try:
            _conn.stop()
        except Exception:
            pass
        _conn = None


def ensure_mount():
    """确保 /tmp/jiritsu 已挂载（nflasha6）。

    删除 libJit.so 会触发 Jiritsu 的崩溃恢复，导致它把分区卸掉。
    此时路径读取返回 130，但分区数据完好 —— 发 JIRITSU_MOUNT(0x4002) 即可恢复。
    """
    j = C()
    resp, _ = j.file_read('/tmp/jiritsu/tmmc.jit')
    if resp == 1:
        return True
    st, _ = j.jiritsu(0x4002)
    time.sleep(0.6)
    resp, _ = j.file_read('/tmp/jiritsu/tmmc.jit')
    if resp == 1:
        print('（已自动重新挂载 /tmp/jiritsu）')
        return True
    print(f'警告: /tmp/jiritsu 未挂载，MOUNT 返回 '
          f'{("0x%08x" % st) if isinstance(st, int) else st}')
    return False


def die(msg, code=1):
    print(f'错误: {msg}')
    sys.exit(code)


def md5(b):
    return hashlib.md5(b).hexdigest()


def human(n):
    return f'{n/1048576:.1f}MB' if n >= 1048576 else f'{n/1024:.0f}KB'


# ---------------- 本地能力缓存 ----------------
# 相机 busybox 被裁剪过，applet 是否存在不该靠猜。caps 探测一次，
# 结果写进 .cam_state.json，后续命令（如 df）直接读缓存，不再浪费往返。

STATE_FILE = os.path.join(DATA_ROOT, '.cam_state.json')


def _load_state():
    try:
        import json
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st):
    import json
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(st, f, indent=1, ensure_ascii=False)
    except Exception:
        pass


# ---------------- 基础 IO ----------------

def read(path):
    resp, data = C().file_read(path)
    return resp, data


def write(path, data):
    return C().file_write(path, data)


def read_or_die(path):
    resp, data = read(path)
    if resp != 1:
        die(f'读取失败 {path} (resp={resp})')
    return data


# ---------------- 子命令 ----------------

def cmd_info():
    j = C()
    print('== 设备 ==')
    ver = read('/version.txt')
    if ver[0] == 1:
        print(f'  固件版本: {ver[1].decode("latin1").strip()}')
    else:
        print('  固件版本: (读不到 /version.txt)')

    st, _ = j.jiritsu(0x400d)
    print(f'  Jiritsu:  {"0x%08x" % st if isinstance(st, int) else st}')

    print('\n== 关键文件 ==')
    ensure_mount()
    for p, note in [('/tmp/jiritsu/tmmc.jit', 'Jiritsu 运行时'),
                    ('/tmp/jiritsu/libJit.so', 'JIT 库（载荷落点）'),
                    ('/usr/bin/im.elf', '影像主进程'),
                    ('/usr/lib/libJiritsu.so', '工厂服务模块')]:
        resp, d = read(p)
        state = f'{human(len(d))}' if resp == 1 else '不存在'
        print(f'  {p:32s} {state:10s} {note}')

    print('\n== 分区 ==')
    total = 0
    for dev, (size, note) in PARTS.items():
        print(f'  {dev:12s} {human(size):>9s}  {note}')
        total += size
    print(f'  {"合计":12s} {human(total):>9s}')


def cmd_parts(probe=False):
    if not probe:
        for dev, (size, note) in PARTS.items():
            print(f'{dev:12s} {size:>12d} {human(size):>9s}  {note}')
        return
    print(f'{"dev":12s} {"size(B)":>12s} {"MB":>8s}  head')
    for dev in PARTS:
        resp, d = read(f'/dev/{dev}')
        if resp != 1:
            print(f'{dev:12s} 不可读 resp={resp}')
            continue
        print(f'{dev:12s} {len(d):>12d} {len(d)/1048576:>8.1f}  {d[:8].hex()}')


def cmd_backup(devs, all_=False):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    targets = list(PARTS) if all_ else (devs or ['nflasha4', 'nflasha6', 'nflasha7', 'nflasha15'])
    print(f'备份 {len(targets)} 个分区 -> {BACKUP_DIR}')
    for dev in targets:
        if dev not in PARTS:
            print(f'  ? {dev} 不在已知分区表，跳过')
            continue
        t0 = time.time()
        resp, d = read(f'/dev/{dev}')
        if resp != 1 or not d:
            print(f'  X {dev}: resp={resp}')
            continue
        out = os.path.join(BACKUP_DIR, f'{dev}.bin')
        open(out, 'wb').write(d)
        print(f'  OK {dev:12s} {len(d):>11d}B {human(len(d)):>9s} '
              f'{time.time()-t0:5.1f}s  md5={md5(d)}')


def cmd_restore(dev, confirm=False, force=False):
    if dev in RESTORE_BLOCKED:
        die(f'{dev} 在禁止列表内（暖启动镜像，写坏可能影响开机）')
    local = os.path.join(BACKUP_DIR, f'{dev}.bin')
    if not os.path.exists(local):
        die(f'本地备份不存在: {local}')
    blob = open(local, 'rb').read()
    print(f'本地备份 {len(blob)}B  md5={md5(blob)}')

    resp, cur = read(f'/dev/{dev}')
    if resp != 1:
        die(f'读取设备失败 resp={resp}')
    print(f'设备当前 {len(cur)}B  md5={md5(cur)}')
    if len(cur) != len(blob):
        die('大小不一致，中止（避免写坏）')

    if cur == blob:
        if not force:
            print('内容已一致，无需还原（加 --force 可强制重写）')
            return
        print('内容一致，--force 强制重写')
    else:
        diffs = sum(1 for a, b in zip(cur, blob) if a != b)
        print(f'差异 {diffs}B')

    if not confirm:
        print('预览模式，加 --confirm 执行写入')
        return

    print(f'写入 {len(blob)}B ...')
    t0 = time.time()
    resp, _ = write(f'/dev/{dev}', blob)
    print(f'  resp={resp} ({time.time()-t0:.1f}s)')
    if resp != 1:
        die('写入失败')
    resp, back = read(f'/dev/{dev}')
    ok = resp == 1 and back == blob
    print(f'  回读 md5={md5(back)}  一致: {ok}')
    if not ok:
        die('还原后校验失败，请勿断电，重跑本命令', 2)


def cmd_verify(devs):
    targets = devs or [d for d in PARTS if os.path.exists(os.path.join(BACKUP_DIR, f'{d}.bin'))]
    print(f'校验 {len(targets)} 个分区')
    bad = 0
    for dev in targets:
        local = os.path.join(BACKUP_DIR, f'{dev}.bin')
        if not os.path.exists(local):
            print(f'  - {dev:12s} 本地无备份')
            continue
        old = open(local, 'rb').read()
        t0 = time.time()
        resp, new = read(f'/dev/{dev}')
        if resp != 1:
            print(f'  X {dev:12s} resp={resp}')
            bad += 1
            continue
        if old == new:
            print(f'  OK {dev:12s} {human(len(old)):>9s} 一致  [{time.time()-t0:.1f}s]')
        else:
            diff = sum(1 for a, b in zip(old, new) if a != b) if len(old) == len(new) else -1
            print(f'  ! {dev:12s} 不一致（差异 {diff}B）')
            bad += 1
    return bad


def cmd_cat(path):
    resp, d = read(path)
    if resp != 1:
        die(f'读取失败 resp={resp}')
    sys.stdout.buffer.write(d)


def cmd_get(remote, local=None):
    resp, d = read(remote)
    if resp != 1:
        die(f'读取失败 resp={resp}')
    local = local or os.path.basename(remote)
    open(local, 'wb').write(d)
    print(f'{remote} -> {local}  {len(d)}B  md5={md5(d)}')


def cmd_put(local, remote):
    if not os.path.exists(local):
        die(f'本地文件不存在: {local}')
    blob = open(local, 'rb').read()
    t0 = time.time()
    resp, _ = write(remote, blob)
    print(f'{local} -> {remote}  {len(blob)}B  resp={resp}  ({time.time()-t0:.1f}s)')
    if resp != 1:
        die('写入失败')


def cmd_rm(remote):
    st, _ = C().delete_file(remote)
    print(f'删除 {remote}: status={"0x%08x" % st if isinstance(st, int) else st}')


# ---------------- 远程执行 ----------------

def deploy_payload(pad=0):
    """写入执行载荷。pad 用于改变文件大小，避免加载器缓存。

    注意: 必须走 Jiritsu write(16)（adj_cmd_set_file2），
    它会 open+write 并在路径等于 /tmp/jiritsu/libJit.so 时 dlopen。
    用 0xff01 FileControl 写同一路径不会触发加载。
    """
    if not os.path.exists(PAYLOAD):
        die(f'载荷不存在: {PAYLOAD}（用 build_exec_payload.py 重新构建）')
    ensure_mount()
    blob = open(PAYLOAD, 'rb').read() + b'\x00' * pad
    j = C()
    j.select(R_LIB)
    st, _ = j.jiritsu(16, b'\0\xff\0' + blob)
    return st, len(blob)


def sh(cmd, timeout=25.0):
    """远程执行一条命令，返回 (exit_code, output)"""
    C()
    ensure_mount()

    script = '#!/bin/sh\n' + cmd + f'\necho {SENTINEL}$?\n'

    # 清掉上次输出，避免读到旧内容
    try:
        C().delete_file(R_OUT)
    except Exception:
        pass

    write(R_RUN, script.encode())

    st, size = deploy_payload(0)
    if not (isinstance(st, int) and st == 0):
        return -1, f'载荷写入失败 status={("0x%08x" % st) if isinstance(st, int) else st}'

    # dlopen + fork + sh 需要时间；立刻读会把 USB 管道打停
    time.sleep(0.9)

    t0 = time.time()
    tried = 0
    errors = 0
    while time.time() - t0 < timeout:
        try:
            resp, d = read(R_OUT)
            errors = 0
        except Exception:
            # 瞬时 USB 错误：先等；连续失败则重连（out.txt 在持久分区，重连后可继续读）
            errors += 1
            if errors == 2 and _reconnect():
                errors = 0
                continue
            if errors >= 4:
                return -3, 'USB 通道中断（读 out.txt 连续失败，重连也没救回来）'
            time.sleep(0.6)
            continue

        if resp == 1 and d and SENTINEL.encode() in d:
            text = d.decode('utf-8', 'replace')
            idx = text.rfind(SENTINEL)
            body = text[:idx]
            tail = text[idx + len(SENTINEL):].strip()
            try:
                code = int(tail.split('\n')[0])
            except Exception:
                code = 0
            return code, body

        # 3 秒还没动静 -> 换一个尺寸重试一次（破加载器缓存）
        if time.time() - t0 > 3.0 and tried < 2:
            tried += 1
            deploy_payload(pad=tried * 4)
            time.sleep(0.9)
        time.sleep(0.5)

    return -2, (f'超时 {timeout}s，未收到完成标记。\n'
                '  可能原因：① 载荷构造函数没执行（dlopen 命中缓存，'
                '本轮已加载过同名库）→ 断电重启相机；\n'
                '  ② run.sh 路径不对 → cam.py cat /tmp/jiritsu/run.sh 确认')


def cmd_sh(args):
    timeout = 25.0
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ('-t', '--timeout') and i + 1 < len(args):
            try:
                timeout = float(args[i + 1])
            except ValueError:
                die('-t 后面要跟秒数，例: cam.py sh -t 120 "tar czf ..."')
            i += 2
            continue
        rest.append(a)
        i += 1
    if not rest:
        die('用法: cam.py sh [-t 秒] "<命令>"')
    cmd = ' '.join(rest)
    code, out = sh(cmd, timeout=timeout)
    if out:
        sys.stdout.write(out)
        if not out.endswith('\n'):
            sys.stdout.write('\n')
    print(f'[exit={code}]')
    return 0 if code == 0 else 1


def cmd_shf(path):
    if not os.path.exists(path):
        die(f'脚本不存在: {path}')
    return cmd_sh([open(path, 'r', encoding='utf-8').read()])


# ---------------- 自检 ----------------

def cmd_doctor():
    ok = True
    print('== 1. 通道 ==')
    try:
        j = C()
        st, _ = j.jiritsu(0x400d)
        print(f'  连接 OK    GET_VERSION={"0x%08x" % st if isinstance(st, int) else st}')
    except SystemExit:
        raise
    except Exception as e:
        print(f'  连接失败: {type(e).__name__}: {e}')
        print('  -> 试 python usb_recover.py')
        return 1

    print('\n== 2. 固件版本 ==')
    resp, d = read('/version.txt')
    if resp == 1:
        print(f'  {d.decode("latin1").strip()}')
    else:
        print('  X 读不到 /version.txt')
        ok = False

    print('\n== 3. Jiritsu 运行时 ==')
    resp, d = read('/tmp/jiritsu/tmmc.jit')
    print(f'  tmmc.jit  {"存在 " + str(len(d)) + "B" if resp == 1 else "不存在"}')
    if resp != 1:
        ok = False
    resp, d = read(R_LIB)
    print(f'  libJit.so {("存在 " + str(len(d)) + "B（载荷）") if resp == 1 else "不存在（干净状态）"}')

    print('\n== 4. 备份完整性 ==')
    if not os.path.isdir(BACKUP_DIR):
        print('  X 备份目录不存在')
        ok = False
    else:
        files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith('.bin'))
        print(f'  本地备份 {len(files)} 个文件')
        missing = [d for d in ('nflasha7', 'nflasha15', 'nflasha6', 'nflasha4')
                   if not os.path.exists(os.path.join(BACKUP_DIR, f'{d}.bin'))]
        if missing:
            print(f'  ! 缺少关键备份: {missing}')
            ok = False
        else:
            print('  关键分区齐备 (7/15/6/4)')

    print('\n== 5. 执行载荷 ==')
    if os.path.exists(PAYLOAD):
        print(f'  OK {PAYLOAD} ({os.path.getsize(PAYLOAD)}B)')
    else:
        print(f'  ! 未编译: {PAYLOAD}')
        ok = False

    print('\n== 6. 写能力 ==')
    try:
        probe = f'{REMOTE_DIR}/.doctor'
        resp, _ = write(probe, b'ok')
        if resp == 1:
            C().delete_file(probe)
            print('  可写 OK')
        else:
            print(f'  X 写入被拒 resp={resp}')
            ok = False
    except Exception as e:
        print(f'  X {type(e).__name__}: {e}')
        ok = False

    print('\n' + ('全部通过' if ok else '存在问题，见上'))
    return 0 if ok else 1


# ---------------- 便捷查询（薄封装） ----------------

STAGE = '/log/camtool'          # 相机侧暂存目录（nflasha11，500MB）


def _sh(cmd, timeout=40.0, quiet=False):
    code, out = sh(cmd, timeout=timeout)
    if not quiet:
        sys.stdout.write(out)
        if out and not out.endswith('\n'):
            sys.stdout.write('\n')
    return code, out


def cmd_ls(args):
    flags = [a for a in args if a.startswith('-')]
    paths = [a for a in args if not a.startswith('-')]
    p = paths[0] if paths else '/'
    _sh(f'ls {" ".join(flags)} {p}')


def cmd_stat(path):
    _sh(f'ls -l {path}')


def cmd_df():
    """空间信息。

    相机 busybox 被裁剪，没有 df applet。这里按 caps 缓存决定：
    只有探测到 df 存在才调用它，否则直接给「分区容量表 + 实际挂载点」。
    """
    caps = _load_state().get('caps') or {}
    if caps.get('df'):
        code, out = sh('df 2>&1')
        if code == 0 and 'not found' not in out:
            sys.stdout.write(out)
            return
    code, mounts = sh('cat /proc/mounts', timeout=30)
    mnt = {}
    for line in mounts.splitlines():
        p = line.split()
        if len(p) >= 4 and p[0].startswith('/dev/'):
            # 同一分区可能挂多处（如 nflasha10 → /tmp 和 /etc）
            mnt.setdefault(p[0].split('/')[-1], []).append((p[1], p[2], p[3]))
    print('（busybox 无 df，以下为分区容量 + 挂载状态）')
    print(f'{"分区":12s} {"容量":>9s}  {"挂载点":22s} {"类型":6s} 读写')
    total = 0
    for dev, (size, note) in PARTS.items():
        ms = mnt.get(dev)
        if ms:
            mp = ','.join(m for m, _, _ in ms)
            fs = ms[0][1]
            rw = 'rw' if 'rw' in ms[0][2].split(',') else 'ro'
        else:
            mp, fs, rw = '-', '-', '-'
        print(f'{dev:12s} {human(size):>9s}  {mp:22s} {fs:6s} {rw}')
        total += size
    print(f'{"合计":12s} {human(total):>9s}')


def cmd_ps():
    _sh('ps')


def cmd_mem():
    _sh('cat /proc/meminfo')


def cmd_dmesg(n=60, pattern=None):
    """内核日志尾部。pattern 非空时先过滤（如 usb / jiritsu / liro）。"""
    code, out = sh('dmesg', timeout=40)
    lines = out.rstrip('\n').split('\n')
    if pattern:
        lines = [l for l in lines if pattern.lower() in l.lower()]
        print(f'（过滤 "{pattern}"：命中 {len(lines)} 行）')
    for line in lines[-n:]:
        print(line)


def cmd_mount(args):
    """挂载分区。自动探测文件系统类型，挂到 /tmp/mnt_<dev>。"""
    rw = '--rw' in args
    args = [a for a in args if not a.startswith('--')]
    if not args:
        die('用法: cam.py mount <dev> [挂载点] [--rw]  例: cam.py mount nflasha1')
    dev = args[0]
    mnt = args[1] if len(args) > 1 else f'/tmp/mnt_{dev.replace("/dev/", "")}'
    if not dev.startswith('/dev/'):
        dev = '/dev/' + dev

    # 取头部 2KB 判断文件系统
    _sh(f'mkdir -p {STAGE}', quiet=True)
    _sh(f'dd if={dev} of={STAGE}/head.bin bs=1024 count=2 2>/dev/null', quiet=True)
    resp, head = read(f'{STAGE}/head.bin')
    if resp != 1:
        die(f'读不到 {dev} 的头部，无法判断文件系统')

    if head[:3] == b'\xeb\x3c\x90' or b'FAT' in head[:80]:
        fstype = 'vfat'
    elif len(head) > 1082 and head[1080:1082] == b'\x53\xef':
        fstype = 'ext2'
    else:
        fstype = 'vfat'
        print(f'（无法识别文件系统，按 {fstype} 尝试）')

    opts = 'rw,sync' if rw else 'ro'
    print(f'{dev} -> {mnt}  类型={fstype} 选项={opts}'
          + ('' if rw else '   （只读；需要写入加 --rw）'))
    code, out = _sh(f'mkdir -p {mnt} && mount -t {fstype} -o {opts} {dev} {mnt}')
    if code == 0:
        _sh(f'ls {mnt}')


def cmd_umount(mnt):
    _sh(f'umount {mnt}')


def cmd_sd():
    """SD 卡状态"""
    code, out = sh('cat /proc/partitions')
    lines = [l for l in out.split('\n') if 'mmc' in l]
    if not lines:
        print('未检测到 mmc 设备')
        return
    print('== mmc 块设备 ==')
    for l in lines:
        print('  ' + l.strip())
    code, out = sh('cat /proc/mounts')
    mounted = [l for l in out.split('\n') if '/dev/mmc' in l]
    if mounted:
        print('\n== 已挂载 ==')
        for l in mounted:
            print('  ' + l.split()[0] + ' -> ' + l.split()[1])
    else:
        print('\n未挂载。用 cam.py mount mmca10p1 /tmp/sd 挂载（注意可能与相机媒体管理冲突）')


# ---------------- 系统与能力探测 ----------------

CAP_CMDS = [
    'sh', 'ls', 'cat', 'cp', 'mv', 'rm', 'mkdir', 'rmdir', 'ln', 'chmod', 'chown',
    'touch', 'dd', 'tar', 'gzip', 'gunzip', 'zcat', 'sed', 'grep', 'egrep', 'fgrep',
    'ps', 'kill', 'pidof', 'dmesg', 'top', 'vmstat', 'sync', 'date', 'sleep', 'usleep',
    'mount', 'umount', 'mknod', 'mktemp', 'ping', 'ifconfig', 'route', 'insmod',
    'rmmod', 'lsmod', 'sysctl', 'mkdosfs', 'dosfsck', 'cpio', 'hostname', 'login',
    'df', 'du', 'free', 'wc', 'head', 'tail', 'tr', 'awk', 'find', 'id', 'vi',
    'sort', 'uniq', 'nc', 'netstat', 'wget', 'flash_erase', 'nandwrite', 'i2cget',
    'i2cset', 'i2cdetect', 'devmem', 'hexdump', 'od', 'md5sum',
]


def cmd_caps(refresh=False):
    """探测相机侧可用命令，缓存到 .cam_state.json。

    两个来源都要看：
      1. `busybox --list` —— 编译进 busybox 的 applet
      2. /bin /sbin /usr/bin /usr/sbin 下的独立二进制（如 ps 就不在 busybox 里）
    注意这台机器的 sh 没有 `command` 内建，`command -v` 会直接 not found。
    """
    st = _load_state()
    caps = None if refresh else st.get('caps')
    if not caps:
        code, out = sh('busybox --list; echo __BIN__; ls /bin /sbin /usr/bin /usr/sbin 2>/dev/null',
                       timeout=60)
        names = set()
        for line in out.splitlines():
            line = line.strip()
            if (not line or line.startswith('/') or line.endswith(':')
                    or line.startswith('[') or line.startswith('__')):
                continue
            names.add(line)
        if not names:
            die(f'探测失败（exit={code}，无输出）')
        caps = {n: True for n in names}
        st['caps'] = caps
        _save_state(st)
        print(f'（已写入 .cam_state.json，共 {len(names)} 个命令）')
    have = [c for c in CAP_CMDS if caps.get(c)]
    miss = [c for c in CAP_CMDS if not caps.get(c)]
    print(f'关注项 可用 {len(have)}: ' + ' '.join(have))
    print(f'关注项 缺失 {len(miss)}: ' + ' '.join(miss))
    extra = sorted(set(caps) - set(CAP_CMDS))
    if extra:
        print(f'\n其余（{len(extra)}）: ' + ' '.join(extra))


def cmd_dev(pattern=None):
    """设备节点总览：/proc/devices + /proc/misc + /dev。"""
    script = ("echo '== /proc/devices =='; cat /proc/devices; "
              "echo '== /proc/misc =='; cat /proc/misc 2>&1; "
              "echo '== /dev =='; ls -l /dev")
    code, out = sh(script, timeout=40)
    if pattern:
        lines = [l for l in out.splitlines() if pattern.lower() in l.lower()]
        print('\n'.join(lines) if lines else f'（无匹配 "{pattern}"）')
        return
    sys.stdout.write(out)
    if not out.endswith('\n'):
        sys.stdout.write('\n')


def cmd_modinfo(name):
    """模块详情：refcnt / holders / parameters（liro 这类驱动看参数入口）。"""
    script = (f"echo '== {name} =='; echo -n 'refcnt: '; cat /sys/module/{name}/refcnt 2>&1; "
              f"echo '-- holders --'; ls /sys/module/{name}/holders 2>&1; "
              f"echo '-- parameters --'; ls /sys/module/{name}/parameters 2>&1; "
              f"for f in /sys/module/{name}/parameters/*; do "
              f"[ -f $f ] && {{ echo -n \"$f = \"; cat $f 2>&1; echo; }}; done")
    _sh(script, timeout=40)


def cmd_setting(args):
    """查看 /setting；--backup 把机身设置备份下载到 evidence/setting/<时间>/"""
    if '--backup' not in args:
        _sh('ls -l /setting')
        return
    stamp = time.strftime('%Y%m%d_%H%M%S')
    outdir = os.path.join(EVIDENCE_DIR, 'setting', stamp)
    os.makedirs(outdir, exist_ok=True)
    print(f'下载 /setting -> {outdir}')
    for name in ('Backup.bin', 'Backup.bak', 'DmmConfig.bin', 'kemco.txt', 'ulogio.bin'):
        resp, d = read(f'/setting/{name}')
        if resp != 1:
            print(f'  X {name:14s} resp={resp}')
            continue
        open(os.path.join(outdir, name), 'wb').write(d)
        print(f'  OK {name:14s} {len(d):>9d}B  md5={md5(d)}')
    a = os.path.join(outdir, 'Backup.bin')
    b = os.path.join(outdir, 'Backup.bak')
    if os.path.exists(a) and os.path.exists(b):
        x, y = open(a, 'rb').read(), open(b, 'rb').read()
        print()
        if len(x) != len(y):
            print(f'Backup.bin({len(x)}B) vs .bak({len(y)}B): 长度不同')
        else:
            diffs = [i for i in range(len(x)) if x[i] != y[i]]
            print(f'Backup.bin vs .bak: 同长 {len(x)}B，差异 {len(diffs)}B')
            if diffs:
                i = diffs[0]
                print(f'  首个差异 @0x{i:x}  bin={x[i]:02x}  bak={y[i]:02x}')
                print(f'  差异区间 0x{diffs[0]:x} .. 0x{diffs[-1]:x}')


def _run(cmd, timeout=60):
    """跑本地命令并返回 (rc, text)。中文 Windows 的控制台输出是 GBK，不能按 UTF-8 解。"""
    import subprocess
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    raw = (r.stdout or b'') + (r.stderr or b'')
    for enc in ('gbk', 'utf-8'):
        try:
            return r.returncode, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.returncode, raw.decode('latin1')


def _pnp_restart():
    """Windows 层重启相机设备（最后一招，需要管理员）。

    返回 True=已重启，False=失败（通常是权限）。
    """
    rc, out = _run(['powershell', '-NoProfile', '-Command',
                    "Get-PnpDevice -PresentOnly | ? {$_.InstanceId -like '*VID_054C*'} "
                    "| % {$_.InstanceId}"], timeout=40)
    ids = [l.strip() for l in out.splitlines() if l.strip()]
    if not ids:
        print(f'  找不到 PnP 实例（rc={rc}）')
        return False
    for iid in ids:
        rc, msg = _run(['pnputil', '/restart-device', iid], timeout=60)
        tail = [l for l in msg.strip().splitlines() if l.strip()]
        print(f'  restart {iid}: {tail[-1] if tail else "?"}')
        if '拒绝访问' in msg or 'Access is denied' in msg or rc != 0:
            print('  → 需要管理员。用管理员 PowerShell 执行:')
            print(f'     pnputil /restart-device "{iid}"')
            return False
    time.sleep(3)
    return True


def cmd_wait(timeout=120):
    """等设备出现并报告状态（重插线 / 关开相机电源后用它）。"""
    from jiritsu_lib import present, probe_usb_state, mode_hint
    t0 = time.time()
    last = None
    print(f'等待相机（最多 {timeout}s）…')
    while time.time() - t0 < timeout:
        p = present()
        st = probe_usb_state() if p else 'absent'
        if st != last:
            print(f'[{time.time()-t0:5.1f}s] 可见={["0x%04x" % x for x in p] or "无"}'
                  f'  状态={st}')
            last = st
        if st in ('ok', 'unknown'):
            print('设备就绪' + ('（控制传输不可测，靠认证验证）' if st == 'unknown' else ''))
            return 0
        time.sleep(1.0)
    print(f'超时，最后状态={last}')
    hint = mode_hint()
    if hint:
        print(f'→ 检测到索尼设备：{hint}')
    if last == 'no_driver':
        print('→ 设备被 Windows 自带驱动（USBSTOR）占着，libusb 打不开：'
              '用 Zadig 给 054C:0D95 装 libusbK/WinUSB')
    elif last == 'absent':
        print('→ Windows 若显示「未知 USB 设备(设备描述符请求失败)」，'
              '说明固件 USB 栈仍挂死：关开相机电源（不是只拔插线）')
    return 1


def cmd_reset():
    """USB 硬复位（不用拔电池）。连接层已内置，这里留作手动兜底。"""
    from jiritsu_lib import present, probe_usb_state, reset_devices
    print(f'复位前: {["0x%04x" % p for p in present()] or "无"}'
          f'  状态={probe_usb_state()}')
    hit = reset_devices(verbose=True)
    print(f'已复位: {["0x%04x" % p for p in hit] or "无（设备不在）"}')
    time.sleep(1.0)
    st = probe_usb_state()
    print(f'复位后: {["0x%04x" % p for p in present()] or "无"}  状态={st}')
    if st == 'hung':
        print('→ libusb 复位无效，尝试 Windows 层重启设备…')
        if _pnp_restart():
            time.sleep(2)
            st = probe_usb_state()
            print(f'重启后状态={st}' + ('（已恢复）' if st == 'ok' else ''))
        if st != 'ok':
            print('→ 仍无响应：固件 USB 栈挂死，请拔插 USB 线（或关开相机电源）')



# ---------------- 大文件与目录传输 ----------------

def cmd_slice(dev, offset, length, local=None):
    """只取设备的一段（避免整盘 500MB 下载）"""
    if not dev.startswith('/dev/'):
        dev = '/dev/' + dev
    offset, length = _int(offset), _int(length)
    skip = offset // 512
    lead = offset % 512
    count = (lead + length + 511) // 512
    out = f'{STAGE}/slice.bin'
    _sh(f'mkdir -p {STAGE}', quiet=True)
    code, _ = _sh(f'dd if={dev} of={out} bs=512 skip={skip} count={count} 2>/dev/null', quiet=True)
    resp, d = read(out)
    if resp != 1:
        die('切片读取失败')
    d = d[lead:lead + length]
    local = local or 'slice.bin'
    open(local, 'wb').write(d)
    print(f'{dev}[{offset:#x}:+{len(d)}] -> {local}  md5={md5(d)}')


def _int(v):
    """数字或字符串都接受（CLI 传字符串，程序内调用传 int）。"""
    return v if isinstance(v, int) else int(str(v), 0)


def cmd_hex(remote, offset=0, length=256):
    """十六进制查看远程文件的一段。大文件用 dd 定位，不整读。"""
    offset, length = _int(offset), _int(length)
    code, out = sh(f'ls -l {remote}')
    parts = out.split()
    size = None
    if len(parts) >= 5:
        try:
            size = int(parts[4])
        except ValueError:
            size = None
    if size is not None and size > 8 * 1024 * 1024:
        tmp = '/log/camtool/hex.bin'
        _sh('mkdir -p /log/camtool', quiet=True)
        skip, lead = offset // 512, offset % 512
        count = (lead + length + 511) // 512
        _sh(f'dd if={remote} of={tmp} bs=512 skip={skip} count={count} 2>/dev/null',
            quiet=True)
        resp, d = read(tmp)
        _sh(f'rm -f {tmp}', quiet=True)
        if resp != 1:
            die('切片读取失败')
        d = d[lead:lead + length]
    else:
        resp, d = read(remote)
        if resp != 1:
            die(f'读取失败 resp={resp}')
        d = d[offset:offset + length]
    for i in range(0, len(d), 16):
        chunk = d[i:i + 16]
        hexs = ' '.join(f'{b:02x}' for b in chunk)
        text = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f'{offset+i:08x}  {hexs:<47}  {text}')


def cmd_pull(remote_dir, local_dir):
    """整目录下载（tar 打包后一次取回，比逐文件快得多）"""
    os.makedirs(local_dir, exist_ok=True)
    tgz = f'{STAGE}/pull.tgz'
    _sh(f'mkdir -p {STAGE}', quiet=True)
    print(f'打包 {remote_dir} ...')
    code, out = sh(f'cd {remote_dir} && tar czf {tgz} . 2>&1; echo RC=$?', timeout=180)
    if 'RC=0' not in out:
        die(f'打包失败: {out[-200:]}')
    resp, d = read(tgz)
    if resp != 1:
        die('取回打包文件失败')
    p = os.path.join(local_dir, '_pull.tgz')
    open(p, 'wb').write(d)
    import tarfile
    with tarfile.open(p, 'r:gz') as t:
        try:
            t.extractall(local_dir, filter='data')
        except TypeError:            # Python < 3.12 没有 filter 参数
            t.extractall(local_dir)
    print(f'已解包到 {local_dir}  ({len(d)}B)')
    _sh(f'rm -f {tgz}', quiet=True)


def cmd_push(local_dir, remote_dir):
    """整目录上传"""
    import tarfile
    tgz = os.path.join(DATA_ROOT, '_push.tgz')
    with tarfile.open(tgz, 'w:gz') as t:
        t.add(local_dir, arcname='.')
    blob = open(tgz, 'rb').read()
    _sh(f'mkdir -p {STAGE} {remote_dir}', quiet=True)
    remote_tgz = f'{STAGE}/push.tgz'
    resp, _ = write(remote_tgz, blob)
    if resp != 1:
        die('上传打包文件失败')
    code, out = sh(f'cd {remote_dir} && tar xzf {remote_tgz} 2>&1; echo RC=$?', timeout=120)
    os.remove(tgz)
    print(f'已解包到 {remote_dir}: {out.strip()[-60:]}')


def cmd_run(local_bin, args):
    """上传并执行一个本地 ARM 可执行文件"""
    if not os.path.exists(local_bin):
        die(f'不存在: {local_bin}')
    blob = open(local_bin, 'rb').read()
    _sh(f'mkdir -p {STAGE}', quiet=True)
    remote = f'{STAGE}/tool'
    resp, _ = write(remote, blob)
    if resp != 1:
        die('上传失败')
    argline = ' '.join(args)
    code, out = sh(f'chmod +x {remote} && {remote} {argline}', timeout=60)
    sys.stdout.write(out)
    print(f'[exit={code}]')


# ---------------- 协议级命令（逆向用） ----------------

def _parse_hex(s):
    s = s.replace(' ', '').replace('0x', '')
    return bytes.fromhex(s) if s else b''


def _proto_dump(tag, req, resp, out, save=False):
    """把一次协议往返落盘，方便离线分析（色彩矩阵逆向要反复对比）。"""
    if not save:
        return
    d = os.path.join(EVIDENCE_DIR, 'proto')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f'{time.strftime("%Y%m%d_%H%M%S")}_{tag}.bin')
    with open(p, 'wb') as f:
        f.write(b'REQ ' + req + b'\nRESP ' + str(resp).encode() + b'\nDATA ' + out)
    print(f'  saved -> {p}')


def _show_hex(name, data, full=False):
    n = len(data) if full else min(len(data), 64)
    tail = '' if full or len(data) <= n else ' …'
    print(f'  {name}({len(data)}B)={data[:n].hex()}{tail}')


def cmd_raw(pfunc, hexdata, save=False, full=False):
    """发送任意 Senser 包: cam.py raw 0x40 0110004010 [--save] [--full]"""
    pfunc = _int(pfunc)
    data = _parse_hex(hexdata)
    resp, out = C().pkt(pfunc, data)
    print(f'pFunc=0x{pfunc:04x} 发送 {len(data)}B')
    print(f'  resp={resp}')
    _show_hex('out', out, full)
    _proto_dump(f'raw_{pfunc:04x}', data, resp, out, save)


def cmd_cmd(block, code, hexdata='', save=False, full=False):
    """发送 AdjustControl 命令: cam.py cmd 0x603 0x1 [hex] [--save] [--full]"""
    block, code = _int(block), _int(code)
    data = _parse_hex(hexdata)
    payload = struct.pack('<HH', block, code) + data
    resp, out = C().pkt(0x40, payload)
    print(f'AdjustControl block=0x{block:04x} code=0x{code:04x} data={len(data)}B')
    print(f'  resp={resp}')
    _show_hex('out', out, full)
    _proto_dump(f'ac_{block:04x}_{code:04x}', payload, resp, out, save)


def cmd_jir(code, hexdata='', save=False, full=False):
    """发送 Jiritsu 命令: cam.py jir 0x4002 [hex] [--save] [--full]"""
    code = _int(code)
    data = _parse_hex(hexdata)
    payload = struct.pack('<HH', 0x1001, code) + data
    st, out = C().jiritsu(code, data)
    print(f'Jiritsu cmd=0x{code:04x} data={len(data)}B')
    print(f'  status={("0x%08x" % st) if isinstance(st, int) else st}')
    _show_hex('out', out, full)
    _proto_dump(f'jir_{code:04x}', payload, st if isinstance(st, int) else 0, out, save)


# ---------------- 日志与快照 ----------------

def cmd_log(pull=False):
    _sh('ls -l /log')
    if pull:
        outdir = os.path.join(EVIDENCE_DIR, 'log')
        os.makedirs(outdir, exist_ok=True)
        for name in ('AccessLog.dat', 'ThreadMonitor.bin'):
            resp, d = read(f'/log/{name}')
            if resp == 1:
                open(os.path.join(outdir, name), 'wb').write(d)
                print(f'  已下载 /log/{name}  {len(d)}B')
            else:
                print(f'  /log/{name} 读取失败 resp={resp}')


SNAP_CMDS = [
    ('version', 'cat /version.txt'),
    ('uname', 'uname -a'),
    ('mounts', 'cat /proc/mounts'),
    ('partitions', 'cat /proc/partitions'),
    ('meminfo', 'cat /proc/meminfo'),
    ('ps', 'ps'),
    ('ls_tmp_jiritsu', 'ls -l /tmp/jiritsu'),
    ('ls_log', 'ls -l /log'),
    ('ls_setting', 'ls -l /setting'),
    ('ls_system', 'ls -l /system'),
    ('ls_usr_bin', 'ls -l /usr/bin'),
    ('modules', 'lsmod'),
]


def cmd_snapshot():
    """采集一份诊断快照（排查/对比改动前后）"""
    stamp = time.strftime('%Y%m%d_%H%M%S')
    outdir = os.path.join(EVIDENCE_DIR, 'snapshots', stamp)
    os.makedirs(outdir, exist_ok=True)
    print(f'快照 -> {outdir}')
    for name, cmd in SNAP_CMDS:
        code, out = sh(cmd, timeout=40)
        with open(os.path.join(outdir, f'{name}.txt'), 'w', encoding='utf-8') as f:
            f.write(f'# {cmd}  (exit={code})\n')
            f.write(out)
        print(f'  {name:16s} {len(out):6d}B  exit={code}')
    print('完成')


def cmd_watch(cmdstr, interval=2.0, times=10):
    """周期性执行，用于观察参数变化"""
    for i in range(times):
        code, out = sh(cmdstr)
        print(f'--- [{i+1}/{times}] exit={code} ---')
        sys.stdout.write(out)
        if i + 1 < times:
            time.sleep(interval)


# ---------------- 交互与常驻 ----------------

def cmd_shell():
    """交互式 shell：整个会话复用一条已认证连接，不再每条命令重认证。"""
    C()
    ensure_mount()
    print('进入相机 shell（exit 或 Ctrl-D 退出）。每条命令复用同一连接。')
    n = 0
    while True:
        try:
            line = input('cam> ')
        except (EOFError, KeyboardInterrupt):
            print()
            break
        line = line.strip()
        if not line:
            continue
        if line in ('exit', 'quit'):
            break
        n += 1
        code, out = sh(line, timeout=180)
        sys.stdout.write(out)
        if out and not out.endswith('\n'):
            sys.stdout.write('\n')
        print(f'[exit={code}]')
    print(f'会话结束（{n} 条命令）')


def cmd_spawn(cmdstr, log='/log/camtool/spawn.log'):
    """后台常驻启动，立即返回 PID。用于触屏注入这类守护进程。

    两个坑：
      1. 这台机器没有 setsid / nohup，也不能用 `command -v`（sh 无此内建）
      2. 复合命令（while/for/管道）不能直接塞进 if-then 一行，会语法错
        ⇒ 先把命令写成 daemon.sh，再 `sh daemon.sh &`
    """
    ensure_mount()
    daemon = f'{REMOTE_DIR}/daemon.sh'
    resp, _ = write(daemon, ('#!/bin/sh\n' + cmdstr + '\n').encode())
    if resp != 1:
        die(f'写 daemon.sh 失败 resp={resp}')
    script = (
        'mkdir -p /log/camtool\n'
        f'LOG={log}\n'
        f'sh {daemon} >$LOG 2>&1 </dev/null &\n'
        'echo SPAWN_PID=$!\n'
    )
    code, out = sh(script, timeout=40)
    sys.stdout.write(out)
    print(f'日志: {log}（用 cam.py tail {log} 查看）')


def cmd_kill(target, confirm=False, sig=9):
    """杀进程：cam.py kill <pid|名字> [--confirm]"""
    if target.isdigit():
        _sh(f'kill -{sig} {target} && echo killed')
        return
    if not confirm:
        print(f'预览: killall -{sig} {target}（加 --confirm 执行）')
        return
    _sh(f'killall -{sig} {target} && echo killed')


def cmd_reboot(action='reboot', confirm=False):
    """重启/关机：cam.py reboot [reboot|poweroff] --confirm"""
    if not confirm:
        print(f'预览: sync; {action}（加 --confirm 执行）')
        return
    print(f'执行 sync; {action} …（USB 会断开，属正常）')
    try:
        sh(f'sync; {action}', timeout=10)
    except Exception:
        pass


# ---------------- 远端分析 ----------------

def cmd_diff(remote_a, remote_b):
    """比较两个远端文件（设置备份/改机前后对比常用）。"""
    a = read_or_die(remote_a)
    b = read_or_die(remote_b)
    print(f'A {remote_a}  {len(a)}B  md5={md5(a)}')
    print(f'B {remote_b}  {len(b)}B  md5={md5(b)}')
    if a == b:
        print('完全一致')
        return
    if len(a) != len(b):
        print(f'长度不同: {len(a)} vs {len(b)}')
    n = min(len(a), len(b))
    ranges = []
    start = None
    for i in range(n):
        if a[i] != b[i]:
            if start is None:
                start = i
        elif start is not None:
            ranges.append((start, i - 1))
            start = None
    if start is not None:
        ranges.append((start, n - 1))
    total = sum(e - s + 1 for s, e in ranges)
    print(f'差异 {total}B，共 {len(ranges)} 段')
    for s, e in ranges[:20]:
        print(f'  0x{s:08x}..0x{e:08x} ({e-s+1}B)')
    if len(ranges) > 20:
        print(f'  … 还有 {len(ranges)-20} 段')
    s = ranges[0][0]
    lo = max(0, s - 16)
    hi = min(n, s + 48)
    print(f'\n首段附近 0x{lo:x}:')
    for i in range(lo, hi, 16):
        aa = a[i:i+16]
        bb = b[i:i+16]
        mark = ' ' if aa == bb else '*'
        print(f'{mark} {i:08x}  {aa.hex():<32}  {bb.hex()}')


def cmd_tail(remote, nbytes=4096):
    """看远端文件尾部（不用下载 500MB 的 /log）。大文件用 dd 定位。"""
    nbytes = _int(nbytes)
    code, out = sh(f'ls -l {remote}')
    parts = out.split()
    size = None
    if len(parts) >= 5:
        try:
            size = int(parts[4])
        except ValueError:
            size = None
    if size is None:
        print('（拿不到文件大小，直接整读）')
        resp, d = read(remote)
        if resp != 1:
            die(f'读取失败 resp={resp}')
        d = d[-nbytes:]
    elif size > 4 * 1024 * 1024:
        skip = max(0, size - nbytes)
        tmp = '/log/camtool/tail.bin'
        _sh('mkdir -p /log/camtool', quiet=True)
        _sh(f'dd if={remote} of={tmp} bs=1 skip={skip} 2>/dev/null', quiet=True)
        resp, d = read(tmp)
        _sh(f'rm -f {tmp}', quiet=True)
        if resp != 1:
            die('尾部切片失败')
    else:
        resp, d = read(remote)
        if resp != 1:
            die(f'读取失败 resp={resp}')
        d = d[-nbytes:]
    sys.stdout.write(d.decode('latin1'))
    if d and not d.endswith(b'\n'):
        print()


def cmd_grep(pattern, remote, ignore_case=False):
    """在远端文件里搜索（不用下载整文件）。"""
    flag = '-i ' if ignore_case else ''
    _sh(f"grep -n {flag}'{pattern}' {remote}")


def cmd_lsr(root='/', pattern=None, limit=2000):
    """递归列目录（busybox 没有 find，这里用 ls -lR 解析）。"""
    code, out = sh(f'ls -lR {root}', timeout=120)
    cur = ''
    hits = []
    for line in out.splitlines():
        if line.startswith('/') and line.endswith(':'):
            cur = line[:-1]
            continue
        p = line.split()
        if len(p) >= 8 and p[0][:1] in ('-', 'd', 'l', 'c', 'b'):
            name = p[-1]
            if pattern and pattern.lower() not in name.lower():
                continue
            hits.append((p[0], p[4], f'{cur}/{name}' if cur else name))
    for mode, size, path in hits[:limit]:
        print(f'{mode:11s} {size:>10s}  {path}')
    print(f'共 {len(hits)} 项' + ('' if len(hits) <= limit else f'（只显示前 {limit}）'))


def cmd_md5(remote):
    """远端 md5。有 md5sum 就用它，否则下载后本地算。"""
    caps = _load_state().get('caps') or {}
    if caps.get('md5sum', True):
        code, out = sh(f'md5sum {remote}')
        if code == 0 and 'not found' not in out and out.strip():
            print(out.strip())
            return
    resp, d = read(remote)
    if resp != 1:
        die(f'读取失败 resp={resp}')
    print(f'{md5(d)}  {remote}  ({len(d)}B, 本地计算)')


def cmd_sysfs(path, value=None, confirm=False):
    """读/写 sysfs。写需要 --confirm（例：/sys/power/image_drop）。"""
    if value is None:
        _sh(f'cat {path}')
        return
    if not confirm:
        print(f'预览: echo {value} > {path}')
        print('（加 --confirm 才执行；sysfs 写入可能改变设备行为）')
        return
    _sh(f'echo {value} > {path} && cat {path}')


def cmd_irq(pattern=None):
    """中断表。看 liro/dma330 是否真在跑。"""
    code, out = sh('cat /proc/interrupts')
    lines = out.splitlines()
    if pattern:
        head = lines[:1]
        body = [l for l in lines[1:] if pattern.lower() in l.lower()]
        lines = head + body
    print('\n'.join(lines))


def cmd_pf():
    """已知 pFunc 与 Jiritsu 命令码速查（逆向时不用翻记忆）。"""
    print('pFunc:')
    for f, n, d in [(0x40, 'AdjustControl', 'Senser 命令通道（block+code）'),
                    (0xff01, 'FileControl', '按文件名读/写/删，任意路径，可整段读写块设备'),
                    (0xff03, 'MemoryDump', '按 base/size 读内存')]:
        print(f'  0x{f:04x}  {n:16s} {d}')
    print('\nJiritsu (block=0x1001):')
    for c, n in [(0x4001, 'MODE'), (0x4002, 'MOUNT'), (0x4003, 'FORMAT (危险，会卡死服务)'),
                 (0x4004, 'START'), (0x4005, 'DEBUG'), (0x4006, 'FIRMUP (危险)'),
                 (0x400d, 'GET_VERSION'), (0x400f, 'SET_FILE_PATH'),
                 (0x4010, 'SET_FILE2'), (0x4011, 'GET_FILE2')]:
        print(f'  0x{c:04x}  {n}')


def cmd_snapdiff(a=None, b=None):
    """对比两份快照（默认最近两次），看改动前后差异。"""
    base = os.path.join(EVIDENCE_DIR, 'snapshots')
    if not os.path.isdir(base):
        die('还没有快照，先跑 cam.py snapshot')
    dirs = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    if not a:
        if len(dirs) < 2:
            die('快照不足两份')
        a, b = dirs[-2], dirs[-1]
    b = b or dirs[-1]
    pa, pb = os.path.join(base, a), os.path.join(base, b)
    import difflib
    names = sorted(set(os.listdir(pa)) | set(os.listdir(pb)))
    changed = 0
    for n in names:
        fa, fb = os.path.join(pa, n), os.path.join(pb, n)
        ta = (open(fa, encoding='utf-8', errors='replace').read().splitlines()
              if os.path.exists(fa) else [])
        tb = (open(fb, encoding='utf-8', errors='replace').read().splitlines()
              if os.path.exists(fb) else [])
        if ta == tb:
            continue
        changed += 1
        print(f'\n=== {n} ===')
        diff = list(difflib.unified_diff(ta, tb, fromfile=a, tofile=b, lineterm='', n=2))
        for line in diff[:80]:
            print(line)
        if len(diff) > 80:
            print(f'… 还有 {len(diff)-80} 行')
    print(f'\n{changed}/{len(names)} 个文件有变化  ({a} -> {b})')


# ---------------- 入口 ----------------

USAGE = __doc__


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        return 0
    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd in ('-h', '--help', 'help'):
        print(USAGE)
        return 0

    # --- 设备与备份 ---
    if cmd == 'info':
        cmd_info()
    elif cmd == 'doctor':
        return cmd_doctor()
    elif cmd == 'parts':
        cmd_parts('--probe' in args)
    elif cmd == 'backup':
        devs = [a for a in args if not a.startswith('--')]
        cmd_backup(devs, '--all' in args)
    elif cmd == 'restore':
        if not args:
            die('用法: cam.py restore <dev> [--confirm] [--force]')
        dev = [a for a in args if not a.startswith('--')][0]
        cmd_restore(dev, '--confirm' in args, '--force' in args)
    elif cmd == 'verify':
        return 1 if cmd_verify([a for a in args if not a.startswith('--')]) else 0

    # --- 文件 ---
    elif cmd == 'cat':
        if not args:
            die('用法: cam.py cat <remote>')
        cmd_cat(args[0])
    elif cmd == 'get':
        if not args:
            die('用法: cam.py get <remote> [local]')
        cmd_get(args[0], args[1] if len(args) > 1 else None)
    elif cmd == 'put':
        if len(args) < 2:
            die('用法: cam.py put <local> <remote>')
        cmd_put(args[0], args[1])
    elif cmd == 'rm':
        if not args:
            die('用法: cam.py rm <remote>')
        cmd_rm(args[0])
    elif cmd == 'hex':
        if not args:
            die('用法: cam.py hex <remote> [offset] [length]')
        cmd_hex(args[0],
                args[1] if len(args) > 1 else 0,
                args[2] if len(args) > 2 else 256)
    elif cmd == 'slice':
        if len(args) < 3:
            die('用法: cam.py slice <dev> <offset> <length> [local]')
        cmd_slice(args[0], args[1], args[2], args[3] if len(args) > 3 else None)
    elif cmd == 'pull':
        if len(args) < 2:
            die('用法: cam.py pull <remote_dir> <local_dir>')
        cmd_pull(args[0], args[1])
    elif cmd == 'push':
        if len(args) < 2:
            die('用法: cam.py push <local_dir> <remote_dir>')
        cmd_push(args[0], args[1])

    # --- 执行 ---
    elif cmd == 'sh':
        return cmd_sh(args)
    elif cmd == 'shf':
        if not args:
            die('用法: cam.py shf <local_script>')
        return cmd_shf(args[0])
    elif cmd == 'run':
        if not args:
            die('用法: cam.py run <local_bin> [args...]')
        cmd_run(args[0], args[1:])
    elif cmd == 'shell':
        cmd_shell()
    elif cmd == 'spawn':
        if not args:
            die('用法: cam.py spawn "<命令>" [日志路径]')
        cmd_spawn(args[0], args[1] if len(args) > 1 else '/log/camtool/spawn.log')
    elif cmd == 'kill':
        if not args:
            die('用法: cam.py kill <pid|名字> [--confirm]')
        rest = [a for a in args if not a.startswith('--')]
        cmd_kill(rest[0], '--confirm' in args)
    elif cmd == 'reboot':
        rest = [a for a in args if not a.startswith('--')]
        cmd_reboot(rest[0] if rest else 'reboot', '--confirm' in args)
    elif cmd == 'watch':
        if not args:
            die('用法: cam.py watch "<命令>" [间隔秒] [次数]')
        cmd_watch(args[0],
                  float(args[1]) if len(args) > 1 else 2.0,
                  int(args[2]) if len(args) > 2 else 10)

    # --- 查询 ---
    elif cmd == 'ls':
        cmd_ls(args)
    elif cmd == 'lsr':
        rest = [a for a in args if not a.startswith('-')]
        cmd_lsr(rest[0] if rest else '/', rest[1] if len(rest) > 1 else None)
    elif cmd == 'stat':
        if not args:
            die('用法: cam.py stat <path>')
        cmd_stat(args[0])
    elif cmd == 'df':
        cmd_df()
    elif cmd == 'ps':
        cmd_ps()
    elif cmd == 'mem':
        cmd_mem()
    elif cmd == 'dmesg':
        rest = [a for a in args if not a.startswith('-')]
        n = int(rest[0]) if rest and rest[0].isdigit() else 60
        pat = rest[1] if len(rest) > 1 else None
        cmd_dmesg(n, pat)
    elif cmd == 'irq':
        cmd_irq(args[0] if args else None)
    elif cmd == 'log':
        cmd_log('--pull' in args)
    elif cmd == 'snapshot':
        cmd_snapshot()
    elif cmd == 'snapdiff':
        rest = [a for a in args if not a.startswith('-')]
        cmd_snapdiff(rest[0] if rest else None, rest[1] if len(rest) > 1 else None)

    # --- 分析 ---
    elif cmd == 'diff':
        if len(args) < 2:
            die('用法: cam.py diff <remoteA> <remoteB>')
        cmd_diff(args[0], args[1])
    elif cmd == 'tail':
        if not args:
            die('用法: cam.py tail <remote> [字节数]')
        cmd_tail(args[0], args[1] if len(args) > 1 else 4096)
    elif cmd == 'grep':
        if len(args) < 2:
            die('用法: cam.py grep <模式> <remote> [-i]')
        rest = [a for a in args if a != '-i']
        cmd_grep(rest[0], rest[1], '-i' in args)
    elif cmd == 'md5':
        if not args:
            die('用法: cam.py md5 <remote>')
        cmd_md5(args[0])

    # --- 系统 ---
    elif cmd == 'caps':
        cmd_caps('--refresh' in args)
    elif cmd == 'dev':
        cmd_dev(args[0] if args else None)
    elif cmd == 'modinfo':
        if not args:
            die('用法: cam.py modinfo <模块名>  例: cam.py modinfo liro')
        cmd_modinfo(args[0])
    elif cmd == 'sysfs':
        if not args:
            die('用法: cam.py sysfs <路径> [值] [--confirm]')
        rest = [a for a in args if a != '--confirm']
        cmd_sysfs(rest[0], rest[1] if len(rest) > 1 else None, '--confirm' in args)
    elif cmd == 'setting':
        cmd_setting(args)

    # --- 挂载 ---
    elif cmd == 'mount':
        cmd_mount(args)
    elif cmd == 'umount':
        if not args:
            die('用法: cam.py umount <挂载点>')
        cmd_umount(args[0])
    elif cmd == 'sd':
        cmd_sd()

    # --- 协议级（逆向） ---
    elif cmd == 'raw':
        rest = [a for a in args if not a.startswith('--')]
        if not rest:
            die('用法: cam.py raw <pfunc> [hexdata] [--save] [--full]')
        cmd_raw(rest[0], rest[1] if len(rest) > 1 else '',
                '--save' in args, '--full' in args)
    elif cmd == 'cmd':
        rest = [a for a in args if not a.startswith('--')]
        if len(rest) < 2:
            die('用法: cam.py cmd <block> <code> [hexdata] [--save] [--full]')
        cmd_cmd(rest[0], rest[1], rest[2] if len(rest) > 2 else '',
                '--save' in args, '--full' in args)
    elif cmd == 'jir':
        rest = [a for a in args if not a.startswith('--')]
        if not rest:
            die('用法: cam.py jir <code> [hexdata] [--save] [--full]')
        cmd_jir(rest[0], rest[1] if len(rest) > 1 else '',
                '--save' in args, '--full' in args)
    elif cmd == 'pf':
        cmd_pf()

    # --- 维护 ---
    elif cmd == 'reset':
        cmd_reset()
    elif cmd == 'wait':
        return cmd_wait(int(args[0]) if args else 120)

    else:
        die(f'未知命令: {cmd}\n\n{USAGE}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
