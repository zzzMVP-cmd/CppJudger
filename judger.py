#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C++ 测评器
- 通过 GUI 选择 C++ 源文件、测评点文件（输入/预期输出），设置时间/空间限制与 C++ 标准版本
- 重新读取并编译源文件，编译失败则完整反馈报错信息
- 依次运行各测评点，反馈 AC / WA / TLE / MLE / RE 及运行时间与空间
- 使用 Windows Job Object 监控内存并强制终止超限进程，无需 psutil
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

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ============================ Windows Job Object 绑定 ============================

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


# ============================ 编译 ============================

DEFAULT_GPP = r"D:\zzzMVP\MinGW64\bin\g++.exe"


def compile_cpp(cpp_path, std_flag, gpp_path, out_exe):
    cmd = [gpp_path, f"-std={std_flag}", "-O2", "-w", "-o", out_exe, cpp_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except FileNotFoundError:
        return 1, "", f"找不到编译器：{gpp_path}\n请确认编译器路径是否正确。"
    except subprocess.TimeoutExpired:
        return 1, "", "编译超时（超过 120 秒）。"
    enc = locale.getpreferredencoding(False) or "utf-8"
    out = proc.stdout.decode(enc, errors="replace")
    err = proc.stderr.decode(enc, errors="replace")
    return proc.returncode, out, err


# ============================ 运行单个测试点 ============================

def run_test(exe_path, input_path, time_limit_ms, mem_limit_mb):
    mem_limit_bytes = mem_limit_mb * 1024 * 1024
    hard_time_ms = time_limit_ms * 1.2
    hard_mem_bytes = int(mem_limit_bytes * 1.2)

    result = {
        "status": None,
        "time_ms": 0,
        "mem_kb": 0,
        "output": b"",
        "exit_code": 0,
    }

    hJob = create_job()
    try:
        fin = open(input_path, "rb")
    except Exception as e:
        result["status"] = "RE"
        result["output"] = f"无法读取输入文件：{e}".encode("utf-8")
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
        result["output"] = f"无法启动程序：{e}".encode("utf-8")
        kernel32.CloseHandle(hJob)
        return result
    finally:
        fin.close()

    hProc = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
    if hProc:
        kernel32.AssignProcessToJobObject(hJob, hProc)
        kernel32.CloseHandle(hProc)

    stdout_chunks = []

    def reader():
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                stdout_chunks.append(chunk)
        except Exception:
            pass

    t_reader = threading.Thread(target=reader, daemon=True)
    t_reader.start()

    start = time.perf_counter()
    killed_by_time = False
    killed_by_mem = False

    while proc.poll() is None:
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms > hard_time_ms:
            kernel32.TerminateJobObject(hJob, 1)
            killed_by_time = True
            break
        peak = query_peak_memory(hJob)
        if peak > hard_mem_bytes:
            kernel32.TerminateJobObject(hJob, 1)
            killed_by_mem = True
            break
        time.sleep(0.02)

    proc.wait()
    t_reader.join(timeout=3)
    elapsed_ms = (time.perf_counter() - start) * 1000
    peak = query_peak_memory(hJob)

    result["time_ms"] = int(elapsed_ms)
    result["mem_kb"] = int(peak // 1024)
    result["exit_code"] = proc.returncode if proc.returncode is not None else -1
    result["output"] = b"".join(stdout_chunks)

    if killed_by_time:
        result["status"] = "TLE"
    elif killed_by_mem:
        result["status"] = "MLE"
    elif proc.returncode != 0:
        result["status"] = "RE"
    elif elapsed_ms > time_limit_ms:
        result["status"] = "TLE"
    elif peak > mem_limit_bytes:
        result["status"] = "MLE"

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


def make_diff(expected_bytes, actual_bytes):
    import difflib
    e = normalize_text(expected_bytes)
    a = normalize_text(actual_bytes)
    diff = difflib.unified_diff(
        e, a, fromfile="预期输出", tofile="实际输出", lineterm=""
    )
    return "\n".join(diff)


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


# ============================ GUI ============================

import queue


class JudgerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("C++ 测评器")
        self.root.geometry("900x700")

        self.cpp_path = tk.StringVar(value="未选择")
        self.test_files = []
        self.pairs = []
        self.time_limit = tk.StringVar(value="1000")
        self.mem_limit = tk.StringVar(value="256")
        self.std_var = tk.StringVar(value="C++14")
        self.gpp_path = tk.StringVar(value=DEFAULT_GPP)

        self.queue = queue.Queue()
        self.running = False

        self._build_ui()
        self.root.after(100, self._process_queue)

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        top = ttk.LabelFrame(self.root, text="文件选择")
        top.pack(fill="x", padx=8, pady=6)

        row = 0
        ttk.Label(top, text="C++ 源文件:").grid(row=row, column=0, sticky="w", **pad)
        ttk.Label(top, textvariable=self.cpp_path, width=50, anchor="w").grid(row=row, column=1, sticky="w", **pad)
        ttk.Button(top, text="选择…", command=self.choose_cpp).grid(row=row, column=2, **pad)

        row += 1
        ttk.Label(top, text="测评点文件:").grid(row=row, column=0, sticky="nw", **pad)
        list_frame = ttk.Frame(top)
        list_frame.grid(row=row, column=1, sticky="we", **pad)
        self.file_listbox = tk.Listbox(list_frame, height=6, selectmode=tk.EXTENDED)
        self.file_listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_listbox.yview)
        sb.pack(side="right", fill="y")
        self.file_listbox.config(yscrollcommand=sb.set)
        btn_frame = ttk.Frame(top)
        btn_frame.grid(row=row, column=2, sticky="n", **pad)
        ttk.Button(btn_frame, text="选择…", command=self.choose_tests).pack(pady=2)
        ttk.Button(btn_frame, text="清空", command=self.clear_tests).pack(pady=2)

        mid = ttk.LabelFrame(self.root, text="限制与选项")
        mid.pack(fill="x", padx=8, pady=6)

        ttk.Label(mid, text="时间限制 (ms):").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(mid, textvariable=self.time_limit, width=10).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(mid, text="空间限制 (MB):").grid(row=0, column=2, sticky="w", **pad)
        ttk.Entry(mid, textvariable=self.mem_limit, width=10).grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(mid, text="C++ 版本:").grid(row=0, column=4, sticky="w", **pad)
        ttk.Combobox(mid, textvariable=self.std_var, values=["C++11", "C++14", "C++23"],
                     width=8, state="readonly").grid(row=0, column=5, sticky="w", **pad)
        ttk.Label(mid, text="编译器:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(mid, textvariable=self.gpp_path, width=40).grid(row=1, column=1, columnspan=3, sticky="we", **pad)
        ttk.Button(mid, text="浏览…", command=self.choose_gpp).grid(row=1, column=4, **pad)

        action = ttk.Frame(self.root)
        action.pack(fill="x", padx=8, pady=6)
        self.start_btn = ttk.Button(action, text="开始评测", command=self.start_judge)
        self.start_btn.pack(side="left")
        ttk.Button(action, text="清空结果", command=self.clear_result).pack(side="left", padx=8)

        result_frame = ttk.LabelFrame(self.root, text="评测结果")
        result_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.result_text = tk.Text(result_frame, wrap="none", font=("Consolas", 10))
        sb_y = ttk.Scrollbar(result_frame, orient="vertical", command=self.result_text.yview)
        sb_x = ttk.Scrollbar(result_frame, orient="horizontal", command=self.result_text.xview)
        self.result_text.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side="right", fill="y")
        sb_x.pack(side="bottom", fill="x")
        self.result_text.pack(fill="both", expand=True)

        self.result_text.tag_configure("ac", foreground="#2e8b57")
        self.result_text.tag_configure("wa", foreground="#c0392b")
        self.result_text.tag_configure("tle", foreground="#d35400")
        self.result_text.tag_configure("mle", foreground="#8e44ad")
        self.result_text.tag_configure("re", foreground="#c0392b")
        self.result_text.tag_configure("ce", foreground="#c0392b")
        self.result_text.tag_configure("header", foreground="#1a5276", font=("Consolas", 10, "bold"))
        self.result_text.tag_configure("info", foreground="#34495e")
        self.result_text.tag_configure("muted", foreground="#7f8c8d")

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
            title="选择测评点文件（输入与预期输出，可多选）",
            filetypes=[("测评点文件", "*.in *.out *.ans"), ("所有文件", "*.*")]
        )
        if ps:
            self.test_files = list(ps)
            self.pairs = pair_test_files(self.test_files)
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
            if tl <= 0 or ml <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "时间限制与空间限制必须为正整数。")
            return

        gpp = self.gpp_path.get().strip()
        if not os.path.isfile(gpp):
            messagebox.showerror("错误", f"找不到编译器：{gpp}")
            return

        std_map = {"C++11": "c++11", "C++14": "c++14", "C++23": "c++23"}
        std_flag = std_map.get(self.std_var.get(), "c++14")

        self.running = True
        self.start_btn.config(state="disabled")
        self.clear_result()
        threading.Thread(
            target=self._judge_worker,
            args=(cpp, std_flag, gpp, tl, ml, list(self.pairs)),
            daemon=True
        ).start()

    def _judge_worker(self, cpp, std_flag, gpp, tl, ml, pairs):
        self.queue.put(("info", f"开始评测：{os.path.basename(cpp)}"))
        self.queue.put(("info", f"标准：{std_flag} | 时间限制：{tl} ms | 空间限制：{ml} MB | 测评点：{len(pairs)} 个"))
        self.queue.put(("info", ""))

        work_dir = tempfile.mkdtemp(prefix="judger_")
        exe_path = os.path.join(work_dir, "solution.exe")

        try:
            self.queue.put(("info", "正在编译…"))
            code, out, err = compile_cpp(cpp, std_flag, gpp, exe_path)
            if code != 0:
                self.queue.put(("ce", "编译失败，以下为完整报错信息："))
                self.queue.put(("block", err if err else out))
                self.queue.put(("done", 0, len(pairs)))
                return
            self.queue.put(("ac", "编译成功。"))
            self.queue.put(("info", ""))

            passed = 0
            for i, (inp, outp, name) in enumerate(pairs, 1):
                self.queue.put(("header", f"#{i}  {name}"))
                res = run_test(exe_path, inp, tl, ml)

                if res["status"] is None:
                    try:
                        with open(outp, "rb") as f:
                            expected = f.read()
                    except Exception as e:
                        res["status"] = "RE"
                        res["output"] = f"无法读取预期输出文件：{e}".encode("utf-8")
                        expected = b""

                if res["status"] is None:
                    if compare_output(res["output"], expected):
                        res["status"] = "AC"
                    else:
                        res["status"] = "WA"

                status = res["status"]
                tag = status.lower()
                self.queue.put((tag, f"  结果：{status}    时间：{res['time_ms']} ms    空间：{res['mem_kb']} KB    退出码：{res['exit_code']}"))

                if status == "AC":
                    passed += 1
                elif status == "WA":
                    self.queue.put(("muted", "  --- 预期输出 ---"))
                    self.queue.put(("block", normalize_text(expected)))
                    self.queue.put(("muted", "  --- 实际输出 ---"))
                    self.queue.put(("block", normalize_text(res["output"])))
                    self.queue.put(("muted", "  --- 差异比较 ---"))
                    self.queue.put(("block", make_diff(expected, res["output"])))
                self.queue.put(("info", ""))

            self.queue.put(("done", passed, len(pairs)))
        except Exception as e:
            self.queue.put(("re", f"评测过程中发生异常：{e}"))
            self.queue.put(("done", 0, len(pairs)))
        finally:
            try:
                if os.path.exists(exe_path):
                    os.remove(exe_path)
                os.rmdir(work_dir)
            except Exception:
                pass

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
        if kind == "block":
            lines = msg[1]
            if isinstance(lines, list):
                text = "\n".join(lines)
            else:
                text = str(lines)
            self.result_text.insert("end", text + "\n", "info")
        elif kind == "done":
            passed, total = msg[1], msg[2]
            tag = "ac" if passed == total else "wa"
            self.result_text.insert("end", f"评测完成：{passed}/{total} 通过\n", tag)
            self.running = False
            self.start_btn.config(state="normal")
        else:
            text = msg[1]
            tag = kind if kind in ("ac", "wa", "tle", "mle", "re", "ce", "header", "info", "muted") else "info"
            self.result_text.insert("end", text + "\n", tag)
        self.result_text.see("end")

    def clear_result(self):
        self.result_text.delete("1.0", "end")


def main():
    root = tk.Tk()
    JudgerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()