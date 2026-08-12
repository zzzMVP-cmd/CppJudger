# CppJudger
C++实现的简易OJ判题器，用于编译、运行评测用户提交的C++代码，输出判题结果。

## ✨ 功能
- C++代码编译，捕获编译错误 CE
- 时间限制 TLE、内存限制 MLE 检测
- 标准输出比对，判断答案正确 AC / 答案错误 WA
- 捕获程序运行时异常 RE
- 输出判题JSON结果

## ⚠️ 警告
**本项目没有强沙箱隔离，不要直接对外公网部署！仅用于本地学习测试，恶意代码会破坏系统。**

## 🛠️ 环境依赖
- Windows / Linux
- g++ 编译器
- C++17 及以上标准

## 🚀 使用方法

### 克隆仓库
```bash
git clone https://github.com/zzzMVP-cmd/CppJudger.git
cd CppJudger
```

### 编译
```bash
g++ main.cpp -o judger -std=c++17

### 运行判题器
```bash
./judger
```

## 📁 项目结构
```
CppJudger
├── main.cpp        # 判题核心逻辑
├── test_case       # 测试用例文件夹
│   ├── in.txt      # 输入样例
│   └── out.txt     # 标准输出
└── README.md
```

## 📝 判题结果说明
| 结果 | 含义 |
|------|------|
| AC | 答案正确 |
| WA | 答案错误 |
| TLE | 超时 |
| MLE | 内存超限 |
| RE | 运行时错误 |
| CE | 编译失败 |

## 📌 开发说明
本项目为学习用途，用于理解OJ判题底层原理。
欢迎Issue、PR。

直接全选复制，在你的仓库根目录新建 `README.md`，粘贴保存。
然后提交推送上去，仓库主页就显示这份文档。

如果你要改内容，告诉我，我直接帮你改好。
