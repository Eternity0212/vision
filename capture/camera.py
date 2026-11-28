import sys  # 判断windows还是linux
import cv2
import time
import os  # 判断有没有图形界面（SSH 连板子时没有 DISPLAY）


class Camera:
    def __init__(
        self,
        index=0,
        width=640,
        height=480,
        fps=30,
        warmup=5,
        auto_exposure=1,
        exposure=-5,
        auto_wb=0,
    ):
        self.index = index  # 第几个摄像头，多个摄像头则要选0/1/2
        self.width = width  # 画面宽多少像素
        self.height = height  # 高多少像素
        self.target_fps = fps
        self.warmup = warmup  # 打开后先扔掉5张
        self.auto_exposure = auto_exposure
        self.exposure = exposure
        self.auto_wb = auto_wb
        self.cap = None  # 占位，默认没有摄像头
        self._fps = 0.0  # 初始值为0
        self._last = time.time()
        self._fail = 0

    def open(self):  # 打开摄像头
        backend = (
            cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_V4L2
        )  # 如果是windows，就用dshow，如果是startwith（"win")
        self.cap = cv2.VideoCapture(self.index, backend)  # 创建摄像头对象
        if not self.cap.isOpened():  # 如果没有开好摄像头
            raise RuntimeError(f"摄像头{self.index}打开失败")
        self.cap.set(
            cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")
        )  # 该摄像头只支持MJPG，不显式指定会打开失败
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)  # cap 采集
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)  # prop 属性
        self.cap.set(
            cv2.CAP_PROP_FPS, self.target_fps
        )  # frame 一帧画面 意思是采集属性里一帧的宽高帧率
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 只留一帧缓存，处理慢时不会读到几秒前的旧画面
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, self.auto_exposure)
        self.cap.set(cv2.CAP_PROP_AUTO_WB, self.auto_wb)
        self.cap.set(cv2.CAP_PROP_EXPOSURE, self.exposure)
        for _ in range(self.warmup):
            self.cap.read()  # 预热
        self._last = time.time()
        return self

    def read(self):
        if self.cap is None:
            raise RuntimeError("摄像头未打开,先调用open()")
        ok, frame = self.cap.read()  # 是否成功和图像两个值
        if not ok:
            self._fail += 1
            if self._fail >= 5:
                raise RuntimeError("连续5帧读取失败，摄像头可能断开")
            return None
        self._fail = 0
        now = time.time()  # 读取现在的时间
        dt = max(now - self._last, 1e-6)  # 时间差为现在时间-上次读的时间
        inst = 1.0 / dt
        self._fps = inst if self._fps == 0.0 else 0.9 * self._fps + 0.1 * inst
        self._last = now
        return frame

    @property
    def fps(self):
        return self._fps

    def info(self):
        return {
            "width": int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": round(self.cap.get(cv2.CAP_PROP_FPS), 1),
        }

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()


if __name__ == "__main__":
    headless = sys.platform.startswith("linux") and os.environ.get("DISPLAY") is None
    from config.config import CAMERA

    idx = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]
    with Camera(
        index=idx,
        width=CAMERA["width"],
        height=CAMERA["height"],
        fps=CAMERA["fps"],
        warmup=CAMERA["warmup"],
        auto_exposure=CAMERA["auto_exposure"],
        exposure=CAMERA["exposure"],
        auto_wb=CAMERA["auto_wb"],
    ) as cam:
        print("摄像头实际参数：", cam.info())
        n = 0
        while True:
            frame = cam.read()
            if frame is None:
                continue
            n += 1
            if headless:  # SSH 连板子：没窗口，改成每 30 帧打一行
                if n % 30 == 0:
                    print(f"已读{n}帧,实测FPS {cam.fps:.1f}")
                continue
            cv2.putText(
                frame, f"FPS {cam.fps:.1f}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2
            )
            cv2.imshow("camera", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):  # 按 s 存一张图，用来查花屏/过曝
                name = time.strftime("capture_%Y%m%d_%H%M%S.jpg")
                print("已保存", name, cv2.imwrite(name, frame))
        if not headless:
            cv2.destroyAllWindows()
