"""启动 → 打开摄像头 → 屏幕上实时显示画面
↑ 按 1~6 选颜色（红/黄/绿/浅蓝/深蓝/黑）
↑ 按 s    存一张到 logs/samples/<颜色>/
↑ 按 q    退出"""

import sys  # sys.exit(1)主动退出
import os  # 拼路径文件夹
import cv2
from datetime import datetime

from config.config import CAMERA, LOG_DIR, TARGET_ID  # 从config.config里面调取这三个参数
from capture.camera import Camera


def main():
    headless = sys.platform.startswith("linux") and os.environ.get("DISPLAY") is None
    #   判断两个条件同时成立：环境变量没有display，在linux板上
    if headless:
        print("record.py要看着画面按键,ssh下用不了,请连接到显示器")
        sys.exit(1)
    # 如果headless为空，退出画面：核心是盯着画面，东西摆正了按一下s，但没有屏幕的时候按不了

    # 选摄像头编号
    idx = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]
    samples_dir = os.path.join(LOG_DIR, "samples")
    color = None
    saved = 0  # 保存多少张
    with Camera(
        index=idx,
        width=CAMERA["width"],
        height=CAMERA["height"],
        fps=CAMERA["fps"],
        auto_exposure=CAMERA["auto_exposure"],
        exposure=CAMERA["exposure"],
        auto_wb=CAMERA["auto_wb"],
        warmup=CAMERA["warmup"],
    ) as cam:
        # as cam把打开的摄像头存成cam   idx是前面算出来的（判断是笔记本还是摄像头）
        print("按1-6选颜色,按s存图,按q退出")  # 打开摄像头后终端会提示
        while True:  # 表示条件恒为真，代码无法判断什么时候按q
            frame = cam.read()  # 调用camera.py第34行定义的read()
            if frame is None:
                continue
            disp = frame.copy()  # 复印frame放进disp里面
            tip = color if color is not None else "未选颜色"  # 有颜色显示颜色名，没有就显示未选颜色
            cv2.putText(
                disp,
                f"{tip}|已存{saved}|FPS{cam.fps:.1f}",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 0),
                2,
            )
            # 在disp上面写 红色|已存3|FPS 30.0 （举例子）
            cv2.imshow("record", disp)  # 送图到record去
            key = cv2.waitKey(1) & 0xFF  # 停1ms，把图画在屏幕上
            if key == ord("q"):
                break  # 按q时停止循环
            if ord("1") <= key <= ord("6"):
                color = TARGET_ID[key - ord("0")]
                print(
                    "当前颜色：", color
                )  # 如果按3，键盘给51，减去0的编号48，得到3再拿去target里查得到绿
            if key == ord("s"):
                if color is None:
                    print("还没选颜色，先按 1~6")
                    continue  # 要是你按了 s，先看有没有选颜色。没选就提醒一句，这一圈作废
                save_dir = os.path.join(samples_dir, color)
                # 把颜色名和日志目录拼起来 os.path.join负责拼接路径
                os.makedirs(save_dir, exist_ok=True)
                # 上一行算出地址，还不存在文件夹。后面true让他把已经存在当作正常情况
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                # 时间戳ts等于datetime.now读取现在的时间，里面那一坨是年月日时分秒，再切片掉微秒
                path = os.path.join(save_dir, f"{color}_{ts}.jpg")
                # 红_20261105_143022_123.jpg
                cv2.imwrite(path, frame)
                saved += 1
                print("已存", path)
        cv2.destroyAllWindows()
    print("共存", saved, "张")


if __name__ == "__main__":
    main()
