"""涟漪测试 —— 双击/命令行运行的独立测试页

主程序内也有入口（工具栏 ✨ 涟漪测试），打包后同样可用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.ripple_test import main

if __name__ == "__main__":
    main()
