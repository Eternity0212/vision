# 负责统计hsv分布
import os
import glob
import numpy as np
import cv2
from config.config import LOG_DIR

ROI_RATIO = 0.5  # 只统计画面正中间这一块（宽高各取 50%）
COLORS = ["红", "黄", "绿", "浅蓝", "深蓝", "黑"]


def collect(color):
    pattern = os.path.join(LOG_DIR, "samples", color, "*.jpg")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"[{color}]没有样本,跳过")
        return None
    # 读取文件夹，如果没有文件，则跳过
    hs, ss, vs = [], [], []
    for f in files:
        img = cv2.imread(f)
        if img is None:
            print("读不出来，跳过")
            continue
        # 在files中读取样本
        h, w = img.shape[:2]  # 只用取高宽，通道数不要
        rw = int(w * ROI_RATIO)  # 高宽乘以占比取整
        rh = int(h * ROI_RATIO)
        x0 = (w - rw) // 2
        y0 = (h - rh) // 2
        roi = img[y0 : y0 + rh, x0 : x0 + rw]  # 切图，起点：终点
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        px = hsv.reshape(-1, 3)
        hs.append(px[:, 0])  # 取h
        ss.append(px[:, 1])  # 取s
        vs.append(px[:, 2])  # 取vv
    return np.concatenate(hs), np.concatenate(ss), np.concatenate(vs), len(files)


def suggest(color):
    r = collect(color)
    if r is None:
        return
    h_arr, s_arr, v_arr, n = r  # n指的是这个颜色一共几张图
    h_shift = np.where(h_arr >= 90, h_arr - 180, h_arr)
    h_lo, h_mid, h_hi = np.percentile(h_shift, [5, 50, 95])
    s_lo, s_mid, s_hi = np.percentile(s_arr, [5, 50, 95])
    v_lo, v_mid, v_hi = np.percentile(v_arr, [5, 50, 95])
    print(f"[{color}] 共 {n} 张")  # 输出[红]共n张
    print(f"  H 中位 {h_mid:.0f}  5%~95% {h_lo:.0f}~{h_hi:.0f}")
    print(f"  S 中位 {s_mid:.0f}  5%~95% {s_lo:.0f}~{s_hi:.0f}")
    print(f"  V 中位 {v_mid:.0f}  5%~95% {v_lo:.0f}~{v_hi:.0f}")
    if h_lo < 0:
        print(f"  ↑ H 跨 0,等价于 {int(h_lo) + 180}~179 和 0~{int(h_hi)} 两段")


if __name__ == "__main__":
    for c in COLORS:
        suggest(c)
