"""二维码预览：一个进程同时做三件事
   1) 打开摄像头  2) pyzbar 解码并在画面上画框  3) 推 MJPEG 到网页
   用法：python3 -m tools.qr_preview      （浏览器/OBS 打开 http://<板子IP>:8000/）
   终端会打印连续 3 帧锁定后的结果"""
import sys
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
from pyzbar.pyzbar import decode as zbar_decode, ZBarSymbol

from config.config import CAMERA, QR, BOMB_ID, TARGET_ID, ZONE_ID
from capture.camera import Camera
from recognize.qr import QrReader

PORT = 8000
QUALITY = 70
FRAME_SIZE = 45


class Latest:
    """只保留最新一帧，推流端慢就丢旧帧，不阻塞采集"""

    def __init__(self):
        self._cond = threading.Condition()
        self._jpg = None
        self._seq = 0

    def put(self, jpg):
        with self._cond:
            self._jpg = jpg
            self._seq += 1
            self._cond.notify_all()

    def get(self, last):
        with self._cond:
            while self._seq == last:
                self._cond.wait(1.0)
            return self._seq, self._jpg


LATEST = Latest()

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>qr</title>
<style>html,body{margin:0;height:100%;background:#111}
img{width:100%;height:100%;object-fit:contain;display:block}</style></head>
<body><img src="/stream"></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            last = -1
            try:
                while True:
                    last, jpg = LATEST.get(last)
                    if jpg is None:
                        continue
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                    self.wfile.write(jpg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_error(404)


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    idx = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]
    min_side = QR["min_side_px"]
    confirm = QR["confirm_frames"]

    with Camera(index=idx, width=CAMERA["width"], height=CAMERA["height"],
                fps=CAMERA["fps"], warmup=CAMERA["warmup"],
                auto_exposure=CAMERA["auto_exposure"], exposure=CAMERA["exposure"],
                auto_wb=CAMERA["auto_wb"]) as cam:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"浏览器/OBS 打开： http://{local_ip()}:{PORT}/")
        print("Ctrl+C 退出")

        hit = 0
        last_text = None
        locked = None

        try:
            while True:
                frame = cam.read()
                if frame is None:
                    continue

                results = zbar_decode(frame, symbols=[ZBarSymbol.QRCODE])
                for r in results:
                    x, y, w, h = r.rect
                    side = min(w, h)
                    color = (0, 255, 0) if side >= min_side else (0, 200, 255)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                    txt = r.data.decode("ascii", "ignore").strip() if r.data else ""
                    txt = "".join(ch for ch in txt if ch.isdigit())
                    if txt:
                        cv2.putText(frame, txt, (x, max(y - 8, 20)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                    if side < min_side or not txt:
                        continue
                    if not QrReader._valid(txt):
                        hit = 0
                        last_text = None
                        continue
                    if txt == last_text:
                        hit += 1
                    else:
                        last_text = txt
                        hit = 1
                    if hit >= confirm and txt != locked:
                        locked = txt
                        print(f"锁定 {locked} | 排爆{BOMB_ID[int(locked[0])]}"
                              f" | 救援{TARGET_ID[int(locked[1])]}"
                              f" | 安置{ZONE_ID[int(locked[2])]}")

                status = "未锁定" if locked is None else f"已锁定 {locked}"
                cv2.putText(frame, f"{status}  hit:{hit}", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
                if ok:
                    LATEST.put(buf.tobytes())
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    main()
