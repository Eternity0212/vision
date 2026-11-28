"""机械臂方案：相机装在臂上，车动臂不动，一次只扫一颗球。

流程：
  M,0  车和臂定住 -> 画面连续静止几帧 -> 读二维码 -> 发 T,A,B,C
  M,1  车停在球前 -> 只认画面中线附近那一颗
       颜色和 A 位一致 -> 绿框 + 发 G,1（可抓）
       不一致         -> 红框 + 发 G,0（禁止抓）
  其它模式只发心跳 R
"""
import os
import sys
import time

import cv2
from pyzbar.pyzbar import decode as zbar_decode, ZBarSymbol

from config.config import (CAMERA, BOMB_ID, TARGET_ID, ZONE_ID,   # 相机、三张颜色编号表
                           BOMB_HSV, BOMB_ROI,                    # 排爆球颜色阈值、检测区域
                           ARM, QR,                               # 判定帧数、扫码静止判据
                           MIN_AREA, MAX_AREA_RATIO, DETECT_SCALE)  # 面积过滤、降采样倍数
from capture.camera import Camera    # 打开摄像头
from link.serial import Tx           # 串口收发

try:                                 # 小码/远距离时 zbar 解不出，用 zxing 兜底
    import zxingcpp
except Exception:                    # 没装就只用 zbar
    zxingcpp = None

# 没有屏幕（SSH 连板子）就不开窗口
HEADLESS = sys.platform.startswith("linux") and os.environ.get("DISPLAY") is None
IDX = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]

MODE_CLOSED = 0    # 发车前：只扫二维码
MODE_P3 = 1        # 防爆区：逐个判球

# 画面上写英文，OpenCV 自带字体写不了中文
COLOR_EN = {"蓝": "blue", "绿": "grn", "红": "red"}
GREEN = (0, 255, 0)     # 绿框：是目标，可抓
RED = (0, 0, 255)       # 红框：不是目标，禁止抓
YELLOW = (0, 255, 255)  # 黄框：还在判定中


def frame_still(prev_small, frame):
    """判断画面动没动：缩成 160x90 灰度，和上一帧逐像素比平均差"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)
    if prev_small is None:              # 第一帧没有对比对象
        return None, gray
    return float(cv2.absdiff(prev_small, gray).mean()), gray


def find_balls(frame):
    """把画面里 BOMB_HSV 这几种颜色的球全找出来，返回 [(颜色, cx, cy, r), ...]"""
    s = DETECT_SCALE                    # 先缩小再算，快 3 倍
    if BOMB_ROI is not None:            # 只在指定区域找（衣服、地面不参与）
        x1, y1, x2, y2 = BOMB_ROI
        sub = frame[y1:y2, x1:x2]
    else:
        x1 = y1 = 0
        sub = frame
    small = cv2.resize(sub, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)   # 整帧只转一次 HSV
    h, w = small.shape[:2]
    limit = MAX_AREA_RATIO * w * h                 # 超过半屏的色块不要
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))   # 开运算核，去噪点
    found = []
    for name in BOMB_HSV:                          # 逐个颜色找
        mask = None
        for low, up in BOMB_HSV[name]:             # 一种颜色可以有多段阈值
            m = cv2.inRange(hsv, low, up)
            mask = m if mask is None else cv2.bitwise_or(mask, m)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < MIN_AREA * s * s or area > limit:      # 太小是噪点，太大是背景
                continue
            (cx, cy), radius = cv2.minEnclosingCircle(c)     # 最小外接圆
            if radius < 8 * s:                               # 半径太小不要
                continue
            if area < 1.1 * radius * radius:                 # 外接圆填充率太低：细长/镂空
                continue
            hull = cv2.contourArea(cv2.convexHull(c))        # 凸包面积
            if hull <= 0 or area / hull < 0.85:              # 凹陷太多：衣褶、影子
                continue
            found.append((name, int(cx / s) + x1, int(cy / s) + y1, int(radius / s)))
    return found


def pick_ball(balls, width):
    """车对着的那颗：优先画面中线附近，实在没有就退回最大的一颗"""
    near = None     # 中线附近、半径最大的
    far = None      # 全画面半径最大的（兜底）
    for name, cx, cy, r in balls:
        if far is None or r > far[3]:
            far = (name, cx, cy, r)
        if abs(cx - width / 2.0) <= width * ARM["center_tol"]:   # 圆心在中线附近
            if near is None or r > near[3]:
                near = (name, cx, cy, r)
    return near if near is not None else far


def refine_circle(frame, cx, cy, r):
    """用霍夫圆把颜色框收紧到球的真实边缘，找不到就原样返回"""
    m = int(r * 1.6)                              # 只在球周围一圈里找
    x1, y1 = max(0, cx - m), max(0, cy - m)
    x2 = min(frame.shape[1], cx + m)
    y2 = min(frame.shape[0], cy + m)
    gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 2)      # 抹掉布纹，留球体轮廓
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2,
                               minDist=int(2 * r), param1=120, param2=30,
                               minRadius=int(r * 0.7), maxRadius=int(r * 1.35))
    if circles is None:                           # 没找到圆，保持颜色框
        return cx, cy, r
    # 多个候选里取离原圆心最近的那个
    best = min(circles[0], key=lambda c: (c[0] - (cx - x1)) ** 2 + (c[1] - (cy - y1)) ** 2)
    return int(best[0]) + x1, int(best[1]) + y1, int(best[2])


def center_hsv(frame, cx, cy):
    """取球心 5x5 的 HSV 中间值，只在画面上显示，调阈值用"""
    hsv = cv2.cvtColor(frame[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3],
                       cv2.COLOR_BGR2HSV)
    pixels = hsv.reshape(-1, 3)
    if len(pixels) == 0:          # 贴边时可能裁出空块
        return 0, 0, 0
    h, s, v = pixels[len(pixels) // 2]
    return int(h), int(s), int(v)


def code_ok(code):
    """三位数字，且每一位都在对应的颜色表里，才算有效码"""
    return (len(code) == 3 and code.isdigit()
            and int(code[0]) in BOMB_ID
            and int(code[1]) in TARGET_ID
            and int(code[2]) in ZONE_ID)


def read_qr(frame, last_slow):
    """读二维码，返回 (码, 码宽, 慢路径时刻)；没读到码就返回 (None, 0, last_slow)

    先走 zbar 快路径（原图直接解）；解不出再裁画面中间，放大 3 倍交给 zxing，
    慢路径 1 秒最多跑一次。裁中间是因为码在扫码位就在画面中央，
    整帧放大 3 倍要 2.4 秒，裁中间只要 0.3 秒，还不耽误发心跳。
    """
    results = zbar_decode(frame, symbols=[ZBarSymbol.QRCODE])
    if results:
        best = max(results, key=lambda r: r.rect.width * r.rect.height)
        text = "".join(ch for ch in best.data.decode("ascii", "ignore") if ch.isdigit())
        side = min(best.rect.width, best.rect.height)
        if code_ok(text) and side >= QR["min_side_px"]:
            return text, side, last_slow
    now = time.time()
    if zxingcpp is None or now - last_slow < 1.0:
        return None, 0, last_slow
    last_slow = now
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    for keep in (0.6, 0.8):                 # 先找中间 60%，没找到再放宽到 80%
        x1 = int(w * (1 - keep) / 2)
        y1 = int(h * (1 - keep) / 2)
        crop = gray[y1:y1 + int(h * keep), x1:x1 + int(w * keep)]
        big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        for r in zxingcpp.read_barcodes(big):
            text = "".join(ch for ch in r.text if ch.isdigit())
            if not code_ok(text):
                continue
            p = r.position
            xs = [p.top_left.x, p.top_right.x, p.bottom_right.x, p.bottom_left.x]
            ys = [p.top_left.y, p.top_right.y, p.bottom_right.y, p.bottom_left.y]
            side = min(max(xs) - min(xs), max(ys) - min(ys)) / 3.0
            if side >= QR["min_side_px"]:
                return text, side, last_slow
    return None, 0, last_slow


def main():
    tx = Tx().open()          # 打开串口；没接电控会打印一行，不影响运行
    mode = MODE_CLOSED        # 一开始当发车前
    want = None               # 二维码 A 位对应的颜色（排爆目标色）
    prev_small = None         # 上一帧缩略图，判静止用
    still = 0                 # 画面连续静止帧数
    last_beat = time.time()   # 上次发心跳的时刻
    last_slow = 0.0           # 上次 zxing 慢路径的时刻
    hit_text = None           # 上一次解出的码
    hit_count = 0             # 同一个码连续解出几次

    # 当前这颗球的判定状态
    last_name = None
    last_cx = last_cy = last_r = 0
    stable = 0                # 连续稳定帧数
    sent = False              # 这个位置是否已经发过 G
    sent_ok = None            # 发出去的是 G,1(True) 还是 G,0(False)
    miss = 0                  # 连续看不到球的帧数

    try:
        with Camera(index=IDX,
                    width=CAMERA["width"],
                    height=CAMERA["height"],
                    fps=CAMERA["fps"],
                    warmup=CAMERA["warmup"],
                    auto_exposure=CAMERA["auto_exposure"],
                    exposure=CAMERA["exposure"],
                    auto_wb=CAMERA["auto_wb"]) as cam:
            while True:
                frame = cam.read()
                if frame is None:
                    continue

                # ---- 1. 接收电控命令 ----
                cmd = tx.read_cmd()
                if cmd is not None and cmd.startswith("M,"):
                    try:
                        new_mode = int(cmd.split(",")[1])
                    except (ValueError, IndexError):
                        new_mode = mode
                    if new_mode in (MODE_CLOSED, MODE_P3):
                        if new_mode != mode:
                            print(f"切换阶段 -> {new_mode}")
                        mode = new_mode
                        if new_mode == MODE_P3:      # 新车位，清空上一次判定
                            last_name = None
                            stable = 0
                            sent = False
                            sent_ok = None
                            miss = 0
                    elif new_mode != mode:
                        mode = new_mode
                        print(f"模式 {mode} 未实现，只发心跳 R")

                # ---- 2. 发车前：画面定住后读二维码 ----
                if want is None and mode == MODE_CLOSED:
                    diff, prev_small = frame_still(prev_small, frame)
                    if diff is not None and diff <= QR["still_diff"]:
                        still += 1
                    else:
                        still = 0
                    if still >= QR["still_frames"]:          # 连续静止够久才扫
                        code, side, last_slow = read_qr(frame, last_slow)
                        if code is not None:
                            hit_count = hit_count + 1 if code == hit_text else 1
                            hit_text = code
                            if hit_count >= QR["confirm_frames"]:   # 连续确认几帧
                                first, second, third = int(code[0]), int(code[1]), int(code[2])
                                want = BOMB_ID[first]
                                tx.send_now(f"T,{first},{second},{third}")
                                print(f"锁定 {code} -> 排爆目标色 {want}")
                        else:
                            hit_count = 0
                    if not HEADLESS:                          # 画面上显示静止状态
                        cv2.putText(frame,
                                    f"still:{still}/{QR['still_frames']} "
                                    f"diff:{-1 if diff is None else round(diff, 1)}",
                                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, GREEN, 2)

                # ---- 3. 防爆区：判这一颗球 ----
                elif want is not None and mode == MODE_P3:
                    balls = find_balls(frame)
                    hit = pick_ball(balls, frame.shape[1])
                    det = None
                    if hit is not None:                       # 找到了就收紧圆
                        name, cx, cy, r = hit
                        det = (name,) + refine_circle(frame, cx, cy, r)

                    if det is None:                           # 画面里没有球
                        miss += 1
                        stable = 0
                        last_name = None
                        if miss >= ARM["clear_frames"] and sent:   # 连续看不到，算臂挪走了
                            sent = False
                            sent_ok = None
                            miss = 0
                    else:
                        name, cx, cy, r = det
                        miss = 0
                        if (last_name == name
                                and abs(cx - last_cx) <= ARM["stable_px"]
                                and abs(cy - last_cy) <= ARM["stable_px"]
                                and abs(r - last_r) <= ARM["stable_r"]):
                            stable += 1                       # 和上一帧差不多，稳定加一
                        else:
                            stable = 1                        # 变化太大，重新数
                        last_name, last_cx, last_cy, last_r = name, cx, cy, r

                        if stable >= ARM["stable_frames"] and not sent:   # 稳定够久才下结论
                            sent_ok = (name == want)
                            sent = True
                            tx.send_now("G,1" if sent_ok else "G,0")
                            print(f"{name} == {want}? "
                                  f"{'是 -> G,1' if sent_ok else '否 -> G,0'}")

                    if not HEADLESS and det is not None:      # 画框
                        name, cx, cy, r = det
                        if sent:
                            col = GREEN if sent_ok else RED   # 判过：绿=可抓，红=禁止
                        else:
                            col = YELLOW                      # 还在判定：黄
                        cv2.circle(frame, (cx, cy), r, col, 3)
                        h, s, v = center_hsv(frame, cx, cy)
                        cv2.putText(frame, f"{COLOR_EN.get(name, name)} r={r} H{h} S{s} V{v}",
                                    (max(10, cx - r), max(20, cy - r - 10)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
                    if not HEADLESS:
                        cv2.putText(frame,
                                    f"mode:{mode} tgt:{COLOR_EN.get(want, want)} "
                                    f"stable:{stable} sent:{sent}",
                                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, GREEN, 2)

                # ---- 4. 心跳 ----
                if time.time() - last_beat > 0.2:
                    if tx.send("R"):
                        last_beat = time.time()

                if not HEADLESS:
                    cv2.imshow("arm", frame)
                    if (cv2.waitKey(1) & 0xFF) == ord("q"):
                        break
    finally:
        tx.close()
        if not HEADLESS:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
