#!/usr/bin/env python3
"""一条连接内把 cam.py 各子命令跑一遍，验证功能与异常处理。

用途：每次改完工具，跑这个做回归，而不是一条命令建一次连接。
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cam


CASES = [
    ('md5 /version.txt', lambda: cam.cmd_md5('/version.txt')),
    ('hex /version.txt 0 64', lambda: cam.cmd_hex('/version.txt', 0, 64)),
    ('grep 30110 /version.txt', lambda: cam.cmd_grep('30110', '/version.txt')),
    ('tail /version.txt 200', lambda: cam.cmd_tail('/version.txt', 200)),
    ('lsr /tmp/jiritsu', lambda: cam.cmd_lsr('/tmp/jiritsu', None, 30)),
    ('irq liro', lambda: cam.cmd_irq('liro')),
    ('dev liro', lambda: cam.cmd_dev('liro')),
    ('sysfs 读', lambda: cam.cmd_sysfs('/sys/module/liro/parameters/debug')),
    ('diff Backup.bin/.bak', lambda: cam.cmd_diff('/setting/Backup.bin',
                                                  '/setting/Backup.bak')),
    ('setting --backup', lambda: cam.cmd_setting(['--backup'])),
    ('jir GET_VERSION', lambda: cam.cmd_jir('0x400d', '', True, False)),
    ('raw 0xff01 读', lambda: cam.cmd_raw('0xff01', '0200' + '0000', True, False)),
    ('ps', lambda: cam.cmd_ps()),
    ('mem', lambda: cam.cmd_mem()),
    ('sd', lambda: cam.cmd_sd()),
    ('df', lambda: cam.cmd_df()),
]


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    ok = fail = 0
    for name, fn in CASES:
        if only and only not in name:
            continue
        print(f'\n{"="*70}\n### {name}\n{"="*70}')
        try:
            fn()
            ok += 1
        except SystemExit as e:
            print(f'  [SystemExit {e.code}]')
            fail += 1
        except Exception:
            traceback.print_exc()
            fail += 1
    print(f'\n结果: 通过 {ok} / 失败 {fail}')
    try:
        cam._release()
    except Exception:
        pass


if __name__ == '__main__':
    main()
