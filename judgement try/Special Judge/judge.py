#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Special Judge for 「双列分配」
- 接收两个命令行参数：输入文件路径、答案文件路径
- 从 stdin 读取选手输出
- 验证输出是否合法（每个元素恰好出现一次）
- 计算选手的总代价，与最优代价比较
- 相等则返回 0 (AC)，否则返回 1 (WA)
"""

import sys


def read_ints_from_line(line):
    return list(map(int, line.split()))


def compute_optimal_cost(n, s1, s2, r):
    """
    贪心计算最优总代价：
    将 r 降序排序，依次决定每个元素放入哪个列表。
    每次选择 s1 * (len(a)+1) 与 s2 * (len(b)+1) 中较小的那个。
    """
    sorted_r = sorted(enumerate(r), key=lambda x: -x[1])
    cost = 0
    pos_a = 1
    pos_b = 1
    for idx, val in sorted_r:
        if s1 * pos_a < s2 * pos_b:
            cost += val * s1 * pos_a
            pos_a += 1
        else:
            cost += val * s2 * pos_b
            pos_b += 1
    return cost


def compute_participant_cost(n, s1, s2, r, list_a, list_b):
    """
    计算选手构造方案的总代价：
    list_a 中第 i 个位置（1-based）的代价 = r[element] * i * s1
    list_b 中第 j 个位置（1-based）的代价 = r[element] * j * s2
    """
    cost = 0
    for i, elem in enumerate(list_a, start=1):
        cost += r[elem - 1] * i * s1
    for j, elem in enumerate(list_b, start=1):
        cost += r[elem - 1] * j * s2
    return cost


def validate(n, list_a, list_b):
    """
    验证两个列表是否恰好包含 1..n 各一次。
    返回 (bool, str)
    """
    seen = [False] * (n + 1)
    for elem in list_a + list_b:
        if elem < 1 or elem > n:
            return False, f"元素编号 {elem} 不在 1..{n} 范围内"
        if seen[elem]:
            return False, f"元素 {elem} 重复出现"
        seen[elem] = True
    for i in range(1, n + 1):
        if not seen[i]:
            return False, f"元素 {i} 未出现"
    return True, ""


def parse_participant_output(text, n):
    """
    解析选手输出中的两个列表。
    格式：
    len_a a1 a2 ... a_len_a
    len_b b1 b2 ... b_len_b
    返回 (list_a, list_b) 或 None（解析失败）
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() != '']
    if len(lines) < 2:
        return None, "输出行数不足，需要两行"

    line_a = lines[0]
    line_b = lines[1]

    tokens_a = read_ints_from_line(line_a)
    tokens_b = read_ints_from_line(line_b)

    if len(tokens_a) < 1 or len(tokens_b) < 1:
        return None, "列表格式错误，缺少长度信息"

    len_a = tokens_a[0]
    list_a = tokens_a[1:]

    len_b = tokens_b[0]
    list_b = tokens_b[1:]

    if len(list_a) != len_a:
        return None, f"列表 a 声明长度 {len_a}，实际给出 {len(list_a)} 个元素"
    if len(list_b) != len_b:
        return None, f"列表 b 声明长度 {len_b}，实际给出 {len(list_b)} 个元素"

    if len_a + len_b != n:
        return None, f"两个列表总长度 {len_a + len_b} 不等于 n={n}"

    return (list_a, list_b), ""


def main():
    if len(sys.argv) < 3:
        print("用法: spj.py <input_file> <answer_file>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    answer_file = sys.argv[2]

    try:
        with open(input_file, "r", encoding="utf-8") as f:
            input_data = f.read().replace("\r\n", "\n").replace("\r", "\n")
    except Exception as e:
        print(f"无法读取输入文件：{e}", file=sys.stderr)
        sys.exit(1)

    participant_output = sys.stdin.read().replace("\r\n", "\n").replace("\r", "\n")

    input_lines = input_data.splitlines()
    if not input_lines:
        print("输入文件为空", file=sys.stderr)
        sys.exit(1)

    T = int(input_lines[0].strip())
    input_offset = 1

    participant_lines = [ln.strip() for ln in participant_output.splitlines()]
    participant_lines = [ln for ln in participant_lines if ln != '']

    output_line_idx = 0

    for test_idx in range(1, T + 1):
        if input_offset >= len(input_lines):
            print(f"WA: 第 {test_idx} 组数据不完整", file=sys.stderr)
            sys.exit(1)

        n, s1, s2 = read_ints_from_line(input_lines[input_offset])
        input_offset += 1

        if input_offset >= len(input_lines):
            print(f"WA: 第 {test_idx} 组缺少 r 数组", file=sys.stderr)
            sys.exit(1)

        r = read_ints_from_line(input_lines[input_offset])
        input_offset += 1

        if len(r) != n:
            print(f"WA: 第 {test_idx} 组 r 数组长度 {len(r)} 不等于 n={n}", file=sys.stderr)
            sys.exit(1)

        if output_line_idx + 1 >= len(participant_lines):
            print(f"WA: 第 {test_idx} 组输出行数不足", file=sys.stderr)
            sys.exit(1)

        output_text = participant_lines[output_line_idx] + "\n" + participant_lines[output_line_idx + 1]
        output_line_idx += 2

        parsed = parse_participant_output(output_text, n)
        if parsed[0] is None:
            print(f"WA: 第 {test_idx} 组 {parsed[1]}", file=sys.stderr)
            sys.exit(1)

        list_a, list_b = parsed[0]

        valid, msg = validate(n, list_a, list_b)
        if not valid:
            print(f"WA: 第 {test_idx} 组 {msg}", file=sys.stderr)
            sys.exit(1)

        participant_cost = compute_participant_cost(n, s1, s2, r, list_a, list_b)
        optimal_cost = compute_optimal_cost(n, s1, s2, r)

        if participant_cost != optimal_cost:
            print(f"WA: 第 {test_idx} 组 选手代价 {participant_cost}，最优代价 {optimal_cost}", file=sys.stderr)
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()