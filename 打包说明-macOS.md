# 按键精灵 · macOS 打包说明

## 先说结论：苹果包必须在 Mac 上打

PyInstaller 不支持交叉编译。Windows 上只能生成 `.exe`，生成不了 `.app` / `.dmg`；
反过来 macOS 也打不出 `.exe`。所以苹果版只能在一台 Mac 上执行打包
（Apple 芯片 M 系列、Intel 都能打，但打出来的包只适配打包时那台机器的架构）。

项目里已经准备好全部 macOS 打包脚本，拷到 Mac 上就能直接出安装包。

---

## 一、准备 Mac 环境

1. 安装 **Python 3.11+**（推荐 [python.org 官方安装包](https://www.python.org/downloads/macos/)——
   自带 `tkinter`，Homebrew 版常常缺这个库）。
2. 把整个项目文件夹拷到 Mac（U 盘 / 网盘 / 微信传文件都行）。
3. 打开「终端」，进到项目目录：

   ```bash
   cd ~/Desktop/按键精灵-1.7.1-Windows打包
   ```

---

## 二、一键打包

**方式 A：双击脚本**

1. 首次双击 `build/打包苹果版.command` 若提示「权限不足」，先在终端执行一次：

   ```bash
   chmod +x "build/打包苹果版.command"
   ```

2. 之后双击即可，脚本会自动装依赖、打包主程序、生成 dmg。

**方式 B：终端两条命令**

```bash
python3 build/build.py          # 生成 dist/按键精灵.app
python3 build/make_macos.py     # 生成 dist/按键精灵-<版本>-macOS.dmg
```

依赖（PyInstaller / pynput / pillow）由 `build/build.py` 自动安装，无需手动 pip。

---

## 三、产物

| 文件 | 用途 |
|---|---|
| `dist/按键精灵.app` | 应用本体，直接拖进「应用程序」即可运行 |
| `dist/按键精灵-1.7.1-macOS.dmg` | 对外分发的安装包（含应用 + Applications 快捷方式） |

dmg 体积通常在 20～30 MB 之间（与 Windows 版接近）。

---

## 四、用户安装步骤

1. 双击 `按键精灵-1.7.1-macOS.dmg`，把「按键精灵」拖进「应用程序」。
2. **首次打开请右键 →「打开」**。包未做 Apple 公证，直接双击会被 Gatekeeper 拦一次。
3. 首次录制前，到 `系统设置 → 隐私与安全性 → 辅助功能` 里勾选「按键精灵」
   （并在「输入监控」里同样授权），否则热键与录制不会生效。

---

## 五、macOS 与 Windows 版的功能差异

| 功能 | macOS 表现 |
|---|---|
| 录制 / 回放 | 可用，需「辅助功能」+「输入监控」授权 |
| 全局热键（F8/F9/F10/F11） | 可用，同样依赖辅助功能授权 |
| 回放前聚焦目标窗口 | 走 AppleScript `activate`，可用 |
| 顶部迷你控制条（HUD） | 置顶显示可用；点击不抢焦点等细节由窗口管理器决定 |
| 录制涟漪特效 | 自动关闭（依赖 Windows 的键控镂空，macOS 无实现） |
| 屏幕缩放(DPI)处理 | macOS 自带 Retina 适配，无需处理 |

---

## 六、常见问题

**1) `python3 -c "import tkinter"` 报错**
用的是 Homebrew 的 Python 且没装 Tk。改用 python.org 官方包，或执行
`brew install python-tk@3.12`（版本号按实际改）。

**2) 打包时报 `Icon file must be in .icns format`**
`assets/icon.icns` 缺失。本仓库已带该文件；若被删掉，用系统「预览/图标工坊」
把 `assets/icon.png` 转成 `icon.icns` 放回原处即可（缺图标也能打包，只是用默认图标）。

**3) 打出的包装到别的 Mac 上打不开**
- 未公证：右键 →「打开」，或在 `系统设置 → 隐私与安全性` 里点「仍要打开」。
- 架构不符：Apple 芯片上打的包默认 arm64，Intel 机器需在 Intel 机器上重打。
  也可以在 Intel 机器上执行 `python3 build/build.py` 生成 x86_64 版本，或
  安装两个 Python 后分别打包。

**4) 想要正式签名 / 公证**
需要 Apple Developer 账号（$99/年）。拿到证书后把 `build/make_macos.py` 里的
ad-hoc 签名换成：

```bash
codesign --force --deep --options runtime \
  --sign "Developer ID Application: 你的名字 (TEAMID)" "dist/按键精灵.app"
xcrun notarytool submit "dist/按键精灵-1.7.1-macOS.dmg" \
  --apple-id 你的邮箱 --team-id TEAMID --password 应用专用密码 --wait
xcrun stapler staple "dist/按键精灵-1.7.1-macOS.dmg"
```

不签名也能用，只是每个用户第一次要手动放行一次。

---

## 七、需要我帮忙的话

如果你想在 Windows 上拿到 mac 包，可选路线只有两条：

1. 找一台 Mac（或让同事帮忙）跑上面的脚本，5 分钟出包；
2. 用云端 macOS 构建（GitHub Actions 的 `macos-latest` 免费额度），
   把项目推到仓库后由 CI 生成 dmg 并作为构建产物下载。

需要我把 GitHub Actions 的 workflow 也写好吗？告诉我仓库地址或直接说「要」。
