#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C++ Judger - 本地测评工具
- GUI 选择 C++ 源文件、测评点文件，设置时间/空间限制与 C++ 标准版本
- 每次评测重新读取并编译源文件，编译失败完整反馈报错信息
- 依次运行各测评点，反馈 AC / WA / TLE / MLE / RE 及运行时间与空间
- 使用 Windows Job Object 监控内存并强制终止超限进程，无需 psutil
- 支持深色/浅色模式切换，默认深色
- 测评结果以折叠框展示，默认折叠，展开可查看输入/预期输出/输出/差异比较
"""

import os
import sys
import time
import locale
import tempfile
import threading
import subprocess
import ctypes
from ctypes import wintypes
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import shutil
import queue
import difflib

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ============================ Windows Job Object ============================

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

SIZE_T = ctypes.c_size_t
ULONG_PTR = ctypes.c_size_t
ULONG64 = ctypes.c_uint64
LARGE_INTEGER = ctypes.c_longlong


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ULONG64),
        ("WriteOperationCount", ULONG64),
        ("OtherOperationCount", ULONG64),
        ("ReadTransferCount", ULONG64),
        ("WriteTransferCount", ULONG64),
        ("OtherTransferCount", ULONG64),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", LARGE_INTEGER),
        ("PerJobUserTimeLimit", LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ULONG_PTR),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
CREATE_NO_WINDOW = 0x08000000

kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.QueryInformationJobObject.restype = wintypes.BOOL
kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
kernel32.TerminateJobObject.restype = wintypes.BOOL
kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryPerformanceCounter.argtypes = [ctypes.POINTER(LARGE_INTEGER)]
kernel32.QueryPerformanceCounter.restype = wintypes.BOOL
kernel32.QueryPerformanceFrequency.argtypes = [ctypes.POINTER(LARGE_INTEGER)]
kernel32.QueryPerformanceFrequency.restype = wintypes.BOOL


def _qpc_freq():
    freq = LARGE_INTEGER()
    kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
    return freq.value


_QPC_FREQ = _qpc_freq()


def qpc_ms():
    counter = LARGE_INTEGER()
    kernel32.QueryPerformanceCounter(ctypes.byref(counter))
    return counter.value * 1000.0 / _QPC_FREQ


def create_job():
    hJob = kernel32.CreateJobObjectW(None, None)
    if not hJob:
        raise ctypes.WinError(ctypes.get_last_error())
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        hJob, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return hJob


def query_peak_memory(hJob):
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    ret = wintypes.DWORD(0)
    ok = kernel32.QueryInformationJobObject(
        hJob, JobObjectExtendedLimitInformation, ctypes.byref(info),
        ctypes.sizeof(info), ctypes.byref(ret)
    )
    return int(info.PeakProcessMemoryUsed) if ok else 0


EXIT_CODE_MAP = {
    0xC0000005: "段错误 / 非法内存访问",
    0xC0000094: "整数除以零",
    0xC00000FD: "栈溢出",
    0xC0000008: "数据未对齐",
    0xC000001D: "非法指令（常见于整数除零、未定义行为）",
    0xC0000096: "特权指令",
    0xC000013A: "被 Ctrl+C 中断",
    0xC0000135: "找不到 DLL",
    0xC0000142: "DLL 初始化失败",
    0xC0000022: "访问被拒绝",
    0xC000012F: "可执行文件格式错误",
}


def decode_exit_code(code):
    if code == 0:
        return ""
    if code in EXIT_CODE_MAP:
        return f"RE ({EXIT_CODE_MAP[code]})"
    if 0xC0000000 <= code <= 0xCFFFFFFF:
        return f"RE (NTSTATUS: 0x{code:08X})"
    return f"RE (退出码: {code})"


# ============================ 编译 ============================

DEFAULT_GPP = r"D:\zzzMVP\MinGW64\bin\g++.exe"


def compile_cpp(cpp_path, std_flag, gpp_path, out_exe):
    cmd = [gpp_path, f"-std={std_flag}", "-O2", "-w", "-o", out_exe, cpp_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError:
        return 1, "", f"找不到编译器：{gpp_path}\n请确认编译器路径是否正确。"
    except subprocess.TimeoutExpired:
        return 1, "", "编译超时（超过 120 秒）。"
    enc = locale.getpreferredencoding(False) or "utf-8"
    out = proc.stdout.decode(enc, errors="replace")
    err = proc.stderr.decode(enc, errors="replace")
    return proc.returncode, out, err


# ============================ 运行单个测试点 ============================

def run_test(exe_path, input_path, time_limit_ms, mem_limit_mb, output_limit_lines=0):
    mem_limit_bytes = mem_limit_mb * 1024 * 1024
    hard_time_ms = time_limit_ms * 1.2
    hard_mem_bytes = int(mem_limit_bytes * 1.2)
    hard_output_lines = int(output_limit_lines * 1.2) if output_limit_lines > 0 else 0

    result = {
        "status": None,
        "time_ms": 0,
        "mem_kb": 0,
        "output": b"",
        "exit_code": 0,
        "re_detail": "",
    }

    hJob = create_job()
    try:
        fin = open(input_path, "rb")
    except Exception as e:
        result["status"] = "RE"
        result["re_detail"] = f"无法读取输入文件：{e}"
        result["output"] = result["re_detail"].encode("utf-8")
        kernel32.CloseHandle(hJob)
        return result

    try:
        proc = subprocess.Popen(
            [exe_path],
            stdin=fin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        fin.close()
        result["status"] = "RE"
        result["re_detail"] = f"无法启动程序：{e}"
        result["output"] = result["re_detail"].encode("utf-8")
        kernel32.CloseHandle(hJob)
        return result
    finally:
        fin.close()

    hProc = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
    if hProc:
        kernel32.AssignProcessToJobObject(hJob, hProc)
        kernel32.CloseHandle(hProc)

    stdout_chunks = []
    output_line_count = 0
    killed_by_output = False

    def reader():
        nonlocal output_line_count, killed_by_output
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                stdout_chunks.append(chunk)
                if hard_output_lines > 0:
                    output_line_count += chunk.count(b'\n')
                    if output_line_count > hard_output_lines:
                        killed_by_output = True
                        kernel32.TerminateJobObject(hJob, 1)
                        break
        except Exception:
            pass

    t_reader = threading.Thread(target=reader, daemon=True)
    t_reader.start()

    start = qpc_ms()
    killed_by_time = False
    killed_by_mem = False

    while proc.poll() is None:
        if killed_by_output:
            break
        elapsed_ms = qpc_ms() - start
        if elapsed_ms > hard_time_ms:
            kernel32.TerminateJobObject(hJob, 1)
            killed_by_time = True
            break
        peak = query_peak_memory(hJob)
        if peak > hard_mem_bytes:
            kernel32.TerminateJobObject(hJob, 1)
            killed_by_mem = True
            break
        time.sleep(0.005)

    proc.wait()
    t_reader.join(timeout=3)
    elapsed_ms = qpc_ms() - start
    peak = query_peak_memory(hJob)

    result["time_ms"] = int(elapsed_ms)
    result["mem_kb"] = int(peak // 1024)
    result["exit_code"] = proc.returncode if proc.returncode is not None else -1
    result["output"] = b"".join(stdout_chunks)

    if killed_by_time:
        result["status"] = "TLE"
    elif killed_by_mem:
        result["status"] = "MLE"
    elif killed_by_output:
        result["status"] = "OLE"
    elif proc.returncode != 0:
        result["status"] = "RE"
        result["re_detail"] = decode_exit_code(proc.returncode)
    elif elapsed_ms > time_limit_ms:
        result["status"] = "TLE"
    elif peak > mem_limit_bytes:
        result["status"] = "MLE"
    elif output_limit_lines > 0 and output_line_count > output_limit_lines:
        result["status"] = "OLE"

    kernel32.CloseHandle(hJob)
    return result


# ============================ 输出比较 ============================

def normalize_text(data):
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = data
    lines = text.splitlines()
    lines = [ln.rstrip() for ln in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def compare_output(actual, expected):
    return normalize_text(actual) == normalize_text(expected)


def compute_diff(expected_bytes, actual_bytes):
    e_lines = normalize_text(expected_bytes)
    a_lines = normalize_text(actual_bytes)
    sm = difflib.SequenceMatcher(None, e_lines, a_lines)
    diff = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            for i in range(i1, i2):
                diff.append(('equal', e_lines[i]))
        elif op == 'delete':
            for i in range(i1, i2):
                diff.append(('delete', e_lines[i]))
        elif op == 'insert':
            for j in range(j1, j2):
                diff.append(('insert', a_lines[j]))
        elif op == 'replace':
            for i in range(i1, i2):
                diff.append(('delete', e_lines[i]))
            for j in range(j1, j2):
                diff.append(('insert', a_lines[j]))
    return diff


# ============================ 测评点配对 ============================

def pair_test_files(file_paths):
    groups = defaultdict(dict)
    for p in file_paths:
        stem, ext = os.path.splitext(p)
        ext = ext.lower()
        if ext == ".in":
            groups[stem]["in"] = p
        elif ext in (".out", ".ans"):
            groups[stem]["out"] = p
    pairs = []
    for stem in sorted(groups.keys()):
        g = groups[stem]
        if "in" in g and "out" in g:
            pairs.append((g["in"], g["out"], os.path.basename(stem)))
    return pairs


# ============================ 主题配色 ============================

THEMES = {
    "dark": {
        "bg": "#1e1e2e",
        "card": "#313244",
        "border": "#45475a",
        "primary": "#89b4fa",
        "primary_hover": "#74c7ec",
        "text_primary": "#cdd6f4",
        "text_secondary": "#a6adc8",
        "listbox_bg": "#313244",
        "listbox_fg": "#cdd6f4",
        "result_bg": "#181825",
        "result_fg": "#cdd6f4",
        "result_select": "#45475a",
        "tag_ac": "#a6e3a1",
        "tag_wa": "#f38ba8",
        "tag_tle": "#fab387",
        "tag_mle": "#cba6f7",
        "tag_ole": "#f9e2af",
        "tag_re": "#f38ba8",
        "tag_ce": "#f38ba8",
        "tag_header": "#89b4fa",
        "tag_info": "#a6adc8",
        "tag_muted": "#6c7086",
        "diff_delete": "#89b4fa",
        "diff_insert": "#f38ba8",
        "scrollbar_trough": "#313244",
        "scrollbar_bg": "#a0a0a0",
        "scrollbar_active": "#b8b8b8",
        "scrollbar_arrow": "#404040",
    },
    "light": {
        "bg": "#f0f2f5",
        "card": "#ffffff",
        "border": "#d0d5dd",
        "primary": "#3b82f6",
        "primary_hover": "#2563eb",
        "text_primary": "#1f2937",
        "text_secondary": "#6b7280",
        "listbox_bg": "#f8f9fa",
        "listbox_fg": "#1f2937",
        "result_bg": "#1e1e2e",
        "result_fg": "#cdd6f4",
        "result_select": "#45475a",
        "tag_ac": "#a6e3a1",
        "tag_wa": "#f38ba8",
        "tag_tle": "#fab387",
        "tag_mle": "#cba6f7",
        "tag_ole": "#f9e2af",
        "tag_re": "#f38ba8",
        "tag_ce": "#f38ba8",
        "tag_header": "#89b4fa",
        "tag_info": "#a6adc8",
        "tag_muted": "#6c7086",
        "diff_delete": "#89b4fa",
        "diff_insert": "#f38ba8",
        "scrollbar_trough": "#f0f2f5",
        "scrollbar_bg": "#c0c0c0",
        "scrollbar_active": "#d0d0d0",
        "scrollbar_arrow": "#404040",
    },
}


# ============================ 折叠框 ============================
MAX_DISPLAY_LENGTH = 512


class TestResultFrame(tk.Frame):
    def __init__(self, parent, data, colors, **kwargs):
        super().__init__(parent, bg=colors["card"], highlightthickness=1,
                        highlightbackground=colors["border"], bd=0, **kwargs)
        self.c = colors
        self.data = data
        self.expanded = False
        self._build_header()
        self._build_content()

    def _build_header(self):
        d = self.data
        c = self.c
        status_colors = {
            "AC": c["tag_ac"], "WA": c["tag_wa"], "TLE": c["tag_tle"],
            "MLE": c["tag_mle"], "OLE": c["tag_ole"], "RE": c["tag_re"], "CE": c["tag_ce"],
        }
        self.header = tk.Frame(self, bg=c["card"], cursor="hand2")
        self.header.pack(fill="x", padx=10, pady=8)

        self.arrow = tk.Label(self.header, text="▶", bg=c["card"],
                              fg=c["text_secondary"], font=("Consolas", 11))
        self.arrow.pack(side="left", padx=(0, 8))

        name_lbl = tk.Label(self.header, text=f"#{d['index']}  {d['name']}", bg=c["card"],
                             fg=c["text_primary"], font=("Microsoft YaHei UI", 10, "bold"))
        name_lbl.pack(side="left", padx=(0, 16))

        status_text = d["status"]
        if d["status"] == "RE" and d.get("re_detail"):
            status_text = d["re_detail"]
        status_lbl = tk.Label(self.header, text=status_text, bg=c["card"],
                               fg=status_colors.get(d["status"], c["text_primary"]),
                               font=("Consolas", 10, "bold"))
        status_lbl.pack(side="left", padx=(0, 16))

        info = f"{d['time_ms']} ms  |  {d['mem_kb']} KB"
        info_lbl = tk.Label(self.header, text=info, bg=c["card"],
                             fg=c["text_secondary"], font=("Consolas", 9))
        info_lbl.pack(side="left")

        for w in [self.header, self.arrow, name_lbl, status_lbl, info_lbl]:
            w.bind("<Button-1>", self.toggle)

    def _build_content(self):
        d = self.data
        c = self.c

        self.content = tk.Frame(self, bg=c["result_bg"])

        self.text = tk.Text(self.content, wrap="none", font=("Consolas", 9),
                            bg=c["result_bg"], fg=c["result_fg"],
                            relief="flat", height=16, padx=8, pady=4,
                            state="disabled", cursor="arrow",
                            selectbackground=c["result_select"],
                            selectforeground=c["result_fg"])
        sb_y = ttk.Scrollbar(self.content, orient="vertical", command=self.text.yview)
        sb_x = ttk.Scrollbar(self.content, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_x.pack(side="bottom", fill="x")
        sb_y.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True, padx=(10, 0), pady=(0, 8))

        self.text.tag_configure("section", foreground=c["tag_header"],
                                font=("Microsoft YaHei UI", 9, "bold"))
        self.text.tag_configure("content", foreground=c["result_fg"])
        self.text.tag_configure("diff_delete", foreground=c["diff_delete"])
        self.text.tag_configure("diff_insert", foreground=c["diff_insert"])
        self.text.tag_configure("diff_equal", foreground=c["result_fg"])

        self.text.config(state="normal")

        self._insert_section("输入", d.get("input_text", ""))
        self._insert_section("预期输出", d.get("expected_text", ""))
        self._insert_section("输出", d.get("actual_text", ""))

        if d["status"] == "WA" and d.get("diff"):
            diff_text = "\n".join(f'{({"delete": "- ", "insert": "+ "}.get(tag, "  "))}{line}' for tag, line in d["diff"])
            if len(diff_text) > MAX_DISPLAY_LENGTH:
                self.text.insert("end", "━━━ 差异比较 ━━━\n", "section")
                self.text.insert("end", f"差异过长（{len(diff_text)} 字符），不显示。\n\n", "content")
            else:
                self.text.insert("end", "━━━ 差异比较 ━━━\n", "section")
                for tag, line in d["diff"]:
                    prefix = {"delete": "- ", "insert": "+ "}.get(tag, "  ")
                    ttag = {"delete": "diff_delete", "insert": "diff_insert"}.get(tag, "diff_equal")
                    self.text.insert("end", f"{prefix}{line}\n", ttag)

        self.text.config(state="disabled")

    def _insert_section(self, title, text):
        self.text.insert("end", f"━━━ {title} ━━━\n", "section")
        if not text:
            self.text.insert("end", "(空)\n\n", "content")
        elif len(text) > MAX_DISPLAY_LENGTH:
            self.text.insert("end", f"文件过长（{len(text)} 字符），不显示。\n\n", "content")
        else:
            self.text.insert("end", text + "\n\n", "content")

    def toggle(self, event=None):
        if self.expanded:
            self.content.pack_forget()
            self.arrow.config(text="▶")
            self.expanded = False
        else:
            self.content.pack(fill="x", padx=10, pady=(0, 8))
            self.arrow.config(text="▼")
            self.expanded = True


# ============================ GUI ============================

class JudgerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("C++ Judger")
        self.root.geometry("960x720")
        self.root.minsize(800, 600)

        self.dark_mode = True
        self.c = THEMES["dark"]

        self.cpp_path = tk.StringVar(value="未选择")
        self.test_files = []
        self.pairs = []
        self.time_limit = tk.StringVar(value="1000")
        self.mem_limit = tk.StringVar(value="256")
        self.output_limit = tk.StringVar(value="256")
        self.std_var = tk.StringVar(value="C++14")
        self.gpp_path = tk.StringVar(value=DEFAULT_GPP)

        self.queue = queue.Queue()
        self.running = False
        self.work_dir = None

        self.results_data = []
        self.compile_error = None
        self.summary_data = None

        self._cleanup_stale()
        self._setup_style()
        self._build_ui()
        self._apply_theme()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._process_queue)

    def _setup_style(self):
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

    def _configure_styles(self):
        s = self.style
        c = self.c
        s.configure(".", background=c["bg"], foreground=c["text_primary"], font=("Microsoft YaHei UI", 10))
        s.configure("TFrame", background=c["bg"])
        s.configure("Card.TFrame", background=c["card"])
        s.configure("TLabel", background=c["bg"], foreground=c["text_primary"], font=("Microsoft YaHei UI", 10))
        s.configure("Card.TLabel", background=c["card"], foreground=c["text_primary"], font=("Microsoft YaHei UI", 10))
        s.configure("Dim.TLabel", background=c["bg"], foreground=c["text_secondary"], font=("Microsoft YaHei UI", 9))
        s.configure("CardDim.TLabel", background=c["card"], foreground=c["text_secondary"], font=("Microsoft YaHei UI", 9))
        s.configure("Title.TLabel", background=c["bg"], foreground=c["text_primary"], font=("Microsoft YaHei UI", 16, "bold"))
        s.configure("Section.TLabel", background=c["card"], foreground=c["text_primary"], font=("Microsoft YaHei UI", 11, "bold"))
        s.configure("Path.TLabel", background=c["card"], foreground=c["text_secondary"], font=("Consolas", 9))

        s.configure("Primary.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=(20, 8))
        s.map("Primary.TButton",
              background=[("active", c["primary_hover"]), ("!active", c["primary"])],
              foreground=[("active", "white"), ("!active", "white")])
        s.configure("Secondary.TButton", font=("Microsoft YaHei UI", 10), padding=(12, 6),
                    background=c["card"], foreground=c["text_primary"])
        s.map("Secondary.TButton",
              background=[("active", c["border"]), ("!active", c["card"])],
              foreground=[("active", c["text_primary"]), ("!active", c["text_primary"])])
        s.configure("Small.TButton", font=("Microsoft YaHei UI", 9), padding=(8, 4),
                    background=c["card"], foreground=c["text_primary"])
        s.map("Small.TButton",
              background=[("active", c["border"]), ("!active", c["card"])],
              foreground=[("active", c["text_primary"]), ("!active", c["text_primary"])])

        s.configure("TEntry", padding=(8, 6), fieldbackground=c["card"], foreground=c["text_primary"],
                    background=c["card"], bordercolor=c["border"], focuscolor=c["primary"])
        s.configure("TCombobox", padding=(8, 6), fieldbackground=c["card"], foreground=c["text_primary"],
                    background=c["card"], bordercolor=c["border"], focuscolor=c["primary"],
                    selectbackground=c["primary"], selectforeground="white")
        s.map("TCombobox",
              fieldbackground=[("readonly", c["card"])],
              foreground=[("readonly", c["text_primary"])],
              selectbackground=[("readonly", c["primary"])],
              selectforeground=[("readonly", "white")])

        s.configure("Vertical.TScrollbar", background=c["scrollbar_bg"],
                    troughcolor=c["scrollbar_trough"], bordercolor=c["scrollbar_trough"],
                    arrowcolor=c["scrollbar_arrow"], arrowsize=14)
        s.map("Vertical.TScrollbar",
              background=[("active", c["scrollbar_active"]), ("!active", c["scrollbar_bg"])],
              arrowcolor=[("active", c["scrollbar_arrow"]), ("!active", c["scrollbar_arrow"])])
        s.configure("Horizontal.TScrollbar", background=c["scrollbar_bg"],
                    troughcolor=c["scrollbar_trough"], bordercolor=c["scrollbar_trough"],
                    arrowcolor=c["scrollbar_arrow"], arrowsize=14)
        s.map("Horizontal.TScrollbar",
              background=[("active", c["scrollbar_active"]), ("!active", c["scrollbar_bg"])],
              arrowcolor=[("active", c["scrollbar_arrow"]), ("!active", c["scrollbar_arrow"])])

    def _build_ui(self):
        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill="both", expand=True, padx=16, pady=12)

        header = ttk.Frame(self.main_frame)
        header.pack(fill="x", pady=(0, 12))

        self.theme_btn = ttk.Button(header, text="☀ 浅色", style="Small.TButton", command=self.toggle_theme, width=8)
        self.theme_btn.pack(side="left", padx=(0, 12))

        ttk.Label(header, text="C++ Judger", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  本地测评工具", style="Dim.TLabel").pack(side="left", padx=(4, 0), pady=(6, 0))

        self._build_file_card(self.main_frame)
        self._build_option_card(self.main_frame)
        self._build_action_bar(self.main_frame)
        self._build_result_card(self.main_frame)

    def _card_frame(self, parent, title):
        outer = ttk.Frame(parent, style="Card.TFrame")
        title_bar = ttk.Frame(outer, style="Card.TFrame")
        title_bar.pack(fill="x", padx=16, pady=(12, 0))
        ttk.Label(title_bar, text=title, style="Section.TLabel").pack(side="left")
        body = ttk.Frame(outer, style="Card.TFrame")
        body.pack(fill="x", padx=16, pady=(8, 12))
        return outer, body, title_bar

    def _build_file_card(self, parent):
        card, body, _ = self._card_frame(parent, "文件选择")
        card.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(body, style="Card.TFrame")
        row1.pack(fill="x", pady=(0, 8))
        ttk.Label(row1, text="源文件", style="Card.TLabel", width=8).pack(side="left")
        ttk.Label(row1, textvariable=self.cpp_path, style="Path.TLabel").pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(row1, text="选择", style="Small.TButton", command=self.choose_cpp).pack(side="right")

        self.sep_frame = tk.Frame(body, height=1)
        self.sep_frame.pack(fill="x", pady=(0, 8))

        row2 = ttk.Frame(body, style="Card.TFrame")
        row2.pack(fill="x")
        ttk.Label(row2, text="测评点", style="Card.TLabel", width=8).pack(side="left", anchor="n", pady=(4, 0))

        list_wrap = ttk.Frame(row2, style="Card.TFrame")
        list_wrap.pack(side="left", fill="both", expand=True, padx=(8, 8))
        self.file_listbox = tk.Listbox(
            list_wrap, height=5, selectmode=tk.MULTIPLE,
            font=("Consolas", 9),
            relief="solid", borderwidth=1, highlightthickness=0,
            activestyle="none"
        )
        self.file_listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_wrap, orient="vertical", command=self.file_listbox.yview)
        sb.pack(side="right", fill="y")
        self.file_listbox.config(yscrollcommand=sb.set)

        btn_col = ttk.Frame(row2, style="Card.TFrame")
        btn_col.pack(side="right", anchor="n")
        ttk.Button(btn_col, text="添加", style="Small.TButton", command=self.choose_tests).pack(pady=(0, 4))
        ttk.Button(btn_col, text="删除", style="Small.TButton", command=self.delete_tests).pack(pady=(0, 4))
        ttk.Button(btn_col, text="清空", style="Small.TButton", command=self.clear_tests).pack()

    def _build_option_card(self, parent):
        card, body, _ = self._card_frame(parent, "评测选项")
        card.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(body, style="Card.TFrame")
        row1.pack(fill="x", pady=(0, 8))

        for label_text, var, width in [("时间限制", self.time_limit, 8), ("空间限制", self.mem_limit, 8), ("输出上限", self.output_limit, 8)]:
            ttk.Label(row1, text=label_text, style="Card.TLabel").pack(side="left", padx=(0, 4))
            ttk.Entry(row1, textvariable=var, width=width, font=("Consolas", 10)).pack(side="left", padx=(0, 4))
            unit = "ms" if "时间" in label_text else "MB" if "空间" in label_text else "行"
            ttk.Label(row1, text=unit, style="CardDim.TLabel").pack(side="left", padx=(0, 20))

        ttk.Label(row1, text="C++ 版本", style="Card.TLabel").pack(side="left", padx=(0, 4))
        ttk.Combobox(row1, textvariable=self.std_var, values=["C++11", "C++14", "C++20", "C++23"],
                     width=8, state="readonly", font=("Consolas", 10)).pack(side="left")

        row2 = ttk.Frame(body, style="Card.TFrame")
        row2.pack(fill="x")
        ttk.Label(row2, text="编译器", style="Card.TLabel").pack(side="left", padx=(0, 4))
        ttk.Entry(row2, textvariable=self.gpp_path, font=("Consolas", 9)).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row2, text="浏览", style="Small.TButton", command=self.choose_gpp).pack(side="right")

    def _build_action_bar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 8))
        self.start_btn = ttk.Button(bar, text="开始评测", style="Primary.TButton", command=self.start_judge)
        self.start_btn.pack(side="left")
        ttk.Button(bar, text="清空结果", style="Secondary.TButton", command=self.clear_result).pack(side="left", padx=(12, 0))
        self.status_label = ttk.Label(bar, text="", style="Dim.TLabel")
        self.status_label.pack(side="right", padx=(8, 0))

    def _build_result_card(self, parent):
        card, _, title_bar = self._card_frame(parent, "评测结果")
        card.pack(fill="both", expand=True)

        self.summary_label = tk.Label(title_bar, text="", bg=self.c["card"],
                                      fg=self.c["text_secondary"],
                                      font=("Microsoft YaHei UI", 9))
        self.summary_label.pack(side="right", padx=(8, 0))

        result_wrap = ttk.Frame(card, style="Card.TFrame")
        result_wrap.pack(fill="both", expand=True, padx=16, pady=(8, 12))

        self.result_canvas = tk.Canvas(result_wrap, highlightthickness=0, bg=self.c["bg"])
        self.result_vscroll = ttk.Scrollbar(result_wrap, orient="vertical", command=self.result_canvas.yview)
        self.result_inner = tk.Frame(self.result_canvas, bg=self.c["bg"])

        self.result_inner.bind("<Configure>",
            lambda e: self.result_canvas.configure(scrollregion=self.result_canvas.bbox("all")))

        self._canvas_window = self.result_canvas.create_window((0, 0), window=self.result_inner, anchor="nw")
        self.result_canvas.configure(yscrollcommand=self.result_vscroll.set)
        self.result_canvas.bind("<Configure>", self._on_canvas_configure)

        self.result_vscroll.pack(side="right", fill="y")
        self.result_canvas.pack(side="left", fill="both", expand=True)

        self.result_canvas.bind("<Enter>", self._bind_mousewheel)
        self.result_canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_canvas_configure(self, event):
        self.result_canvas.itemconfig(self._canvas_window, width=event.width)

    def _bind_mousewheel(self, event):
        self.result_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        self.result_canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.result_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _apply_theme(self):
        c = self.c
        self.root.configure(bg=c["bg"])
        self._configure_styles()
        self.sep_frame.configure(bg=c["border"])
        self.file_listbox.configure(
            bg=c["listbox_bg"], fg=c["listbox_fg"],
            selectbackground=c["primary"], selectforeground="white"
        )
        self.result_canvas.configure(bg=c["bg"])
        self.result_inner.configure(bg=c["bg"])
        self.summary_label.configure(bg=c["card"], fg=c["text_secondary"])
        self._render_results()

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.c = THEMES["dark"] if self.dark_mode else THEMES["light"]
        self.theme_btn.configure(text="☀ 浅色" if self.dark_mode else "🌙 深色")
        self._apply_theme()

    # ---------- 文件选择 ----------
    def choose_cpp(self):
        p = filedialog.askopenfilename(
            title="选择 C++ 源文件",
            filetypes=[("C++ 源文件", "*.cpp *.cc *.cxx *.C"), ("所有文件", "*.*")]
        )
        if p:
            self.cpp_path.set(p)

    def choose_gpp(self):
        p = filedialog.askopenfilename(
            title="选择 g++.exe",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")]
        )
        if p:
            self.gpp_path.set(p)

    def choose_tests(self):
        ps = filedialog.askopenfilenames(
            title="添加测评点文件（输入与预期输出，可多选）",
            filetypes=[("测评点文件", "*.in *.out *.ans"), ("所有文件", "*.*")]
        )
        if ps:
            existing = set(self.test_files)
            new_files = [p for p in ps if p not in existing]
            self.test_files.extend(new_files)
            self.pairs = pair_test_files(self.test_files)
            self._refresh_listbox()

    def delete_tests(self):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        indices = sorted(sel, reverse=True)
        for idx in indices:
            if 0 <= idx < len(self.pairs):
                inp, outp, _ = self.pairs[idx]
                if inp in self.test_files:
                    self.test_files.remove(inp)
                if outp in self.test_files:
                    self.test_files.remove(outp)
        self.pairs = pair_test_files(self.test_files)
        self._refresh_listbox()

    def _refresh_listbox(self):
        self.file_listbox.delete(0, tk.END)
        for inp, outp, name in self.pairs:
            self.file_listbox.insert(tk.END, f"{name}  (in: {os.path.basename(inp)} | out: {os.path.basename(outp)})")
        unpaired = len(self.test_files) - len(self.pairs) * 2
        if unpaired > 0:
            self.file_listbox.insert(tk.END, f"[警告] {unpaired} 个文件未能配对")

    def clear_tests(self):
        self.test_files = []
        self.pairs = []
        self.file_listbox.delete(0, tk.END)

    # ---------- 结果渲染 ----------
    def _render_results(self):
        for w in self.result_inner.winfo_children():
            w.destroy()

        if self.compile_error:
            c = self.c
            err_frame = tk.Frame(self.result_inner, bg=c["card"], highlightthickness=1,
                                  highlightbackground=c["tag_re"], bd=0)
            err_frame.pack(fill="x", pady=(0, 6), padx=4)

            err_header = tk.Frame(err_frame, bg=c["card"], cursor="hand2")
            err_header.pack(fill="x", padx=10, pady=8)
            tk.Label(err_header, text="编译失败", bg=c["card"], fg=c["tag_re"],
                     font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")

            err_text = tk.Text(err_frame, wrap="none", font=("Consolas", 9),
                               bg=c["result_bg"], fg=c["result_fg"],
                               relief="flat", height=12, padx=8, pady=4,
                               state="disabled", cursor="arrow",
                               selectbackground=c["result_select"],
                               selectforeground=c["result_fg"])
            sb_y = ttk.Scrollbar(err_frame, orient="vertical", command=err_text.yview)
            sb_x = ttk.Scrollbar(err_frame, orient="horizontal", command=err_text.xview)
            err_text.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
            sb_x.pack(side="bottom", fill="x", padx=(10, 0))
            sb_y.pack(side="right", fill="y", padx=(0, 10))
            err_text.pack(fill="both", expand=True, padx=(10, 0), pady=(0, 8))
            err_text.config(state="normal")
            err_text.insert("end", self.compile_error)
            err_text.config(state="disabled")

        for data in self.results_data:
            frame = TestResultFrame(self.result_inner, data, self.c)
            frame.pack(fill="x", pady=(0, 6), padx=4)

        if self.summary_data:
            passed, total = self.summary_data
            tag = "tag_ac" if passed == total else "tag_wa"
            self.summary_label.config(text=f"{passed}/{total} 通过", fg=self.c[tag])
        else:
            self.summary_label.config(text="")

    # ---------- 评测 ----------
    def start_judge(self):
        if self.running:
            return
        cpp = self.cpp_path.get()
        if not cpp or cpp == "未选择" or not os.path.isfile(cpp):
            messagebox.showerror("错误", "请先选择有效的 C++ 源文件。")
            return
        if not self.pairs:
            messagebox.showerror("错误", "请先选择测评点文件（需包含配对的 .in 与 .out/.ans）。")
            return
        try:
            tl = int(self.time_limit.get())
            ml = int(self.mem_limit.get())
            ol = int(self.output_limit.get())
            if tl <= 0 or ml <= 0 or ol <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "时间限制、空间限制与输出上限必须为正整数。")
            return

        parallel = os.cpu_count() or 4

        gpp = self.gpp_path.get().strip()
        if not os.path.isfile(gpp):
            messagebox.showerror("错误", f"找不到编译器：{gpp}")
            return

        std_map = {"C++11": "c++11", "C++14": "c++14", "C++20": "c++20", "C++23": "c++23"}
        std_flag = std_map.get(self.std_var.get(), "c++14")

        self.running = True
        self.start_btn.config(state="disabled")
        self.status_label.config(text="评测中…")
        self.results_data = []
        self.compile_error = None
        self.summary_data = None
        self._render_results()

        threading.Thread(
            target=self._judge_worker,
            args=(cpp, std_flag, gpp, tl, ml, ol, list(self.pairs), parallel),
            daemon=True
        ).start()

    def _judge_worker(self, cpp, std_flag, gpp, tl, ml, ol, pairs, parallel):
        work_dir = tempfile.mkdtemp(prefix="judger_")
        self.work_dir = work_dir
        exe_path = os.path.join(work_dir, "solution.exe")

        try:
            self.queue.put(("status", "正在编译…"))
            code, out, err = compile_cpp(cpp, std_flag, gpp, exe_path)
            if code != 0:
                self.queue.put(("compile_error", err if err else out))
                self.queue.put(("done", 0, len(pairs)))
                return

            self.queue.put(("status", "编译成功，开始评测…"))

            def run_single(i, inp, outp, name):
                res = run_test(exe_path, inp, tl, ml, ol)

                input_text = ""
                try:
                    with open(inp, "r", encoding="utf-8", errors="replace") as f:
                        input_text = f.read()
                except Exception:
                    input_text = "(无法读取)"

                expected_bytes = b""
                expected_text = ""
                try:
                    with open(outp, "rb") as f:
                        expected_bytes = f.read()
                    expected_text = expected_bytes.decode("utf-8", errors="replace")
                except Exception:
                    expected_text = "(无法读取)"

                actual_bytes = res["output"]
                actual_text = actual_bytes.decode("utf-8", errors="replace") if actual_bytes else ""

                if res["status"] is None:
                    if compare_output(actual_bytes, expected_bytes):
                        res["status"] = "AC"
                    else:
                        res["status"] = "WA"

                diff = None
                if res["status"] == "WA":
                    diff = compute_diff(expected_bytes, actual_bytes)

                return {
                    "index": i,
                    "name": name,
                    "status": res["status"],
                    "time_ms": res["time_ms"],
                    "mem_kb": res["mem_kb"],
                    "exit_code": res["exit_code"],
                    "re_detail": res.get("re_detail", ""),
                    "input_text": input_text,
                    "expected_text": expected_text,
                    "actual_text": actual_text,
                    "diff": diff,
                }

            tasks = [(i, inp, outp, name) for i, (inp, outp, name) in enumerate(pairs, 1)]
            all_results = [None] * len(pairs)
            next_send = 0
            completed = 0
            passed = 0

            with ThreadPoolExecutor(max_workers=parallel) as executor:
                future_map = {executor.submit(run_single, *task): task[0] for task in tasks}
                for future in as_completed(future_map):
                    try:
                        result_data = future.result()
                    except Exception as e:
                        idx = future_map[future]
                        result_data = {
                            "index": idx, "name": pairs[idx - 1][2],
                            "status": "RE", "time_ms": 0, "mem_kb": 0,
                            "exit_code": -1, "input_text": "", "expected_text": "",
                            "actual_text": f"运行异常：{e}", "diff": None,
                        }
                    all_results[result_data["index"] - 1] = result_data
                    completed += 1
                    self.queue.put(("status", f"评测中… {completed}/{len(pairs)}"))

                    while next_send < len(pairs) and all_results[next_send] is not None:
                        rd = all_results[next_send]
                        next_send += 1
                        if rd["status"] == "AC":
                            passed += 1
                        self.queue.put(("test_result", rd))

            self.queue.put(("done", passed, len(pairs)))
        except Exception as e:
            self.queue.put(("compile_error", f"评测过程中发生异常：{e}"))
            self.queue.put(("done", 0, len(pairs)))
        finally:
            self._cleanup_workdir(work_dir)
            self.work_dir = None

    # ---------- 队列处理 ----------
    def _process_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)

    def _handle_msg(self, msg):
        kind = msg[0]
        if kind == "status":
            self.status_label.config(text=msg[1])
        elif kind == "compile_error":
            self.compile_error = msg[1]
            self._render_results()
        elif kind == "test_result":
            self.results_data.append(msg[1])
            frame = TestResultFrame(self.result_inner, msg[1], self.c)
            frame.pack(fill="x", pady=(0, 6), padx=4)
        elif kind == "done":
            passed, total = msg[1], msg[2]
            self.summary_data = (passed, total)
            tag = "tag_ac" if passed == total else "tag_wa"
            self.summary_label.config(text=f"{passed}/{total} 通过", fg=self.c[tag])
            self.running = False
            self.start_btn.config(state="normal")
            self.status_label.config(text=f"完成  {passed}/{total} 通过")

    def clear_result(self):
        self.results_data = []
        self.compile_error = None
        self.summary_data = None
        self._render_results()
        self.status_label.config(text="")

    def _cleanup_workdir(self, work_dir):
        if work_dir and os.path.isdir(work_dir):
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass

    def _cleanup_stale(self):
        tmp = tempfile.gettempdir()
        try:
            for name in os.listdir(tmp):
                if name.startswith("judger_"):
                    path = os.path.join(tmp, name)
                    try:
                        shutil.rmtree(path, ignore_errors=True)
                    except Exception:
                        pass
        except Exception:
            pass

    def _on_close(self):
        if self.work_dir:
            self._cleanup_workdir(self.work_dir)
        self.root.destroy()


def main():
    root = tk.Tk()
    JudgerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()