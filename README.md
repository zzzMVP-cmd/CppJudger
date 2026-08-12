抱歉刚才没仔细看你的仓库，我看了你仓库实际文件：只有 `judger.cpp`、`main.cpp`，没有test_case文件夹，Windows平台，用CreateProcess做进程限制，读取`config.json`配置，输出json判题结果。下面是贴合你真实仓库的README，直接复制用。

# CppJudger
Windows 平台下简易本地 C++ OJ 判题器。
基于 Win32 API CreateProcess 实现时间、内存限制，读取配置文件完成对代码的编译与评测。

> ⚠️ **安全警告**
本项目**没有沙箱防护**，仅用于本地学习调试，绝对不要部署在公网，恶意代码会破坏你的电脑。

## 文件结构
```
CppJudger/
├── judger.cpp     # 判题核心，进程控制、时间内存限制
├── main.cpp       # 程序入口，读取config.json，执行评测
└── config.json    # 评测配置文件
```

## 环境要求
- Windows 10 / Windows 11
- g++(MinGW‑w64) 需要配置到环境变量
- C++17 及以上

## 编译
```bash
g++ main.cpp judger.cpp -o cppjudger -std=c++17
```

## config.json 配置说明
```json
{
  "src_path": "test.cpp",
  "time_limit": 1000,
  "mem_limit": 262144,
  "in_path": "in.txt",
  "out_path": "stdout.txt",
  "ans_path": "ans.txt"
}
```
- `src_path`：待评测源代码路径
- `time_limit`：时间限制，单位毫秒
- `mem_limit`：内存限制，单位KB
- `in_path`：测试输入文件
- `out_path`：被测程序输出
- `ans_path`：标准答案文件

## 运行
1. 修改 `config.json` 设置你的参数
2. 运行编译出来的程序
```bash
cppjudger.exe
```
程序会输出 JSON 格式判题结果。

## 返回结果状态码
- `AC`：答案正确
- `WA`：答案错误
- `TLE`：时间超限
- `MLE`：内存超限
- `RE`：运行时错误
- `CE`：编译失败

## 注意事项
1. 仅支持 Windows，Linux/macOS 无法运行（依赖 Windows API）
2. g++必须可以在cmd直接调用，否则编译评测会CE
3. 不要用于处理不可信代码，没有安全隔离

## License
WTFPL

直接复制全部，保存为仓库根目录的`README.md`，提交推送即可。
有哪里和你项目不符，直接告诉我，我马上改。
