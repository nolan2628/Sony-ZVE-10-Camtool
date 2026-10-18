#!/usr/bin/env python3
"""build_exec_payload.py - 把一段 ARM/Thumb 裸代码包成可 dlopen 的最小 .so

⚠️ 历史教训：这个脚本以前自带一套「单 PT_LOAD + DT_INIT_ARRAY」的 ELF 骨架，
   `.dynamic` 落在只读段 → glibc dlopen 写 l_info 时 SIGSEGV → im.elf 当场死。
   现在它只是 build_payload_v2.py 的薄包装，骨架唯一来源就是 v2。

用法:
  python build_exec_payload.py                       # jit_exec_v11.bin -> libJit_exec.so
  python build_exec_payload.py <src.bin> <out.so> [arm|thumb]
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'payload', 'jit_exec_v11.bin')
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'payload', 'libJit_exec.so')
MODE = sys.argv[3] if len(sys.argv) > 3 else 'thumb'

cmd = [sys.executable, os.path.join(HERE, 'build_payload_v2.py'), SRC, OUT, '0', MODE]
print('+ ' + ' '.join(os.path.basename(c) for c in cmd))
sys.exit(subprocess.call(cmd))
