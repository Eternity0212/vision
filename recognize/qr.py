import threading
import time

import cv2  # 主程序自测时用来显示画面
from pyzbar.pyzbar import decode as zbar_decode, ZBarSymbol
from config.config import QR, BOMB_ID, TARGET_ID, ZONE_ID

try:  # 小码/远距离时 zbar 解不出，用 zxing 兜底
    import zxingcpp
except Exception:  # 没装就退回只用 zbar
    zxingcpp = None


class QrReader:
    def __init__(self, min_side_px=None, confirm_frames=None, slow=True):
        self.min_side_px = QR["min_side_px"] if min_side_px is None else min_side_px
        self.confirm_frames = QR["confirm_frames"] if confirm_frames is None else confirm_frames
        # 如果有就用没有就用我传的
        self._slow_enabled = slow  # 预览里关掉，3倍放大解码太吃CPU，会把画面拖到卡顿
        self._hit = 0  # 连续命中计数器
        self._last = None  # 上一帧解出来原文
        self.text = None  # 已经确定的码
        self.last_box = None  # 最近一次识别框 (x,y,w,h)，给预览画框用
        self.last_text = None  # 最近一次解出的数字串
        self.last_side = 0  # 最近一次识别框短边
        # zbar 对远处小码无能为力，zxing 跑得慢，放后台线程，避免卡住主循环
        self._slow_lock = threading.Lock()
        self._slow_result = None
        self._slow_busy = False
        self._slow_last = 0.0
        self._slow_interval = 1.0  # 两次兜底扫描的最小间隔（秒）
        self._slow_scale = 3  # 兜底扫描前先放大倍数

    @property
    def hits(self):
        return self._hit

    def feed(self, frame):
        if self.text is not None:  # 已锁定就不再重复解码
            return self.text
        # 1) 先看后台兜底线程有没有结果
        slow = None
        with self._slow_lock:
            if self._slow_result is not None:
                slow = self._slow_result
                self._slow_result = None
                self._slow_busy = False
        if slow is not None:
            text, box = slow
            if box is not None:
                self.last_box = box
                self.last_side = min(box[2], box[3])
            self.last_text = text
            if self._valid(text) and self.last_side >= self.min_side_px:
                self.text = text
                self._hit = self.confirm_frames
                return text
        # 2) 本帧走 zbar 快路径
        try:
            results = zbar_decode(frame, symbols=[ZBarSymbol.QRCODE])
        except Exception:
            results = []
        got = False
        if results:
            best = max(results, key=lambda r: r.rect.width * r.rect.height)
            x, y, w, h = best.rect
            side = min(w, h)
            text = best.data.decode("ascii", "ignore").strip()  # 去掉首尾空白
            text = "".join(ch for ch in text if ch.isdigit())
            self.last_box = (x, y, w, h)
            self.last_text = text or None
            self.last_side = side
            if side >= self.min_side_px and text and self._valid(text):
                got = True
                if text == self._last:  # 连续命中
                    self._hit += 1
                else:
                    self._last = text
                    self._hit = 1
                if self._hit >= self.confirm_frames:
                    self.text = text
                    return text
        if not got:
            self._hit = 0  # 这一帧没有有效结果
            self._start_slow(frame)
        return None

    def _start_slow(self, frame):
        """zbar 没结果时，起一个后台线程用 zxing 找小码，不阻塞主循环"""
        if zxingcpp is None or not self._slow_enabled or self._slow_busy:
            return
        if time.time() - self._slow_last < self._slow_interval:
            return
        self._slow_last = time.time()
        self._slow_busy = True
        threading.Thread(target=self._slow_scan, args=(frame.copy(),), daemon=True).start()

    def _slow_scan(self, frame):
        result = None
        try:
            scale = self._slow_scale
            big = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            for r in zxingcpp.read_barcodes(big):
                text = "".join(ch for ch in r.text if ch.isdigit())
                if not self._valid(text):
                    continue
                result = (text, self._box_from_position(r, scale, frame.shape))
                break
        except Exception:
            result = None
        finally:
            with self._slow_lock:
                self._slow_result = result
                self._slow_busy = False

    @staticmethod
    def _box_from_position(r, scale, shape):
        try:
            p = r.position
            xs = [p.top_left.x, p.top_right.x, p.bottom_right.x, p.bottom_left.x]
            ys = [p.top_left.y, p.top_right.y, p.bottom_right.y, p.bottom_left.y]
            x0 = max(0, int(min(xs) / scale))
            y0 = max(0, int(min(ys) / scale))
            x1 = min(shape[1], int(max(xs) / scale))
            y1 = min(shape[0], int(max(ys) / scale))
            return (x0, y0, x1 - x0, y1 - y0)
        except Exception:
            return None

    @staticmethod
    def _valid(text):
        if len(text) != 3 or not text.isdigit():
            return False  # 不是三位纯数字就跳过
        return int(text[0]) in BOMB_ID and int(text[1]) in TARGET_ID and int(text[2]) in ZONE_ID


if __name__ == "__main__":
    import os
    import sys
    from capture.camera import Camera
    from config.config import CAMERA

    headless = sys.platform.startswith("linux") and os.environ.get("DISPLAY") is None
    idx = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]
    qr = QrReader()
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
        last_print = None  # 同一个码不重复刷屏
        while True:
            frame = cam.read()  # 要一帧画面存进frame
            if frame is None:
                continue
            code = qr.feed(frame)  # 把这一帧塞进feed--认出编码 or 没认出编码
            # 如果存在code且code和上一次打印的不同
            if code is not None and code != last_print:  # 防止253不停刷屏
                print(
                    f"码:{code} | 排爆物:{BOMB_ID[int(code[0])]} | "
                    f"优先救援:{TARGET_ID[int(code[1])]} | "
                    f"安置区:{ZONE_ID[int(code[2])]}"
                )
                last_print = code
            if headless:
                continue
            cv2.imshow("qr", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    if not headless:
        cv2.destroyAllWindows()
