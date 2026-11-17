"""摄像头 MJPEG 推流：浏览器或 OBS 打开 http://<板子IP>:8000/ 就能看到实时画面
   用法：python3 -m tools.stream        （Ctrl+C 退出）"""
import sys
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

from config.config import CAMERA
from capture.camera import Camera

PORT = 8000
QUALITY = 70

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>vision</title>
<style>html,body{margin:0;height:100%;background:#111}
img{width:100%;height:100%;object-fit:contain;display:block}</style></head>
<body><img src="/stream"></body></html>"""


class Handler(BaseHTTPRequestHandler):
    cam = None

    def log_message(self, *args):
        pass  # 不刷屏

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
            try:
                while True:
                    frame = self.cam.read()
                    if frame is None:
                        continue
                    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
                    if not ok:
                        continue
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                    self.wfile.write(buf.tobytes())
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass  # 浏览器关页面就退出这个连接
        else:
            self.send_error(404)


def local_ip():
    """取本机对外网卡的 IP（不会真的发包，也不需要 DNS）"""
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
    with Camera(index=idx, width=CAMERA["width"], height=CAMERA["height"],
                fps=CAMERA["fps"], warmup=CAMERA["warmup"],
                auto_exposure=CAMERA["auto_exposure"], exposure=CAMERA["exposure"],
                auto_wb=CAMERA["auto_wb"]) as cam:
        Handler.cam = cam
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        print(f"浏览器/OBS 打开： http://{local_ip()}:{PORT}/")
        print("Ctrl+C 退出（同一时刻只能有一个程序占用摄像头）")
        try:
            srv.serve_forever()
        finally:
            srv.server_close()


if __name__ == "__main__":
    main()
