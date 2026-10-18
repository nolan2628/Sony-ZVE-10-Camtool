#!/usr/bin/env python3
"""
verify_dump.py - 重读只读分区，与本地 dump 比对 md5，确认 dump 完整性

只对未挂载/只读分区做校验，避免把动态分区（/tmp、/log）的差异误判为错误。
输出 manifest.txt
"""
import hashlib
import os
import time

from jiritsu_lib import connect

from camdata import BACKUP_DIR as OUTDIR  # 数据目录在 senser_probe/，见 camdata.py

# 静态（未挂载或只读挂载）分区 -> 说明
STATIC = {
    'nflasha4':  'MPR Flash Backup（未挂载，静态）',
    'nflasha5':  'WBI1 暖启动镜像（未挂载，静态）',
    'nflasha7':  'rootfs ext2（只读挂载）',
    'nflasha15': '/usr ext2（只读挂载）',
    'nflasha13': '更新历史（未挂载）',
    'nflasha16': '空白分区（未挂载）',
    'nflasha17': '空白 FAT（未挂载）',
}


def md5(b):
    return hashlib.md5(b).hexdigest()


def main():
    j = connect()
    if j is None:
        return

    lines = []
    print(f'\n[校验静态分区] {len(STATIC)} 个')
    for dev, note in STATIC.items():
        local = os.path.join(OUTDIR, f'{dev}.bin')
        if not os.path.exists(local):
            print(f'  - {dev}: 本地缺失')
            continue
        old = open(local, 'rb').read()
        t0 = time.time()
        resp, new = j.file_read(f'/dev/{dev}')
        dt = time.time() - t0
        if resp != 1:
            print(f'  X {dev}: resp={resp}')
            continue
        same = old == new
        print(f'  {"OK " if same else "差异"} {dev:10s} {len(old):>10d}B  '
              f'md5={md5(old)}  {"一致" if same else "不一致!"}  [{dt:.1f}s]  {note}')
        lines.append(f'{dev}\t{len(old)}\t{md5(old)}\t{"MATCH" if same else "DIFF"}\t{note}')

    with open(os.path.join(OUTDIR, 'manifest_static.txt'), 'w', encoding='utf-8') as f:
        f.write('# 静态分区 dump 校验清单\n')
        f.write('# dev\tsize\tmd5\tverify\tnote\n')
        f.write('\n'.join(lines) + '\n')
    print(f'\n清单已写入 {os.path.join(OUTDIR, "manifest_static.txt")}')

    j.stop()


if __name__ == '__main__':
    main()
