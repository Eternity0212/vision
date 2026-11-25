"""机械臂方案（车动臂不动）：相机固定在臂上，防爆区车开到球前逐个扫描

流程：
  M,0 阶段   车原地不动，臂伸长后定住；画面静止若干帧后才扫二维码
            锁定后发一次 T,A,B,C（与旧方案相同）
  M,1 阶段   车停到一颗球前，视觉只判离画面中线最近的那颗
            颜色 == 二维码 A 位颜色 → 绿框 + 发 G,1（电控执行抓取）
            颜色 != A 位颜色        → 红框 + 发 G,0（电控立即停臂，不许抓）
  其它模式    本文件不处理，只发心跳 R

每个球位只判一次：电控每开到一颗球前就发一次 M,1，视觉收到 M,1 就清空判定；
另外连续 clear_frames 帧看不到球也会自动解除（电控漏发 M,1 时的兜底）。
"""
import sys                                    #判断系统、读 DISPLAY 环境变量
import os                                     #读环境变量、拼路径
import time                                   #计时，心跳用

import cv2                                    #画框、显示窗口、转 HSV

from config.config import (CAMERA, BOMB_ID,    #相机参数、排爆色编号
                           BOMB_HSV, BOMB_ROI, #排爆区颜色阈值、检测区域
                           ARM, QR)            #判定参数（稳定帧数等）、扫码静止判据
from capture.camera import Camera             #打开摄像头、取帧
from recognize.qr import QrReader             #二维码识别
from recognize.color import ColorFinder       #按颜色找球
from link.serial import Tx                    #串口收发

#Linux 且没有 DISPLAY（SSH 连板子）就是无界面模式，不能开窗口
HEADLESS = sys.platform.startswith("linux") and os.environ.get("DISPLAY") is None
#摄像头编号：windows 用 index_win，linux 用 index_linux
IDX = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]

MODE_CLOSED = 0     #发车前：只扫二维码，不判球
MODE_P3 = 1         #防爆区：逐个扫描，判 G,1 / G,0

#画面上用英文显示，OpenCV 自带的 Hershey 字体写不了中文
COLOR_EN = {"蓝": "blue", "绿": "grn", "红": "red"}

GREEN = (0, 255, 0)     #绿框 = 目标物，可抓（BGR）
RED = (0, 0, 255)       #红框 = 不是目标，禁止抓
YELLOW = (0, 255, 255)  #黄框 = 还在稳定判定中，没下结论


def pick_one(finder, frame):
    """车停在球前时只认一颗：优先离画面中心最近的，中心附近没有才退回最大的一颗

    车动臂不动，三颗球可能同时在画面里。车是对着目标球停的，
    所以中线附近的才是“这一颗”；远处的球再大也不参与判定。
    返回 (颜色名, cx, cy, r)；一颗都没有返回 None
    """
    groups = finder.find_many(frame, list(finder.colors), roi=BOMB_ROI)  #一次扫描三种颜色
    w = frame.shape[1]                           #画面宽度，用来算中线
    near = None                                  #中线附近、半径最大的一颗
    far = None                                   #全画面半径最大的一颗（兜底）
    for name, items in groups.items():           #逐个颜色看结果
        for cx, cy, r, _ in items:               #每颗：圆心x、圆心y、半径，第4个值用不到
            if far is None or r > far[3]:        #全画面最大的一颗
                far = (name, cx, cy, r)
            if abs(cx - w / 2.0) <= w * ARM["center_tol"]:   #圆心在中线附近
                if near is None or r > near[3]:              #中线附近的取最大的
                    near = (name, cx, cy, r)
    return near if near is not None else far     #优先中线那颗，实在没有就取最大的


def center_hsv(frame, cx, cy):
    """取球心 5x5 小块的 HSV 中值，只用于画面调试显示"""
    hsv = cv2.cvtColor(frame[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3],  #裁 5x5 小块
                       cv2.COLOR_BGR2HSV)                                    #转成 HSV
    flat = hsv.reshape(-1, 3)                    #铺平成 25 行 3 列（H/S/V）
    if len(flat) == 0:                           #贴边时可能裁出空块
        return (0, 0, 0)                         #返回全 0，别崩
    return tuple(int(v) for v in flat[len(flat) // 2])   #取中间那个像素当代表值


def frame_still(prev_small, frame):
    """判断画面动没动：两帧都缩成 160x90 灰度图，比平均差

    返回 (平均差, 这一帧的缩略图)。第一帧没有对比对象，差返回 None。
    平均差是 0~255，越小越静止；臂伸长、车在走时这个值会明显变大。
    """
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)                  #转灰度
    g = cv2.resize(g, (160, 90), interpolation=cv2.INTER_AREA)   #缩成小图，算得快
    if prev_small is None:                                       #第一帧没得比
        return None, g                                           #只留缩略图，差返回空
    return float(cv2.absdiff(prev_small, g).mean()), g           #和上一帧逐像素相减取平均


def main():
    qr = QrReader()                              #二维码识别器（主程序用慢速远距模式）
    finder = ColorFinder(BOMB_HSV)               #排爆区专用的颜色表
    tx = Tx().open()                             #打开串口；没接电控也不会崩，只打印一行

    ids = None                                   #二维码三位数字
    want = None                                  #二维码 A 位对应的颜色名（排爆目标色）
    mode = MODE_CLOSED                           #一开始当发车前处理
    last_data = time.time()                      #上次发心跳的时刻
    prev_small = None                            #上一帧的灰度缩略图，用来判画面静止
    still = 0                                    #画面连续静止的帧数

    #判定状态：当前这颗球连续稳定了几帧、有没有已经发过 G
    st = {"name": None,                          #上一帧的颜色名
          "cx": 0, "cy": 0, "r": 0,              #上一帧的圆心和半径
          "stable": 0,                           #连续稳定帧数
          "sent": False,                         #这个位置是否已经发过 G
          "ok": None,                            #发出去的是 G,1(True) 还是 G,0(False)
          "miss": 0}                             #连续看不到球的帧数

    def reset():
        """清空判定：位置换了、或者球离开画面时调用"""
        st.update(name=None, stable=0, sent=False, ok=None, miss=0)  #一次性归零

    try:
        with Camera(index=IDX,                   #摄像头编号
                    width=CAMERA["width"],       #宽 1280
                    height=CAMERA["height"],     #高 720
                    fps=CAMERA["fps"],           #帧率 30
                    warmup=CAMERA["warmup"],     #先丢几帧，避开刚打开的过曝
                    auto_exposure=CAMERA["auto_exposure"],  #关自动曝光
                    exposure=CAMERA["exposure"],            #固定曝光 150
                    auto_wb=CAMERA["auto_wb"]) as cam:      #关自动白平衡
            while True:                          #主循环，一直跑到退出
                frame = cam.read()               #取一帧
                if frame is None:                #偶尔读失败
                    continue                     #直接重来，不往下走

                cmd = tx.read_cmd()              #看电控有没有下发命令
                if cmd is not None and cmd.startswith("M,"):    #只关心 M 开头的模式命令
                    try:
                        new_mode = int(cmd.split(",")[1])       #逗号后面那个数字
                    except (ValueError, IndexError):            #命令格式不对
                        new_mode = mode                         #当成没变
                    if new_mode in (MODE_CLOSED, MODE_P3):      #只认 0 和 1
                        if new_mode != mode:                    #真的换阶段才打印
                            mode = new_mode                     #记下新模式
                            print(f"切换阶段 -> {mode}")         #屏幕上留个记录
                        if new_mode == MODE_P3:                 #收到 M,1 就清空
                            reset()                             #允许在新位置重新判一次
                    elif new_mode != mode:                      #2/3 等还没实现的模式
                        mode = new_mode                         #记住，避免反复打印
                        print(f"模式 {mode} 本文件未实现，只发心跳 R")   #提示一下

                if want is None and mode == MODE_CLOSED:    #还没锁码，且在发车前
                    diff, prev_small = frame_still(prev_small, frame)   #和上一帧比，判断画面动没动
                    if diff is not None and diff <= QR["still_diff"]:   #几乎没变化
                        still += 1                      #静止帧数加一
                    else:                               #还在动（臂在伸、车没停好）
                        still = 0                       #重新计数
                    if still >= QR["still_frames"]:     #连续静止够久，说明臂已经定住
                        code = qr.feed(frame)           #这时才把画面喂给二维码解码
                        if code is not None:            #解出来了
                            ids = [int(code[0]), int(code[1]), int(code[2])]  #拆成三位数字
                            want = BOMB_ID[ids[0]]      #A 位转成颜色名，比如 1->蓝
                            tx.send_now(f"T,{ids[0]},{ids[1]},{ids[2]}")      #立刻告诉电控
                            print(f"锁定 {code} -> 排爆目标色 {want}")        #本地留记录
                    if not HEADLESS:                    #有界面时把静止状态显示出来
                        cv2.putText(frame, f"still:{still}/{QR['still_frames']} diff:{-1 if diff is None else round(diff, 1)}",
                                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, GREEN, 2)

                elif want is not None and mode == MODE_P3:  #已锁码，且进入防爆区
                    det = pick_one(finder, frame)           #这一帧最大的一颗球

                    if det is None:                         #画面里没有球
                        st["miss"] += 1                     #漏检帧数加一
                        st["stable"] = 0                    #稳定计数清零
                        st["name"] = None                   #颜色也清掉
                        if st["miss"] >= ARM["clear_frames"] and st["sent"]:  #连续看不到且之前发过
                            reset()                         #算臂挪走了，允许下一次判定
                    else:                                   #画面里有球
                        name, cx, cy, r = det               #拆出颜色名、圆心、半径
                        st["miss"] = 0                      #漏检清零
                        if (st["name"] == name              #颜色和上一帧一样
                                and abs(cx - st["cx"]) <= ARM["stable_px"]   #圆心横移很小
                                and abs(cy - st["cy"]) <= ARM["stable_px"]   #圆心竖移很小
                                and abs(r - st["r"]) <= ARM["stable_r"]):    #半径变化很小
                            st["stable"] += 1               #稳定帧数加一
                        else:                               #颜色/位置/大小有明显变化
                            st["stable"] = 1                #重新从 1 开始数
                        st.update(name=name, cx=cx, cy=cy, r=r)   #把这一帧记成新基准

                        if st["stable"] >= ARM["stable_frames"] and not st["sent"]:  #够 5 帧且没发过
                            st["ok"] = (name == want)       #颜色对上二维码 A 位就是 True
                            st["sent"] = True               #标记这个位置已判过
                            tx.send_now("G,1" if st["ok"] else "G,0")    #可抓 / 禁止抓
                            print(f"{name} == {want}?"      #打印判定过程
                                  f" {'是 -> G,1' if st['ok'] else '否 -> G,0'}")

                    if not HEADLESS:                        #有界面才画框
                        if det is not None:                 #有球才画
                            name, cx, cy, r = det           #再拆一次，画图用
                            if st["sent"]:                  #已经判过了
                                col = GREEN if st["ok"] else RED     #对就绿，不对就红
                            else:                           #还在稳定判定中
                                col = YELLOW                #先黄框
                            cv2.circle(frame, (cx, cy), r, col, 3)   #按球的外接圆画圈
                            h, s, v = center_hsv(frame, cx, cy)      #球心 HSV，调参用
                            cv2.putText(frame, f"{COLOR_EN.get(name, name)} r={r} H{h} S{s} V{v}",  #框上写颜色/半径/HSV
                                        (max(10, cx - r), max(20, cy - r - 10)),   #写在框左上，别出画面
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)     #字体、大小、颜色、粗细
                        txt = (f"mode:{mode} tgt:{COLOR_EN.get(want, want)} "      #顶部状态：模式
                               f"stable:{st['stable']} sent:{st['sent']}")        #当前稳定帧数、是否已发
                        cv2.putText(frame, txt, (20, 40),                        #画在左上角
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, GREEN, 2)     #绿色粗体

                #R 心跳：200ms 一条（send 本身 10Hz 节流）
                if time.time() - last_data > 0.2:                   #距上次超过 0.2 秒
                    if tx.send("R"):                                #真发出去了
                        last_data = time.time()                     #才更新时间戳

                if not HEADLESS:                                    #有界面
                    cv2.imshow("arm", frame)                        #显示这一帧
                    if (cv2.waitKey(1) & 0xFF) == ord("q"):         #按 q 退出
                        break                                       #跳出主循环
    finally:                                                        #不管正常退出还是报错
        tx.close()                                                  #都要关串口
        if not HEADLESS:                                            #有界面的话
            cv2.destroyAllWindows()                                 #把窗口关掉


if __name__ == "__main__":      #直接运行本文件时
    main()                      #才启动主程序
