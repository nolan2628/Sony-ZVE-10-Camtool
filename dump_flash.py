#!/usr/bin/env python3
"""
dump_flash.py - 通过 FileControl(0xff01) 整段 dump 块设备

用法:
  python dump_flash.py                 # 默认关键分区
  python dump_flash.py nflasha7 nflasha6
  python dump_flash.py --all           # 全部已知分区
  python dump_flash.py --mbr           # 只取 /dev/nflasha 前 8MB（分区表/引导区）

输出目录: backup_full/
"""
import hashlib
import os
import sys
import time

from jiritsu_lib import connect

from camdata import BACKUP_DIR as OUTDIR  # 数据目录在 senser_probe/，见 camdata.py

# 设备 -> 用途备注
KNOWN = {
    'nflasha1':  'vfat 8MB (mkfs.fat)',
    'nflasha2':  '/setting vfat 20MB',
    'nflasha3':  '/system  vfat 48MB',
    'nflasha4':  'MPR Flash Backup 12MB',
    'nflasha5':  'WBI1 warm-boot 60MB',
    'nflasha6':  '/tmp/jiritsu vfat 16MB',
    'nflasha7':  '/ rootfs ext2 8MB',
    'nflasha10': '/tmp + /etc vfat 12MB',
    'nflasha11': '/log vfat 500MB',
    'nflasha12': '/cert vfat',
    'nflasha15': '/usr ext2',
    'nflasha18': '/lens vfat',
}

DEFAULT = ['nflasha4', 'nflasha6', 'nflasha7', 'nflasha15']


def dump_one(j, dev, note=''):
    path = f'/dev/{dev}'
    t0 = time.time()
    resp, data = j.file_read(path)
    dt = time.time() - t0
    if resp != 1 or not data:
        print(f'  X {path}: resp={resp} len={len(data)}')
        return None
    out = os.path.join(OUTDIR, f'{dev}.bin')
    with open(out, 'wb') as f:
        f.write(data)
    md5 = hashlib.md5(data).hexdigest()
    mb = len(data) / 1048576
    print(f'  OK {path:20s} {len(data):>10d}B ({mb:6.1f}MB) {dt:5.1f}s  '
          f'md5={md5}  {note}')
    print(f'     -> {out}')
    # 顺带识别文件系统
    magic = ''
    if len(data) > 1082:
        if data[1080:1082] == b'\x53\xef':
            magic = 'ext2/3/4 超级块 @1024'
    if data[:3] == b'\xeb\x3c\x90' or data[54:59] == b'FAT16':
        magic = (magic + ' + FAT').strip()
    if data[:4] == b'WBI1':
        magic = (magic + ' + WBI1').strip()
    if magic:
        print(f'     fs: {magic}')
    return data


def main():
    args = [a for a in sys.argv[1:]]
    os.makedirs(OUTDIR, exist_ok=True)

    j = connect()
    if j is None:
        return

    try:
        if '--mbr' in args:
            print('\n[MBR] /dev/nflasha 前 8MB（分区表 / 引导区）')
            t0 = time.time()
            # FileControl 无偏移，只能整读；先用 /dev/nflasha 全量太大，
            # 改为读分区表所在的小分区或依赖已知分区列表
            resp, data = j.file_read('/dev/nflasha')
            if resp == 1 and data:
                head = data[:8 * 1048576]
                out = os.path.join(OUTDIR, 'nflasha_head8m.bin')
                with open(out, 'wb') as f:
                    f.write(head)
                print(f'  /dev/nflasha 全长 {len(data)}B，已保存前 8MB')
                print(f'     md5(head8m)={hashlib.md5(head).hexdigest()}')
                # 解析 MBR/GPT 线索
                if data[510:512] == b'\x55\xaa':
                    print('     发现 MBR 签名 55AA')
                    for i in range(4):
                        e = data[446 + 16 * i: 446 + 16 * (i + 1)]
                        if any(e):
                            print(f'     分区项{i}: {e.hex()}')
                if data[0:8] == b'EFI PART' or b'EFI PART' in data[:1024]:
                    print('     发现 GPT 签名')
            else:
                print(f'  X resp={resp} len={len(data)}')
            return

        if '--all' in args:
            targets = list(KNOWN.keys())
        elif args:
            targets = args
        else:
            targets = DEFAULT

        print(f'\n[Dump] {len(targets)} 个分区 -> {OUTDIR}')
        for dev in targets:
            try:
                dump_one(j, dev, KNOWN.get(dev, ''))
            except Exception as e:
                print(f'  EXCEPTION {dev}: {type(e).__name__}: {e}')
    finally:
        j.stop()


if __name__ == '__main__':
    main()
