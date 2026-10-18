#!/usr/bin/env python3
"""
cleanup_jiritsu.py - 清理我们在相机上留下的测试文件

流程: 先探测存在性 → 只删我们创建的文件 → 删除后复查
安全: 白名单硬编码，绝不触碰 TMMC.JIT / DIAC.csv 等固件文件
用法: python cleanup_jiritsu.py --dry   仅探测
      python cleanup_jiritsu.py         执行删除
"""
import sys
import time

from jiritsu_lib import connect

# 只删我们自己创建的（白名单）
WHITELIST = [
    '/tmp/jiritsu/zzz_fmt.txt',
    '/tmp/jiritsu/zzz_rce_proof.txt',
    '/tmp/jiritsu/zzz_uid_proof.txt',
    '/tmp/jiritsu/zzz_sysinfo.txt',
    '/tmp/jiritsu/zzz_big.bin',
    '/tmp/jiritsu/zzz_wtest.txt',
    '/tmp/jiritsu/jbusy',
    '/tmp/jiritsu/jmode',
    '/tmp/jbusy',
    '/tmp/jmode',
    '/tmp/jiritsu/libJit.so',      # 我们写入的 payload
]

# 明确保护，永不删除
PROTECT = {
    '/tmp/jiritsu/tmmc.jit', '/tmp/jiritsu/DIAC.csv',
    '/tmp/jiritsu/jit.log', '/tmp/jiritsu/history.txt', '/tmp/jiritsu/ver.txt',
}


def exists(j, path):
    try:
        resp, data = j.file_read(path)
        return resp == 1, len(data)
    except Exception:
        return False, 0


def main():
    dry = '--dry' in sys.argv
    j = connect()
    if j is None:
        return 1

    print('\n[1] 探测白名单文件')
    present = []
    for p in WHITELIST:
        assert p not in PROTECT, f'白名单里混入保护文件: {p}'
        ok, n = exists(j, p)
        print(f'  {"存在" if ok else "不存在"}  {p:36s}' + (f' {n}B' if ok else ''))
        if ok:
            present.append(p)

    if not present:
        print('\n没有需要清理的文件')
        j.stop()
        return 0

    if dry:
        print(f'\n[--dry] 将删除 {len(present)} 个文件，未执行')
        j.stop()
        return 0

    print(f'\n[2] 删除 {len(present)} 个文件')
    for p in present:
        try:
            st, _ = j.delete_file(p)
            ok = isinstance(st, int) and st == 0
            print(f'  {"OK" if ok else "失败"}  {p:36s} status='
                  f'{("0x%08x" % st) if isinstance(st, int) else st}')
        except Exception as e:
            print(f'  EXCEPTION {p}: {type(e).__name__}: {e}')
        time.sleep(0.2)

    print('\n[3] 复查')
    left = []
    for p in present:
        ok, n = exists(j, p)
        print(f'  {"仍存在!" if ok else "已删除"}  {p}')
        if ok:
            left.append(p)

    print('\n[4] 保护文件状态（应保持不变）')
    for p in sorted(PROTECT):
        ok, n = exists(j, p)
        print(f'  {"存在" if ok else "不存在"}  {p:32s}' + (f' {n}B' if ok else ''))

    j.stop()
    return 1 if left else 0


if __name__ == '__main__':
    sys.exit(main())
