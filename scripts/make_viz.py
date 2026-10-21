#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_viz.py — 生成 README 用的对比图

数据全部来自官方 MCP 接口实测结果（见 docs/），无任何估算值。
"""

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch

# 中文字体
for p in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
          "C:/Windows/Fonts/simhei.ttf"]:
    if os.path.exists(p):
        font_manager.fontManager.addfont(p)
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# ---- 麦当劳品牌色 ----
GOLD = "#FFC72C"
DARK_RED = "#DA291C"
INK = "#2C2C2A"
GRAY = "#B4B2A9"
BG = "#FFFFFF"

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets")
os.makedirs(OUT, exist_ok=True)


def fig_comparison():
    """核心卖点图：默认配置 vs 求解结果"""
    # (标题, 默认值, 求解值, 单位, 差值文字)
    panels = [
        ("热量", 949, 566, "kcal", "−40%"),
        ("钠", 1126, 997, "mg", "−129"),
        ("蛋白质", 31.0, 29.0, "g", "−2"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), facecolor=BG)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.8, bottom=0.16, wspace=0.42)

    for ax, (title, dv, sv, unit, delta) in zip(axes, panels):
        top = max(dv, sv) * 1.28

        # 默认柱（灰）与 求解柱（金）并排，宽度 0.5，间距 0.06
        ax.bar([0], [dv], color=GRAY, width=0.5)
        ax.bar([1], [sv], color=GOLD, width=0.5)

        fmt = "{:.0f}" if float(sv).is_integer() else "{:.1f}"
        ax.text(0, dv + top * 0.03, fmt.format(dv) + f" {unit}",
                ha="center", fontsize=12, color=INK)
        ax.text(1, sv + top * 0.03, fmt.format(sv) + f" {unit}",
                ha="center", fontsize=13, color=DARK_RED, fontweight="bold")

        # 求解柱内部标注差值
        if sv > top * 0.22:
            ax.text(1, sv / 2, delta, ha="center", va="center",
                    fontsize=12.5, color="white", fontweight="bold")
        else:
            ax.text(1, sv + top * 0.12, delta, ha="center", fontsize=11,
                    color=DARK_RED, fontweight="bold")

        ax.set_title(title, fontsize=15, color=INK, pad=12)
        ax.set_xlim(-0.6, 1.6)
        ax.set_ylim(0, top)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["默认", "求解"], fontsize=12, color=INK)
        ax.set_facecolor(BG)
        for sp in ["top", "right", "left"]:
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color(GRAY)
        ax.tick_params(axis="both", length=0, labelsize=12, colors=INK)
        ax.set_yticks([])

    fig.suptitle("巨无霸三件套 · 昆明柏联广场¥37.50 → ¥36.50",
                 fontsize=17, color=INK, fontweight="bold", y=0.98)
    p = os.path.join(OUT, "comparison.png")
    fig.savefig(p, dpi=200, facecolor=BG, bbox_inches="tight")
    plt.close()
    print("✓", p)


def fig_rounds():
    """套餐三轮次结构图"""
    fig, ax = plt.subplots(figsize=(9.5, 3.4), facecolor=BG)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3.2)
    ax.axis("off")

    rounds = [
        ("主食 · 必选", "新升级巨无霸", "+¥0", "513 kcal", True),
        ("小食", "中薯条", "+¥0", "289 kcal", False),
        ("小食", "小杯玉米杯", "−¥1", "53 kcal", True),
        ("小食", "大薯条", "+¥2", "379 kcal", False),
        ("饮料", "可乐中杯", "+¥0", "147 kcal", False),
        ("饮料", "无糖可口可乐", "+¥0", "0 kcal", True),
    ]

    x = 0.15
    for kind, name, price, kcal, best in rounds:
        w = 1.5
        rect = FancyBboxPatch(
            (x, 0.35), w, 2.3,
            boxstyle="round,pad=0,rounding_size=0.08",
            facecolor=GOLD if best else "#F4F3F0",
            edgecolor=GOLD if best else GRAY,
            linewidth=2.0 if best else 0.8,
        )
        ax.add_patch(rect)
        # 黄底用深色字，保证对比度
        sub_c = "#8A6D00" if best else GRAY
        main_c = INK
        kcal_c = "#8A6D00" if best else GRAY
        ax.text(x + w / 2, 2.38, kind, ha="center", fontsize=11.5, color=sub_c)
        for i, part in enumerate(_wrap(name, 6)):
            ax.text(x + w / 2, 1.95 - i * 0.26, part, ha="center",
                    fontsize=12, color=main_c,
                    fontweight="bold" if best else "normal")
        ax.text(x + w / 2, 0.95, price, ha="center", fontsize=12.5,
                color=DARK_RED if "-" in price else main_c,
                fontweight="bold" if best else "normal")
        ax.text(x + w / 2, 0.58, kcal, ha="center", fontsize=10, color=kcal_c)
        if best:
            ax.text(x + w / 2, 0.05, "★ 求解选择", ha="center", fontsize=9.5,
                    color=DARK_RED, fontweight="bold")
        x += w + 0.18

    ax.text(5, 3.02, "套餐共 140 种有效组合 → 本地枚举求解 → 仅 3 次接口调用",
            ha="center", fontsize=14, color=INK, fontweight="bold")

    p = os.path.join(OUT, "rounds.png")
    fig.savefig(p, dpi=200, facecolor=BG, bbox_inches="tight")
    plt.close()
    print("✓", p)


def _wrap(s, n):
    out, cur = [], ""
    for ch in s:
        cur += ch
        if len(cur) == n:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def fig_pressure():
    """匹配率对比图"""
    fig, ax = plt.subplots(figsize=(8.6, 2.5), facecolor=BG)
    ax.axis("off")

    labels = ["营养库 158 条\n（单品名）", "门店菜单 113 个\n（大量套餐名）"]
    matched = [19, 19]

    ax.barh([0], [158], color="#F4F3F0", height=0.42, edgecolor=GRAY, linewidth=0.8)
    ax.barh([1], [113], color="#F4F3F0", height=0.42, edgecolor=GRAY, linewidth=0.8)
    ax.barh([0], [19], color=DARK_RED, height=0.42)
    ax.barh([1], [19], color=DARK_RED, height=0.42)

    ax.text(80, 0, "匹配 19 条", va="center", fontsize=12, color="white",
            ha="center", fontweight="bold")
    ax.text(67, 1, "匹配 19 条", va="center", fontsize=12, color="white",
            ha="center", fontweight="bold")

    ax.text(162, 0, "158", va="center", fontsize=12, color=GRAY)
    ax.text(117, 1, "113", va="center", fontsize=12, color=GRAY)

    ax.set_xlim(0, 185)
    ax.set_ylim(-0.6, 1.6)
    ax.set_title("名称精确匹配率仅 17%（19/113）—— 必须拆套餐轮次",
                 fontsize=14.5, color=INK, fontweight="bold", pad=14)

    p = os.path.join(OUT, "matching.png")
    fig.savefig(p, dpi=200, facecolor=BG, bbox_inches="tight")
    plt.close()
    print("✓", p)


if __name__ == "__main__":
    fig_comparison()
    fig_rounds()
    fig_pressure()