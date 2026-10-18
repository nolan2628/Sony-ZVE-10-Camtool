#!/usr/bin/env python3
"""
jiritsu_lib.py - 共用连接层（MSC auth -> 0x0336 -> 服务 auth -> 通道）

被 probe/dump 脚本复用，避免每个脚本重复写认证流程。
"""
import os
import struct
import sys
import time

# ---- 第三方依赖 Sony-PMCA-RE (ma1co, MIT) ----
# 优先级：$PMCA_ROOT > ../ref_a7c2_fl23/a7c2_fl23_preview > ../vendor
# 仓库里不带 vendor 副本（避免第三方代码混进提交），用的时候指一下：
#   export PMCA_ROOT=/path/to/a7c2-fl23-research/a7c2_fl23_preview
_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDS = [
    os.environ.get('PMCA_ROOT'),
    os.path.join(_HERE, '..', 'ref_a7c2_fl23', 'a7c2_fl23_preview'),
    os.path.join(_HERE, 'vendor'),
]
for _p in _CANDS:
    if not _p:
        continue
    _sub = None
    if os.path.isdir(os.path.join(_p, 'pmca')):
        _sub = _p
    elif os.path.isdir(os.path.join(_p, 'vendor', 'pmca')):
        _sub = os.path.join(_p, 'vendor')
    if _sub:
        sys.path.insert(0, _sub)
        sys.path.insert(0, _p)
        break
else:
    sys.exit('缺少 Sony-PMCA-RE。请 clone ma1co/Sony-PMCA-RE 或\n'
             '  QAQLeonard/a7c2-fl23-research（内含 vendor/pmca），\n'
             '  然后 export PMCA_ROOT=<含 pmca/ 的那层目录>')

import usb.core
import usb.util
import usb.backend.libusb1
import libusb_package

from pmca.usb.driver.generic import GenericUsbDriver
from pmca.usb.driver.generic.libusb import UsbBackend
from pmca.usb.sony import SonySenserAuthDevice, SonySenserDevice

MSC_PID = 0x0D95
SVC_PID = 0x0336


def libusb_backend():
    return usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)


def find(pid):
    devs = list(usb.core.find(find_all=True, idVendor=0x054c, idProduct=pid,
                              backend=libusb_backend()))
    return devs[0] if devs else None


def wait_for(pid, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = find(pid)
        if d is not None:
            return d
        time.sleep(0.5)
    return None


def present():
    """当前可见的相机 PID 列表"""
    return [pid for pid in (MSC_PID, SVC_PID) if find(pid) is not None]


def reset_devices(settle=2.5, verbose=False):
    """USB 硬复位（等价于拔插一次，不用碰相机电源）。

    对 0x0336 / 0x0D95 依次 reset 并释放句柄。握手错位
    （'NoneType' object has no attribute 'cmd'）、claim 被拒、管道 stall
    这三类僵死都靠它恢复。
    """
    hit = []
    for pid in (SVC_PID, MSC_PID):
        d = find(pid)
        if d is None:
            continue
        hit.append(pid)
        try:
            d.reset()
        except Exception as e:
            if verbose:
                print(f'  reset 0x{pid:04x} 失败: {type(e).__name__}: {e}')
        try:
            usb.util.dispose_resources(d)
        except Exception:
            pass
    if hit:
        time.sleep(settle)
    return hit


class Jiritsu:
    """服务模式通道封装"""

    def __init__(self, auth, ch):
        self.auth = auth
        self.ch = ch

    # ---- 底层 ----
    def pkt(self, pfunc, data, retries=2, delay=0.6):
        """发一包。USB 读超时会抛 GenericUsbException —— 相机在 MOUNT/大文件
        操作后偶尔超过默认 5s 才回应，这里退避重试，避免整个会话被打断。
        破坏性命令（FORMAT/FIRMUP）请传 retries=0。
        """
        last = None
        for i in range(retries + 1):
            try:
                return self.ch.sendSenserPacket(pfunc, data)
            except Exception as e:
                last = e
                if i < retries:
                    time.sleep(delay * (i + 1))
        raise last

    def jiritsu(self, cmd, data=b'', retries=2):
        resp, out = self.pkt(0x40, struct.pack('<HH', 0x1001, cmd) + data,
                             retries=retries)
        if resp:
            return ('resp', resp), b''
        if len(out) < 4:
            return ('short', len(out)), out
        return struct.unpack_from('<I', out)[0], out[4:]

    # ---- Jiritsu 文件接口（路径受 /tmp/jiritsu 前缀限制）----
    def select(self, path):
        return self.jiritsu(15, b'\0' + path.encode() + b'\0')

    def read_file(self, path):
        self.select(path)
        return self.jiritsu(17, b'\0\xff\0')

    def write_file(self, path, data):
        self.select(path)
        return self.jiritsu(16, b'\0\xff\0' + data)

    def delete_file(self, path):
        self.select(path)
        return self.jiritsu(18, b'\0\xff')

    # ---- FileControl 接口（pFunc 0xff01，按文件名）----
    def file_read(self, filename):
        fn = filename.encode('latin1')
        fn += b'\0' * (4 - len(fn) % 4)
        hdr = struct.pack('<HH', 2, len(fn))     # cmd=READ, filenameSize
        return self.pkt(0xff01, hdr + fn)

    def file_write(self, filename, data):
        fn = filename.encode('latin1')
        fn += b'\0' * (4 - len(fn) % 4)
        hdr = struct.pack('<HH', 1, len(fn))     # cmd=WRITE
        return self.pkt(0xff01, hdr + fn + data)

    def file_delete(self, filename):
        fn = filename.encode('latin1')
        fn += b'\0' * (4 - len(fn) % 4)
        hdr = struct.pack('<HH', 3, len(fn))     # cmd=DELETE
        return self.pkt(0xff01, hdr + fn)

    # ---- MemoryDump 接口（pFunc 0xff03）----
    def mem_dump(self, base, size):
        return self.pkt(0xff03, struct.pack('<II', base, size))

    def stop(self):
        try:
            self.auth.stop()
        except Exception:
            pass


def connect(timeout=90, verbose=True):
    """返回 Jiritsu 实例；失败返回 None"""
    usb_to = int(os.environ.get('CAM_USB_TIMEOUT', '15000'))
    dev = find(SVC_PID)
    if dev is None:
        msc = find(MSC_PID)
        if msc is None:
            if verbose:
                print('X 无 0x0D95 / 0x0336')
            return None
        msc.default_timeout = usb_to
        if verbose:
            print('MSC auth...')
        auth = SonySenserAuthDevice(GenericUsbDriver(UsbBackend(msc)))
        auth.start()
        auth.authenticate()
        try:
            usb.util.dispose_resources(msc)
        except Exception:
            pass
        if verbose:
            print(f'  等待 0x0336（最多 {timeout}s）...')
        dev = wait_for(SVC_PID, timeout)
        if dev is None:
            if verbose:
                print('X 0x0336 未出现')
            return None
        if verbose:
            print('  0x0336 已出现')

    dev.default_timeout = usb_to
    if verbose:
        print('service auth...')
    auth = SonySenserAuthDevice(GenericUsbDriver(UsbBackend(dev)))
    auth.start()
    auth.authenticate()
    if verbose:
        print('  OK')
    return Jiritsu(auth, SonySenserDevice(auth.driver))


def health(j):
    """轻量健康检查：读一个必然存在的小文件。"""
    try:
        resp, d = j.file_read('/version.txt')
        return resp == 1 and len(d) > 0
    except Exception:
        return False


LAST_ERROR = ''


# 索尼相机常见 USB 模式（同一个机身切换连接方式就会换 PID）
MODE_HINT = {
    0x0D95: '大容量存储 MSC（本工具需要的模式）',
    0x0D96: 'MTP/PTP —— 需在菜单里改成「大容量存储」',
    0x0336: '服务模式 Senser（认证后的目标）',
    0x0994: 'HID 控制设备（电脑遥控相关）',
}


def scan_sony():
    """列出所有 054C 设备的 PID（不区分模式）。"""
    devs = list(usb.core.find(find_all=True, idVendor=0x054c,
                              backend=libusb_backend()))
    return [d.idProduct for d in devs]


def mode_hint(pids=None):
    """把当前 PID 翻译成人话，用于诊断「相机在但连不上」。"""
    pids = scan_sony() if pids is None else pids
    if not pids:
        return ''
    return '；'.join(f'0x{p:04X} = {MODE_HINT.get(p, "未知模式")}' for p in pids)


def probe_usb_state(pid=None):
    """判断 USB 栈状态，比「能不能 open」更准。

    ok       : 枚举正常且控制传输有回应 —— 可以认证
    hung     : 能枚举、能 claim，但控制传输超时 —— 相机固件 USB 栈挂死
               （软件复位无效，必须拔插线/关开电源）
    no_driver: 打不开（libusb_open 返回 NOT_SUPPORTED）—— Windows 上通常是
               USBSTOR 把设备抢回去了，需要用 Zadig 重绑 libusbK/WinUSB
    absent   : 看不到设备
    """
    pid = pid or (SVC_PID if find(SVC_PID) is not None else MSC_PID)
    d = find(pid)
    if d is None:
        return 'absent'
    try:
        r = d.ctrl_transfer(0x80, 0x06, 0x0100, 0, 18, 2000)
        return 'ok' if len(r) >= 18 else 'hung'
    except NotImplementedError:
        # libusb_open 返回 LIBUSB_ERROR_NOT_SUPPORTED(-12) 时，pyusb 会包成
        # NotImplementedError —— 真实含义是「这个设备没绑到 libusb 驱动」，
        # Windows 上通常是 USBSTOR 把它抢回去了。
        return 'no_driver'
    except usb.core.USBError as e:
        # Windows: 13=EACCES(被占用)，-12=NOT_SUPPORTED
        if getattr(e, 'errno', None) in (13, -12):
            return 'no_driver'
        return 'hung'
    except Exception:
        return 'hung'


def connect_hardened(attempts=4, timeout=60, verbose=True, verify=True):
    """带自愈的连接：先判状态，再决定是复位还是直接报可执行的结论。

    比盲目重试强的地方：相机 USB 栈挂死时不会白等 4×60 秒，
    而是立刻告诉你「拔插线」。
    """
    global LAST_ERROR
    soft_done = False
    for i in range(1, attempts + 1):
        pids = present()
        if verbose:
            print(f'[连接 {i}/{attempts}] 可见: '
                  f'{["0x%04x" % p for p in pids] or "无"}')
        if not pids:
            others = [p for p in scan_sony() if p not in (MSC_PID, SVC_PID)]
            if others:
                LAST_ERROR = f'相机在，但 USB 模式不对：{mode_hint(others)}'
                if verbose:
                    print('  ' + LAST_ERROR)
                return None
            if wait_for(MSC_PID, 15) is None:
                LAST_ERROR = '未检测到相机（0x0D95 / 0x0336 都不在）'
                continue

        st = probe_usb_state()
        if verbose:
            print(f'  USB 状态: {st}')
        if st == 'no_driver':
            LAST_ERROR = (f'设备被非 libusb 驱动占用 → 用 Zadig 给 '
                          f'054C:{MSC_PID:04X} 装 WinUSB/libusbK')
            if verbose:
                print('  ' + LAST_ERROR)
            return None
        if st == 'hung':
            if not soft_done:
                soft_done = True
                if verbose:
                    print('  控制传输超时 → 复位 + clear_halt 后重试一次')
                reset_devices(verbose=verbose)
                continue
            LAST_ERROR = ('相机 USB 栈挂死：能枚举但不回控制传输，'
                          '软件复位无效 → 请拔插 USB 线（或关开相机电源）')
            if verbose:
                print('  ' + LAST_ERROR)
            return None

        try:
            j = connect(timeout=timeout, verbose=verbose)
            if j is None:
                LAST_ERROR = '0x0336 未出现（认证后没切到服务模式）'
            elif verify and not health(j):
                LAST_ERROR = '通道无响应（health 失败）'
                try:
                    j.stop()
                except Exception:
                    pass
            else:
                LAST_ERROR = ''
                if verbose:
                    print('  通道就绪')
                return j
        except Exception as e:
            LAST_ERROR = f'{type(e).__name__}: {e}'

        if verbose:
            print(f'  失败: {LAST_ERROR}')
        if i < attempts:
            if verbose:
                print('  → USB reset 后重试')
            reset_devices(verbose=verbose)
            timeout = min(timeout * 2, 180)
    if verbose:
        print(f'X 连接失败 {attempts} 次，最后一次: {LAST_ERROR}')
    return None
