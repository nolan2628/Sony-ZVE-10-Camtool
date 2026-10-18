#!/usr/bin/env python3
"""
usb_recover.py - USB 通道复位/重连工具

用法:
  python usb_recover.py          # 复位当前设备并尝试重新认证
  python usb_recover.py --wait   # 等待 0x0336 出现（最长 120s）
"""
import sys
import time

import usb.core
import usb.util

from jiritsu_lib import MSC_PID, SVC_PID, find, libusb_backend, connect


def reset_dev(pid):
    dev = find(pid)
    if dev is None:
        return False
    print(f'  找到 PID 0x{pid:04x}，执行 USB 复位...')
    try:
        dev.reset()
        print('  reset OK')
    except Exception as e:
        print(f'  reset 失败: {type(e).__name__}: {e}')
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass
    time.sleep(2.0)
    return True


def main():
    print('[1] 复位所有已知 PID')
    for pid in (SVC_PID, MSC_PID):
        reset_dev(pid)

    print('\n[2] 重新认证')
    j = connect(timeout=60)
    if j is None:
        print('\nX 重连失败。请拔插 USB 线（或拔电池 30s）后重试。')
        return 1
    st, _ = j.jiritsu(0x400d)
    print(f'  GET_VERSION status = {("0x%08x" % st) if isinstance(st, int) else st}')
    j.stop()
    print('\nOK 通道已恢复')
    return 0


if __name__ == '__main__':
    sys.exit(main())
