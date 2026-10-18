#!/usr/bin/env python3
"""build_payload_v2.py - 正确的最小 dlopen 载荷构建器

关键教训：`.dynamic` **必须放在可写段**。glibc 在 dlopen 时会往 dynamic
段写指针（l_info 表、DT_DEBUG 等），如果它落在 R+X 的只读映射里，
会直接 SIGSEGV —— 表现为「写 libJit.so 后服务整个消失」。

正确布局（与今天验证可用的 libJit_v9.so 一致）：
  ph0 PT_LOAD  R+X   : 0 .. code_end          （ELF 头 + 代码）
  ph1 PT_LOAD  R+W   : code_end .. +dyn_size  （.dynamic，vaddr 偏移一页）
  ph2 PT_DYNAMIC     : 指向 ph1
  构造函数用 DT_INIT（不是 INIT_ARRAY），入口带 Thumb 位（|1）
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'payload', 'jit_exec_v10.bin')
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'payload', 'libJit_exec.so')
PAD_TO = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0   # 可选：补零到指定大小
# 可选：代码模式。默认 thumb（DT_INIT 带 |1）；.arm 源码必须传 arm，
# 否则 CPU 会用 Thumb 解码 ARM 指令 -> 立即 SIGSEGV
MODE = (sys.argv[4] if len(sys.argv) > 4 else 'thumb').lower()
THUMB_BIT = 0 if MODE == 'arm' else 1

CODE = open(SRC, 'rb').read()

EHDR_SZ, PHDR_SZ, NPH = 52, 32, 3

hash_off = EHDR_SZ + NPH * PHDR_SZ            # 0x94
hash_tbl = struct.pack('<4I', 1, 1, 0, 0)     # nbucket=1 nchain=1
sym_off = hash_off + len(hash_tbl)
sym_tbl = b'\x00' * 16                        # 一个 null symbol
str_off = sym_off + len(sym_tbl)
str_tbl = b'\x00'
code_off = (str_off + len(str_tbl) + 3) & ~3
ctor_vaddr = code_off
seg1_end = (code_off + len(CODE) + 3) & ~3

dyn_size = 7 * 8                              # 6 个有效项 + DT_NULL
dyn_vaddr = seg1_end + 0x1000                 # 单独一页，保证与 off 同余

ehdr = b'\x7fELF' + bytes([1, 1, 1, 0]) + b'\x00' * 8
ehdr += struct.pack('<HHI', 3, 40, 1)         # ET_DYN, EM_ARM, EV_CURRENT
ehdr += struct.pack('<III', 0, EHDR_SZ, 0)    # entry=0（用 DT_INIT）
ehdr += struct.pack('<I', 0x05000200)         # EF_ARM_EABI_VER5 | soft-float
ehdr += struct.pack('<HHHHHH', EHDR_SZ, PHDR_SZ, NPH, 40, 0, 0)
assert len(ehdr) == EHDR_SZ

ph0 = struct.pack('<8I', 1, 0, 0, 0, seg1_end, seg1_end, 5, 0x1000)
ph1 = struct.pack('<8I', 1, seg1_end, dyn_vaddr, dyn_vaddr, dyn_size, dyn_size, 6, 0x1000)
ph2 = struct.pack('<8I', 2, seg1_end, dyn_vaddr, dyn_vaddr, dyn_size, dyn_size, 6, 4)


def d(tag, val):
    return struct.pack('<iI', tag, val)


dyn = (d(12, ctor_vaddr | THUMB_BIT)          # DT_INIT（thumb 需置 1）
       + d(4, hash_off)                       # DT_HASH
       + d(5, str_off)                        # DT_STRTAB
       + d(6, sym_off)                        # DT_SYMTAB
       + d(10, len(str_tbl))                  # DT_STRSZ
       + d(11, 16)                            # DT_SYMENT
       + d(0, 0))                             # DT_NULL
assert len(dyn) == dyn_size, (len(dyn), dyn_size)

out = bytearray()
out += ehdr + ph0 + ph1 + ph2
assert len(out) == hash_off
out += hash_tbl + sym_tbl + str_tbl
out += b'\x00' * (code_off - len(out))
out += CODE
out += b'\x00' * (seg1_end - len(out))
out += dyn
if PAD_TO and len(out) < PAD_TO:
    out += b'\x00' * (PAD_TO - len(out))

open(OUT, 'wb').write(bytes(out))
print(f'{OUT}  {len(out)}B')
print(f'  ctor(DT_INIT) = {ctor_vaddr:#x}|{THUMB_BIT} ({MODE})  代码 {len(CODE)}B')
print(f'  ph0 R+X 0..{seg1_end:#x}   ph1 R+W {dyn_vaddr:#x}..{dyn_vaddr+dyn_size:#x}')
