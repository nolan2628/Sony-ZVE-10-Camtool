#!/usr/bin/env python3
"""
restore_flash.py - 用本地 dump 还原块设备（恢复工具）

用法:
  python restore_flash.py nflasha13            # 预览：比对本地与设备
  python restore_flash.py nflasha13 --confirm  # 执行写入并校验

安全措施:
  - 写入前先整读设备，与本地 dump 比对，报告差异字节数
  - 若设备当前内容与 dump 完全一致，视为幂等（不写或写完仍一致）
  - 写完整读回，md5 必须等于本地 dump 的 md5，否则报错
"""
import hashlib
import os
import sys
import time

from jiritsu_lib import connect

from camdata import BACKUP_DIR as OUTDIR  # 数据目录在 senser_probe/，见 camdata.py
# 禁止还原的分区（风险过高，需人工确认）
BLOCKED = {'nflasha5'}   # 暖启动镜像，写坏可能影响开机


def md5(b):
    return hashlib.md5(b).hexdigest()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    dev = sys.argv[1]
    confirm = '--confirm' in sys.argv

    if dev in BLOCKED:
        print(f'X {dev} 在禁止列表内（{BLOCKED}），需人工确认后再改脚本')
        return 1

    local = os.path.join(OUTDIR, f'{dev}.bin')
    if not os.path.exists(local):
        print(f'X 本地备份不存在: {local}')
        return 1
    blob = open(local, 'rb').read()
    print(f'本地备份: {local}  {len(blob)}B  md5={md5(blob)}')

    j = connect()
    if j is None:
        return 1

    print(f'\n[1] 整读 {dev} 比对')
    t0 = time.time()
    resp, cur = j.file_read(f'/dev/{dev}')
    if resp != 1 or not cur:
        print(f'  X 读取失败 resp={resp}')
        j.stop()
        return 1
    print(f'  设备: {len(cur)}B ({time.time()-t0:.1f}s)  md5={md5(cur)}')
    if len(cur) != len(blob):
        print('  X 大小不一致，中止（避免写坏）')
        j.stop()
        return 1
    if cur == blob:
        if '--force' not in sys.argv:
            print('  ⇒ 设备内容与备份完全一致，无需还原')
            j.stop()
            return 0
        print('  ⇒ 内容一致，但 --force 指定强制重写（用于验证写入路径）')
    else:
        diffs = [i for i in range(len(cur)) if cur[i] != blob[i]]
        print(f'  差异 {len(diffs)}B，首个 @ {diffs[0]:#x}')

    if not confirm:
        print('\n[预览] 加 --confirm 执行还原')
        j.stop()
        return 0

    print(f'\n[2] 写入 {len(blob)}B（{len(blob)//1048576 + 1} 个包）')
    t0 = time.time()
    resp, out = j.file_write(f'/dev/{dev}', blob)
    print(f'  resp={resp} ({time.time()-t0:.1f}s)')
    if resp != 1:
        print('  X 写入失败')
        j.stop()
        return 1

    print('\n[3] 整读回校验')
    resp, back = j.file_read(f'/dev/{dev}')
    ok = resp == 1 and back == blob
    print(f'  md5={md5(back)}  与备份一致: {ok}')

    j.stop()
    print('\n结论:', '还原成功' if ok else '还原后校验失败，请勿断电，重跑本脚本')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
