import cv2                                  # 主程序自测时用来显示画面
from pyzbar.pyzbar import decode as zbar_decode, ZBarSymbol
from config.config import QR, BOMB_ID, TARGET_ID, ZONE_ID


class QrReader:
    def __init__(self,min_side_px=None,confirm_frames=None):
        self.min_side_px=QR["min_side_px"]  if min_side_px is None else min_side_px
        self.confirm_frames = QR["confirm_frames"] if confirm_frames is None else confirm_frames 
        #如果有就用没有就用我传的
        self._hit=0 #连续命中计数器
        self._last=None #上一帧解出来原文
        self.text=None #已经确定的码
    def feed(self,frame):
        try:
            results=zbar_decode(frame,symbols=[ZBarSymbol.QRCODE])
        except Exception:
            self._hit=0
            return None
        if not results:
            self._hit=0
            return None
        best=max(results,key=lambda r:r.rect.width*r.rect.height)
        side=min(best.rect.width,best.rect.height)
        if side<self.min_side_px:
            self._hit=0 #太小了解不出来
            return None
        text=best.data.decode("ascii","ignore").strip()   #去掉首尾空白
        text="".join(ch for ch in text if ch.isdigit())
        if not text:
            self._hit=0
            return None
        if not self._valid(text):
            self._hit=0
            return None     #不是三位1-6直接扔
        if text==self._last:    #连续命中
            self._hit+=1
        else:
            self._last=text
            self._hit=1
        if self._hit>=self.confirm_frames:
            self.text=text
            return text
        return None
    @staticmethod
    def _valid(text):
        if len(text)!=3 or not text.isdigit():
            return False    #不是三位纯数字就跳过
        return (int(text[0]) in BOMB_ID
                and int(text[1]) in TARGET_ID
                and int(text[2]) in ZONE_ID)
if __name__ == "__main__":
    import os
    import sys
    from capture.camera import Camera
    from config.config import CAMERA

    headless = sys.platform.startswith("linux") and os.environ.get("DISPLAY") is None
    idx = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]
    qr = QrReader()
    with Camera(index=idx,
                width=CAMERA["width"],
                height=CAMERA["height"],
                fps=CAMERA["fps"],
                warmup=CAMERA["warmup"],
                auto_exposure=CAMERA["auto_exposure"],
                exposure=CAMERA["exposure"],
                auto_wb=CAMERA["auto_wb"]) as cam:
        last_print = None                          # 同一个码不重复刷屏
        while True:
            frame = cam.read()  #要一帧画面存进frame
            if frame is None:
                continue
            code = qr.feed(frame)   #把这一帧塞进feed--认出编码 or 没认出编码
            #如果存在code且code和上一次打印的不同
            if code is not None and code != last_print: #防止253不停刷屏
                print("码:", code,
                    "| 排爆物:", BOMB_ID[int(code[0])],
                    "| 优先救援:", TARGET_ID[int(code[1])],
                    "| 安置区:", ZONE_ID[int(code[2])])
                last_print = code
            if headless:
                continue
            cv2.imshow("qr", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    if not headless:
        cv2.destroyAllWindows()