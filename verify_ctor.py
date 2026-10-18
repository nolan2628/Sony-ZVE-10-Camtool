#!/usr/bin/env python3
"""verify_ctor.py - 最小验证：部署一个已知可用的载荷，看 ctor 是否执行。

用法:
  python verify_ctor.py                       # 默认用 payload/libJit_ctor_v2.so
  python verify_ctor.py <so> <marker>         # 自定义载荷与期望产物

判定:
  - write(16) 返回 0 且 marker 出现  -> 服务正常，ctor 能跑
  - write(16) 抛异常 / 通道断        -> dlopen 阶段崩溃（im.elf 死 → 相机会自己重启，
                                        等 60s 再连；不是必须拔电池）

⚠️ 不要用 payload/libJit.so（528B，10-17 的旧产物）：它的 .dynamic 落在只读段，
   dlopen 会 SIGSEGV 打死 im.elf。要用 build_payload_v2.py 重建过的载荷。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cam

DEFAULT_SO = os.path.join(HERE, 'payload', 'libJit_ctor_v2.so')
DEFAULT_MARKER = '/tmp/jiritsu/RCE_MARK.txt'


def main():
    so = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SO
    marker = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MARKER

    j = cam.C()
    st, _ = j.jiritsu(0x4002)
    print(f'MOUNT -> {hex(st) if isinstance(st, int) else st}')
    time.sleep(0.5)

    blob = open(so, 'rb').read()
    print(f'部署 {so} ({len(blob)}B)')
    j.select('/tmp/jiritsu/libJit.so')
    try:
        st, _ = j.jiritsu(16, b'\0\xff\0' + blob, retries=0)
        print(f'write(16) -> {hex(st) if isinstance(st, int) else st}')
    except Exception as e:
        print(f'X 通道在 dlopen 阶段断开: {type(e).__name__}')
        print('  => 服务侧动态链接器崩溃。im.elf 死后相机通常会自动重启，')
        print('     等 60s 再 cam.py wait；若 3 分钟还不回来，才需要真断电')
        print('     （ZV-E10 可由 USB 供电，光拔电池不断电，要先拔 USB 线）')
        return 2

    time.sleep(1.5)
    r, d = cam.read(marker)
    print(f'{marker} resp={r} len={len(d) if d else 0}')
    if r == 1:
        print('OK 构造函数已执行')
        return 0
    print('! 构造函数没有产出（dlopen 成功但 ctor 没跑，或写路径不对）')
    return 1


if __name__ == '__main__':
    sys.exit(main())
