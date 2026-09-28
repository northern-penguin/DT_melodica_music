# DTmusic 三角洲口琴曲谱导入与演奏

## Windows 程序包

下载 Release 附件 `DTmusic-Windows.zip`，解压后运行 `DTmusic/DTmusic.exe`。程序包自带 Python 运行时、Tesseract、PDF 读取组件，以及带 Java 运行时的 [Audiveris 5.11.0](https://github.com/Audiveris/audiveris/releases/tag/5.11.0)，无需另装 Conda、Python 或 Java。

如果要从源码运行，请在项目目录运行 `运行DTmusic.bat`，或执行：

```powershell
conda run -n DTmusic python dtmusic_gui.py
```

选择歌曲后点“准备演奏”，或双击曲名，然后切换到游戏窗口：默认 **F6** 单次演奏、**F7** 启动或切换循环、**F8** 停止。窗口可选单曲、顺序或随机循环；选中歌单时只在该歌单循环，否则在整个曲库循环。搜索仅筛选曲库显示，不改变正在播放的队列。三个热键都可在“热键设置”中改成不同的 F1–F12。

右键曲名可以删除、修改、打开演奏 JSON 或加入歌单。修改窗口提供逐音表格和曲名、BPM、拍号、调号字段。导入曲目删除后进入 `data/trash`；内置曲目删除后只从当前曲库隐藏。用户曲谱、设置、歌单和日志保存在程序所在目录的 `data/` 下。首次运行会校验并迁移旧 `%LOCALAPPDATA%\DTmusic` 中的已知文件，复制确认后清理原件；被占用的文件留在原处并显示失败原因。

## 导入曲谱

窗口中选择清晰印刷体 PNG/JPG/PDF 简谱、TXT 简谱，或 MusicXML/XML/MXL，填写曲名、拍号、BPM 与调号，点“识别并加入曲库”。有多条五线谱声部时，选择要演奏的旋律。导入结果及识别疑点会显示在窗口底部；请先核对结果，再选曲并点“准备演奏”。五线谱图片由 Audiveris 离线转换为 MusicXML；若要使用自己安装的版本，可用 `DTMUSIC_AUDIVERIS` 指定其 `Audiveris.exe` 完整路径。

Audiveris 以独立进程运行，原版程序文件位于包内 `_internal/tools/audiveris`。其 AGPLv3 许可、同版本源码包及来源说明位于 `licenses`，Java 运行时的许可文本保留在 Audiveris 的 `runtime/legal` 目录。重新分发时请连同这些材料一起提供。

若要核对解压后的独立包，可在 PowerShell 中运行 `DTmusic/DTmusic.exe --self-test`，再查看 `DTmusic/data/selftest.json`。其中 `builtin_songs`、`tesseract`、`pdf`、`tk`、`audiveris` 应为 `true`。请将程序解压到可写目录，以便保存曲谱和设置。

图片识谱只针对清晰印刷体。简谱图片的数字、音区点、时值横线可能识错，程序会显示核对提示；遇到无法辨认的时值暂按一拍。确认无可用旋律或旋律超出口琴音域时不会生成 JSON。导入文件位于程序旁的 `data/library`。

## 开发与构建

在已安装 Conda 的 Windows PowerShell 中执行：

```powershell
conda env create -f environment.yml
conda run -n DTmusic python -m unittest discover -s tests -v
conda run -n DTmusic python dtmusic_gui.py
./build.ps1
```

`build.ps1` 默认查找 `D:\ProgramData\anaconda3\Scripts\conda.exe`；其他位置可通过 `-Conda` 指定。构建产物位于 `dist/DTmusic-Windows.zip`。源码命令行用法见下文。

TXT 简谱以空格分隔音符，`|` 分小节。`1=C` 指定调号；`1` 是一拍、`1_` 是半拍、`1__` 是四分之一拍、`1.` 是附点一拍、`1/2` 是两拍、`0` 是休止、`#4` 升半音、`5+`/`5-` 是高/低八度。示例：

```text
1=C
| 1 1 5 5 | 6 6 5/2 |
```

当简谱没有明确时值记号时，程序按一拍保存并提示。拍号用于检查小节时值，BPM 决定整首演奏速度。MusicXML 保留原谱音高和时值；若整首移动整数个八度仍无法落入口琴音域，导入会报错。

## 原命令行播放器

Windows + Python 3.10 或更新版本，无须安装第三方包。脚本按曲谱向当前前台窗口发送普通键盘和鼠标输入，支持单旋律；不读取或修改游戏文件。

## 使用

在此目录打开 PowerShell：

```powershell
python harmonica.py
```

选择曲目后，切换到游戏并拿出口琴，使用设置中的开始、循环、停止热键。演奏中若前台窗口改变，脚本会停止并释放它按住的键。

其他命令：

```powershell
python harmonica.py --list
python harmonica.py --song 2
python harmonica.py --file example-song.json
python harmonica.py --file example-song.json --song 1
python harmonica.py --playlist 练习 --loop-mode random
```

内置第 4 首是根据你提供的五线谱截图修正的加木《两难》副歌首遍，可用 `python harmonica.py --song 4` 选中。采用谱面标出的 C 调、4/4、96 BPM；连音合并为一个持续音。截图在第二遍副歌结束前被截断，因此这里只录入可完整核对的第一遍。谱中最低音低于游戏口琴音域，整段旋律统一升高一个八度，保留相对音程。

**注意：**开始或循环热键作用于按下时的前台窗口。请确认已进入口琴演奏界面。若游戏未响应模拟输入，脚本无法保证兼容，也不提供绕过反作弊的方法。

## 自定义曲谱

复制 `example-song.json`，修改歌曲名称、速度和音符。单首曲谱格式如下：

```json
{
  "name": "我的歌曲",
  "bpm": 100,
  "notes": [["1", 1], ["2", 0.5], ["#4", 0.5], ["1+", 2], ["-", 1]]
}
```

也可把多首这样的对象放在一个 JSON 数组中，当作自定义曲库。`bpm` 范围为 20–300；每个音符的拍数须大于 0、且不超过 16。启动时会指出无效曲谱的具体位置。

| 记号 | 含义 | 输入示例 |
| --- | --- | --- |
| `1`–`7` | 中音 do–xi | `1` → Z |
| `#` 前缀 | 升半音，按住鼠标中键 | `#4` → 中键 + V |
| `-` 后缀 | 降八度，按住鼠标左键 | `5-` → 左键 + B |
| `+` 后缀 | 升八度，按住鼠标右键 | `3+` → 右键 + C |
| `1+` | 高音 do，直接按逗号键 | `1+` → `,` |
| 单独的 `-` | 休止符 | `["-", 1]` → 休止一拍 |

修饰记号可以组合，例如 `#2-` 是左键 + 中键 + X，`#1+` 是中键 + 逗号。为避免音符黏连，每个音符末尾会留最多 40 毫秒的短间隙。当前曲谱只支持一个时间点发出一个音，不能表示和弦。

## 本地验证

```powershell
python -m unittest discover -s tests -v
```

这些测试只使用模拟输入记录器，不会发送真实键鼠事件。实际音高、节奏和游戏是否接收输入，仍需你在游戏中核对。

## 使用风险

Garena《三角洲行动》的[官方禁用软件清单](https://deltaforce.garena.com/en/news/all/RD6289)明确将可模拟按键的 Python 等自动脚本列为禁用项。在游戏运行时使用可能导致账号处罚。本程序不提供规避检测、后台输入或绕过反作弊的功能。
