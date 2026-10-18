#!/usr/bin/env python3
"""camdata.py - 统一解析「代码目录」与「数据目录」

代码放在 camtool/（工具箱本体），数据放在 senser_probe/（历史备份、证据、缓存）。
两者分开，是为了让工具箱可以整体拷走而不带走 1.5GB 备份。

覆盖方式：
  export CAM_DATA_ROOT=D:/某个目录
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

DATA_ROOT = os.environ.get('CAM_DATA_ROOT') or os.path.join(
    os.path.dirname(HERE), 'senser_probe')
if not os.path.isdir(DATA_ROOT):
    # 独立 clone 的场景：没有同级 senser_probe，就在代码旁边建 data/（已 gitignore）
    DATA_ROOT = os.path.join(HERE, 'data')

BACKUP_DIR = os.path.join(DATA_ROOT, 'backup_full')
EVIDENCE_DIR = os.path.join(DATA_ROOT, 'evidence')
STATE_FILE = os.path.join(DATA_ROOT, '.cam_state.json')
PAYLOAD_DIR = os.path.join(HERE, 'payload')
