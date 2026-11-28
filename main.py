import sys      #判断 windows 还是 linux
import os       #判断有没有图形界面（SSH 连板子时没有 DISPLAY）
import time     #心跳计时、按键节奏
import cv2      #画框和显示

from config.config import (CAMERA, BOMB_ID, TARGET_ID, ZONE_ID,
                           BOMB_HSV, TARGET_HSV, ZONE_HSV,
                           BOMB_ROI, TARGET_ROI, ZONE_ROI,
                           STOP, POS_LEFT_IS, P4)   #相机参数 + 三套颜色编号 + 三组阈值 + 方案甲 v5.0 配置
from capture.camera import Camera       #相机模块，负责打开相机、取帧
from recognize.qr import QrReader       #二维码
from recognize.color import ColorFinder #找颜色
from recognize.measure import Measure   #像素 -> 角度/距离
from link.serial import Tx              #串口收发

HEADLESS = sys.platform.startswith("linux") and os.environ.get("DISPLAY") is None
IDX = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]

MODE_CLOSED = 0     #循迹/发车，闭眼，只发 R
MODE_P3 = 1         #排爆区：判居中发 K，停稳后报 Q,n
MODE_P4 = 2         #转盘：量偏差发 O,±d，守坑位发 G
MODE_P5 = 3         #安置区：与 p3 同一套，判中间球居中发 K，停稳后报指定色球 Q,n

#车前进方向与 off_cm 的对应：直接由 POS_LEFT_IS 推导，不单独配置，避免两个符号参数互相矛盾
#画面左=车尾(POS_LEFT_IS=1)  → 盘心偏画面右说明车还没走到 → 前进
#画面左=车头(POS_LEFT_IS=3)  → 盘心偏画面右说明车已开过头 → 后退
ADVANCE_SIGN = 1 if POS_LEFT_IS == 1 else -1

#画面上的文字用英文，Hershey 字体显示不了中文
COLOR_EN = {"红": "red", "黄": "yel", "绿": "grn", "浅蓝": "lblue", "深蓝": "dblue", "蓝": "blue", "黑": "blk"}


def find_targets(finder, frame, want=3, names=None, roi=None):
    """三个目标颜色随机（p3、p4 都是）：扫指定颜色做候选，去重后取半径最大的 want 个"""
    names = finder.colors if names is None else list(names)
    groups = finder.find_many(frame, names, roi=roi)   #一次 HSV 转换扫完所有颜色
    cands = []
    for name in names:
        for cx, cy, r, _ in groups[name]:
            dup = False
            for c in cands:     #圆心太近，判为同一颗，不重复计数
                if abs(c[0] - cx) < r and abs(c[1] - cy) < r:
                    dup = True
                    break
            if not dup:
                cands.append((cx, cy, r))
    if len(cands) < want:
        return None
    cands.sort(key=lambda t: t[2], reverse=True)    #半径大的优先
    cands = cands[:want]
    cands.sort(key=lambda t: t[0])                  #再按 cx 从左到右
    return cands


def box_colors(finder, frame, items, want, roi=None):
    """按二维码指定的目标色上色：目标物绿色，其余红色；还没对上目标色就全黄"""
    if want is None:
        return [(0, 255, 255)] * len(items)
    hit = finder.find(frame, want, roi=roi)
    if hit is None:
        return [(0, 255, 255)] * len(items)
    i = min(range(len(items)), key=lambda k: abs(items[k][0] - hit[0]))
    return [(0, 255, 0) if k == i else (0, 0, 255) for k in range(len(items))]


def pos_number(idx):
    """排序后的下标 -> 位置编号。最左 0、最右 2，中间永远是 2 号位"""
    if idx == 1:
        return 2
    if POS_LEFT_IS == 1:
        return 1 if idx == 0 else 3
    return 3 if idx == 0 else 1


def main():
    qr = QrReader()
    bomb_finder = ColorFinder(BOMB_HSV)     #排爆物颜色
    target_finder = ColorFinder(TARGET_HSV, allow_tall=True) #转盘救援目标：锥体细高，放宽实心度
    zone_finder = ColorFinder(ZONE_HSV)     #安置区小球颜色
    m = Measure()
    tx = Tx().open()

    ids = [None, None, None]        #二维码三位：0排爆 1救援 2安置
    colors = [None, None, None]     #三位对应的颜色名
    mode = MODE_CLOSED              #由电控下发切换
    last_data = time.time()         #上次发出周期报文的时刻

    p3 = {"centered": 0, "stopped": False, "q_sent": False}
    p4 = {"adjusted": 0, "settled": False, "last_o": 0.0, "last_g": 0.0,
          "g_cx": None, "g_stable": 0}
    p5 = {"centered": 0, "stopped": False, "q_sent": False}

    def reset_p3():
        p3.update(centered=0, stopped=False, q_sent=False)

    def reset_p4():
        p4.update(adjusted=0, settled=False, last_o=0.0, last_g=0.0,
                  g_cx=None, g_stable=0)

    def reset_p5():
        p5.update(centered=0, stopped=False, q_sent=False)

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
                frame = cam.read()  #取一帧
                if frame is None:   #没取到就回头再取
                    continue

                #先看电控有没有下发阶段切换：M,0/1/2/3
                cmd = tx.read_cmd()
                if cmd is not None and cmd.startswith("M,"):
                    try:
                        new_mode = int(cmd.split(",")[1])
                    except (ValueError, IndexError):
                        new_mode = mode
                    if new_mode in (MODE_CLOSED, MODE_P3, MODE_P4, MODE_P5) and new_mode != mode:
                        mode = new_mode
                        reset_p3()  #换阶段，判据全部重来
                        reset_p4()
                        reset_p5()
                        print(f"切换阶段 -> {mode}")

                #二维码只在发车前（MODE_CLOSED）扫，锁定后不再解码，省 CPU
                if ids[0] is None:
                    if mode == MODE_CLOSED:
                        code = qr.feed(frame)
                        if code is not None:
                            ids = [int(code[0]), int(code[1]), int(code[2])]
                            colors = [BOMB_ID[ids[0]], TARGET_ID[ids[1]], ZONE_ID[ids[2]]]
                            tx.send_now(f"T,{ids[0]},{ids[1]},{ids[2]}")
                            print(f"锁定 {code} -> 排爆{colors[0]} 救援{colors[1]} 安置{colors[2]}")

                elif mode == MODE_P3:
                    #排爆区：先判居中发 K，停稳后报目标色在第几号位
                    items = find_targets(bomb_finder, frame, names=BOMB_ID.values(), roi=BOMB_ROI)
                    if items is None:
                        p3["centered"] = 0  #少一个球就不判，连计数清零
                    else:
                        mid = items[1]      #按 cx 排序取中间那颗 = 2 号位球
                        ang = m.angle(mid[0])
                        if not p3["stopped"]:
                            if abs(ang) < STOP["center_deg"]:
                                p3["centered"] += 1
                                if p3["centered"] >= STOP["lock_frames"]:
                                    tx.send_now("K")
                                    p3["stopped"] = True
                                    print("中间球居中 -> 发 K")
                            else:
                                p3["centered"] = 0
                        elif not p3["q_sent"]:
                            #二维码指定的排爆色单独找一次，再用它和最接近的候选对位置
                            target = bomb_finder.find(frame, colors[0], roi=BOMB_ROI)
                            if target is not None:
                                best = min(range(len(items)),
                                           key=lambda i: abs(items[i][0] - target[0]))
                                gap = (items[2][0] - items[0][0]) / 2.0   #三球跨度的一半
                                if abs(items[best][0] - target[0]) <= gap:  #目标球真在候选里
                                    pos = pos_number(best)
                                    tx.send_now(f"Q,{pos}")
                                    p3["q_sent"] = True
                                    print(f"{colors[0]} 在 {pos} 号位 -> 发 Q,{pos}")
                        if not HEADLESS:
                            cols = box_colors(bomb_finder, frame, items, colors[0], BOMB_ROI)
                            for i, it in enumerate(items):
                                cv2.circle(frame, (it[0], it[1]), it[2], cols[i], 2)
                                cv2.putText(frame, str(pos_number(i)),
                                            (it[0] - 8, it[1] - it[2] - 6),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, cols[i], 2)
                            cv2.putText(frame, f"K:{p3['stopped']} Q:{p3['q_sent']} tgt:{COLOR_EN.get(colors[0], colors[0])}", (20, 40),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                elif mode == MODE_P4:
                    #转盘：三目标取平均 cx = 盘心投影；先精调，再守坑位
                    items = find_targets(target_finder, frame, roi=TARGET_ROI)
                    if items is None:
                        tx.send("N")    #这个窗口没看到，电控只记日志
                    else:
                        w = CAMERA["width"]
                        mean_cx = sum(it[0] for it in items) / 3.0
                        dists = [m.distance(it[2]) for it in items]
                        if any(d is None for d in dists):
                            tx.send("N")
                        else:
                            mean_dist = sum(dists) / 3.0
                            if not p4["settled"]:
                                #精调阶段：发 O,±d 让车走一小步
                                if time.time() - p4["last_o"] >= P4["o_lock_s"]:
                                    off_cm = (mean_cx - w / 2.0) * mean_dist / m.f
                                    if (abs(off_cm) <= P4["off_tol_cm"]
                                            or p4["adjusted"] >= P4["max_adjust"]):
                                        p4["settled"] = True
                                        print("偏差已收敛 -> 开始守坑位")
                                    else:
                                        step = min(int(round(abs(off_cm))), P4["max_step_cm"])
                                        forward = (1 if off_cm > 0 else -1) * ADVANCE_SIGN
                                        sign = "+" if forward > 0 else "-"
                                        tx.send_now(f"O,{sign}{step}")
                                        p4["adjusted"] += 1
                                        p4["last_o"] = time.time()
                                        print(f"偏差 {off_cm:.1f}cm -> 发 O,{sign}{step}")
                            else:
                                #守坑位：目标色落到 B 坑就喊抓，车全程不动
                                #目标色是扫码得到的 colors[1]，单独找它，不依赖候选里的颜色名
                                b_tol = P4["b_tol_px"] if P4["b_tol_px"] is not None else w / 10.0
                                target = target_finder.find(frame, colors[1])
                                if target is None:
                                    p4["g_cx"] = None
                                    p4["g_stable"] = 0
                                else:
                                    cx = target[0]
                                    if (p4["g_cx"] is not None
                                            and abs(cx - p4["g_cx"]) <= P4["g_stable_px"]):
                                        p4["g_stable"] += 1
                                    else:
                                        p4["g_stable"] = 0
                                    p4["g_cx"] = cx
                                    if (p4["g_stable"] >= P4["g_stable_frames"]
                                            and abs(cx - w / 2.0) <= b_tol
                                            and time.time() - p4["last_g"] >= P4["g_lock_s"]):
                                        tx.send_now("G")
                                        p4["last_g"] = time.time()
                                        p4["g_stable"] = 0
                                        print("目标到 B 坑且稳定 -> 发 G")
                        if not HEADLESS:
                            cols = box_colors(target_finder, frame, items, colors[1], TARGET_ROI)
                            for i, it in enumerate(items):
                                cv2.circle(frame, (it[0], it[1]), it[2], cols[i], 2)
                            cv2.putText(frame, f"O:{p4['adjusted']} G:{p4['settled']} tgt:{COLOR_EN.get(colors[1], colors[1])}", (20, 40),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                elif mode == MODE_P5:
                    #安置区：与 p3 同一套，先判中间球居中发 K，停稳后再报指定色球在几号位
                    items = find_targets(zone_finder, frame, names=ZONE_ID.values(), roi=ZONE_ROI)
                    if items is None:
                        p5["centered"] = 0  #少一个球就不判，连计数清零
                    else:
                        mid = items[1]      #按 cx 排序取中间那个 = 2 号位球
                        ang = m.angle(mid[0])
                        if not p5["stopped"]:
                            if abs(ang) < STOP["center_deg"]:
                                p5["centered"] += 1
                                if p5["centered"] >= STOP["lock_frames"]:
                                    tx.send_now("K")
                                    p5["stopped"] = True
                                    print("安置区中间球居中 -> 发 K")
                            else:
                                p5["centered"] = 0
                        elif not p5["q_sent"]:
                            #二维码指定的安置区色小球单独找一次，再用它和最接近的候选对位置
                            target = zone_finder.find(frame, colors[2], roi=ZONE_ROI)
                            if target is not None:
                                best = min(range(len(items)),
                                           key=lambda i: abs(items[i][0] - target[0]))
                                gap = (items[2][0] - items[0][0]) / 2.0   #三球跨度的一半
                                if abs(items[best][0] - target[0]) <= gap:  #目标球真在候选里
                                    pos = pos_number(best)
                                    tx.send_now(f"Q,{pos}")
                                    p5["q_sent"] = True
                                    print(f"安置区 {colors[2]} 球在 {pos} 号位 -> 发 Q,{pos}")
                        if not HEADLESS:
                            cols = box_colors(zone_finder, frame, items, colors[2], ZONE_ROI)
                            for i, it in enumerate(items):
                                cv2.circle(frame, (it[0], it[1]), it[2], cols[i], 2)
                                cv2.putText(frame, str(pos_number(i)),
                                            (it[0] - 8, it[1] - it[2] - 6),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, cols[i], 2)
                            cv2.putText(frame, f"K:{p5['stopped']} Q:{p5['q_sent']} tgt:{COLOR_EN.get(colors[2], colors[2])}", (20, 40),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                #R 兜底：超过 200ms 没有真的发出周期报文才补一条
                if time.time() - last_data > 0.2:
                    if tx.send("R"):
                        last_data = time.time()

                if HEADLESS:
                    continue
                cv2.imshow("vision", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        tx.close()
        if not HEADLESS:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
