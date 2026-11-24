"""颜色预览：实时框出某一组（排爆/救援/安置）里的所有颜色，方便现场量阈值
   用法：python3 -m tools.color_preview [auto|bomb|target|zone]（默认自动判区域）
   浏览器打开 http://<板子IP>:8001/ ，页面上可切换三组/自动、可直接在画面上框检测区域
   每个框上会显示 颜色名 / 半径 / 中心 HSV，反光时看 V 最直观"""
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import cv2

from config.config import (CAMERA, BOMB_HSV, TARGET_HSV, ZONE_HSV,
                           BOMB_ROI, TARGET_ROI, ZONE_ROI,
                           BOMB_ID, TARGET_ID, ZONE_ID)
from capture.camera import Camera
from recognize.color import ColorFinder
from recognize.qr import QrReader

PORT = 8001
QUALITY = 50
PREVIEW_W, PREVIEW_H = 800, 450   #推流分辨率：识别仍在 1280x720 上做，只在压缩前缩小，省带宽防画面滞后

GROUPS = {"bomb": BOMB_HSV, "target": TARGET_HSV, "zone": ZONE_HSV}
NAMES = {"bomb": "排爆物", "target": "救援目标", "zone": "安置区", "auto": "自动"}
CURRENT = sys.argv[1] if len(sys.argv) > 1 and (sys.argv[1] in GROUPS or sys.argv[1] == "auto") else "auto"
ROI_STATE = {"bomb": BOMB_ROI, "target": TARGET_ROI, "zone": ZONE_ROI}   #页面上框选后只改这里
GROUP_DIGIT = {"bomb": 0, "target": 1, "zone": 2}   #二维码三位分别对应三个区
GROUP_IDS = {"bomb": BOMB_ID, "target": TARGET_ID, "zone": ZONE_ID}

AUTO_GROUP = "target"   #自动模式当前判定的区域
_auto_vote = None
_auto_votes = 0


class Latest:
    """只保留最新一帧，推流端慢就丢旧帧"""

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

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>color</title>
<style>html,body{margin:0;height:100%;background:#111;color:#eee;font-family:sans-serif}
#bar{padding:6px 8px}button{margin-right:6px;padding:4px 10px}
#wrap{position:relative;height:calc(100% - 44px);background:#000}
#v{width:100%;height:100%;object-fit:contain;display:block}
#sel{position:absolute;border:2px dashed #ffe000;display:none;pointer-events:none}
#tip{position:absolute;left:10px;bottom:8px;color:#ffe000;font-size:13px;display:none}
</style></head>
<body><div id="bar">
<button data-g="auto">自动</button>
<button data-g="bomb">排爆物</button>
<button data-g="target">救援目标</button>
<button data-g="zone">安置区</button>
<button id="pick">框选区域</button>
<button id="clear">清除区域</button>
<b id="g">__GROUP__</b>
</div>
<div id="wrap"><img id="v" src="/stream"><div id="sel"></div>
<div id="tip">在画面上点两下：左上角 → 右下角</div></div>
<script>
var g="__GROUP_KEY__",picking=false,p1=null,SW=__SW__,SH=__SH__;
var img=document.getElementById('v'),sel=document.getElementById('sel'),tip=document.getElementById('tip');
function geom(){var r=img.getBoundingClientRect();var s=Math.min(r.width/SW,r.height/SH);
return {s:s,ox:(r.width-SW*s)/2,oy:(r.height-SH*s)/2};}
function set(x){g=x;fetch('/set?group='+x);document.getElementById('g').textContent=
document.querySelector('button[data-g="'+x+'"]').textContent;}
document.querySelectorAll('button[data-g]').forEach(function(b){
b.onclick=function(){set(b.getAttribute('data-g'));};});
document.getElementById('pick').onclick=function(){picking=!picking;p1=null;
sel.style.display='none';tip.style.display=picking?'block':'none';
this.style.background=picking?'#ffe000':'';};
document.getElementById('clear').onclick=function(){fetch('/roi_clear?group='+g);};
function show(a,b){var q=geom();var x1=Math.min(a[0],b[0]),y1=Math.min(a[1],b[1]),
x2=Math.max(a[0],b[0]),y2=Math.max(a[1],b[1]);
sel.style.left=(q.ox+x1*q.s)+'px';sel.style.top=(q.oy+y1*q.s)+'px';
sel.style.width=((x2-x1)*q.s)+'px';sel.style.height=((y2-y1)*q.s)+'px';sel.style.display='block';}
function at(e){var r=img.getBoundingClientRect(),q=geom();
return [Math.round((e.clientX-r.left-q.ox)/q.s),Math.round((e.clientY-r.top-q.oy)/q.s)];}
img.addEventListener('click',function(e){
if(!picking)return;var p=at(e);
if(p[0]<0||p[1]<0||p[0]>SW||p[1]>SH)return;
if(!p1){p1=p;show(p1,p1);}
else{var a=[Math.min(p1[0],p[0]),Math.min(p1[1],p[1])],b=[Math.max(p1[0],p[0]),Math.max(p1[1],p[1])];
var kx=1280/SW,ky=720/SH;
fetch('/roi?group='+g+'&x1='+Math.round(a[0]*kx)+'&y1='+Math.round(a[1]*ky)+
'&x2='+Math.round(b[0]*kx)+'&y2='+Math.round(b[1]*ky));
picking=false;p1=null;sel.style.display='none';tip.style.display='none';
document.getElementById('pick').style.background='';}
});
img.addEventListener('mousemove',function(e){
if(!picking||!p1)return;var p=at(e);
show(p1,[Math.max(0,Math.min(SW,p[0])),Math.max(0,Math.min(SH,p[1]))]);});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global CURRENT
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            body = (PAGE.replace("__GROUP__", NAMES.get(CURRENT, CURRENT))
                        .replace("__GROUP_KEY__", CURRENT)
                        .replace("__SW__", str(PREVIEW_W))
                        .replace("__SH__", str(PREVIEW_H))).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/set":
            g = q.get("group", [""])[0]
            if g in GROUPS or g == "auto":
                CURRENT = g
            self.send_response(204)
            self.end_headers()
        elif u.path == "/roi":
            g = q.get("group", [""])[0]
            if g == "auto":
                g = AUTO_GROUP
            try:
                x1, y1, x2, y2 = [int(q[k][0]) for k in ("x1", "y1", "x2", "y2")]
            except (KeyError, ValueError):
                x1 = y1 = x2 = y2 = -1
            if g in GROUPS and 0 <= x1 < x2 <= 1280 and 0 <= y1 < y2 <= 720:
                ROI_STATE[g] = (x1, y1, x2, y2)
                print(f"{g} ROI = {(x1, y1, x2, y2)}", flush=True)
            self.send_response(204)
            self.end_headers()
        elif u.path == "/roi_clear":
            g = q.get("group", [""])[0]
            if g == "auto":
                g = AUTO_GROUP
            if g in GROUPS:
                ROI_STATE[g] = None
                print(f"{g} ROI 已清除", flush=True)
            self.send_response(204)
            self.end_headers()
        elif u.path == "/stream":
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
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
            except Exception:
                pass
        else:
            self.send_response(404)
            self.end_headers()


LABEL = {"红": "red", "黄": "yel", "绿": "grn", "浅蓝": "lblue", "深蓝": "dblue", "蓝": "blue", "黑": "blk"}


def detect(frame, group):
    """检出该组里半径最大的三个目标，返回 (颜色名,cx,cy,r,填充率)"""
    finder = ColorFinder(GROUPS[group])
    roi = ROI_STATE.get(group)
    groups = finder.find_many(frame, finder.colors, roi=roi)   #一次 HSV 转换扫完
    cands = []
    for name in finder.colors:
        for cx, cy, r, fill in groups[name]:
            if any(abs(c[1] - cx) < r and abs(c[2] - cy) < r for c in cands):
                continue
            cands.append((name, cx, cy, r, fill))
    cands.sort(key=lambda t: t[3], reverse=True)
    return cands[:3]


def is_turntable(frame):
    """看到又大又白、近似圆的转盘就判成救援区（三个圆台只出现在这里）
       用降采样 + 霍夫圆找大盘面，再确认圆心附近确实是白色，避免误判"""
    small = cv2.resize(frame, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (7, 7), 1.5)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.5, minDist=80,
                               param1=120, param2=60, minRadius=90, maxRadius=320)
    if circles is None:
        return False
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h = small.shape[0]
    for x, y, r in circles[0]:
        if y < h * 0.25:        #盘心不会在画面最上面
            continue
        patch = hsv[max(0, int(y) - 6):int(y) + 7, max(0, int(x) - 6):int(x) + 7]
        if patch.size == 0:
            continue
        _, s, v = patch.reshape(-1, 3).mean(0)
        if s < 100 and v > 120:     #圆心是白色
            return True
    return False


def resolve_auto(frame):
    """自动判区域：先看白色转盘（救援区独有），没有再按三组颜色去比
       转盘上的圆台比背景杂物大，按“三个目标的半径之和”比大小，
       只要小球组不比转盘小太多，就优先判成排爆/安置（避免把圆台判成小球）"""
    global AUTO_GROUP, _auto_vote, _auto_votes
    if is_turntable(frame):
        pick = "target"
    else:
        scores = {}
        for g in GROUPS:
            c = detect(frame, g)
            if len(c) == 3:
                rs = [t[3] for t in c]
                #三个同规格目标在画面里大小应该接近；差距过大多半混进了大块杂物（人腿、桌椅）
                scores[g] = sum(rs) if max(rs) <= 2.0 * min(rs) else -1
            else:
                scores[g] = -1
        pick = max(scores, key=scores.get)
        if scores[pick] < 0:        #这一帧哪组都凑不齐三个目标，保持当前判定
            _auto_vote = None
            _auto_votes = 0
            return
        if pick == "target":        #小球组尺寸接近转盘时，优先小球
            for g in ("bomb", "zone"):
                if scores[g] >= 0.8 * scores["target"]:
                    pick = g
                    break
    if pick == _auto_vote:
        _auto_votes += 1
    else:
        _auto_vote = pick
        _auto_votes = 1
    if _auto_votes >= 2 and pick != AUTO_GROUP:   #连续两帧一致才切，防抖
        AUTO_GROUP = pick
        print(f"自动识别区域 -> {pick}", flush=True)


def draw(frame, group, ids=None, auto=False):
    """只标半径最大的三个目标，并按二维码结果上色：
       黄=候选（二维码还没对上），绿=二维码指定的目标，红=其余两个"""
    roi = ROI_STATE.get(group)
    want = None
    if ids is not None:
        want = GROUP_IDS[group][ids[GROUP_DIGIT[group]]]
    if roi is not None:
        cv2.rectangle(frame, (roi[0], roi[1]), (roi[2], roi[3]), (0, 255, 255), 1)
    cands = detect(frame, group)
    cands.sort(key=lambda t: t[1])                  #按 cx 从左到右
    matched = want if any(c[0] == want for c in cands) else None
    for name, cx, cy, r, _ in cands:
        if matched is None:
            col = (0, 255, 255)     # 黄：候选
        elif name == matched:
            col = (0, 255, 0)       # 绿：二维码指定的目标
        else:
            col = (0, 0, 255)       # 红：其余
        cv2.circle(frame, (cx, cy), r, col, 2)
        patch = cv2.cvtColor(frame[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3],
                             cv2.COLOR_BGR2HSV)     #只转这一小块，省 CPU
        h, s, v = [int(x) for x in patch.reshape(-1, 3).mean(0)]
        cv2.putText(frame, f"{LABEL.get(name, name)} r={r} H{h} S{s} V{v}",
                    (max(5, cx - r), max(20, cy - r - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
    tag = "QR:none" if ids is None else f"QR:{''.join(str(i) for i in ids)} target:{LABEL.get(want, want)}"
    head = f"auto->{group}" if auto else group
    cv2.putText(frame, f"{head} found:{len(cands)}  {tag}", (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)


def main():
    idx = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]
    with Camera(index=idx,
                width=CAMERA["width"],
                height=CAMERA["height"],
                fps=CAMERA["fps"],
                warmup=CAMERA["warmup"],
                auto_exposure=CAMERA["auto_exposure"],
                exposure=CAMERA["exposure"],
                auto_wb=CAMERA["auto_wb"]) as cam:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"颜色预览已启动: http://<板子IP>:{PORT}/  当前组: {CURRENT}", flush=True)
        qr = QrReader(slow=False)   #预览不做 3 倍放大兜底，否则 CPU 打满、画面停顿好几秒
        ids = None
        n = 0
        t0 = time.time()
        frames = 0
        while True:
            frame = cam.read()
            if frame is None:
                continue
            n += 1
            frames += 1
            if ids is None and n % 5 == 0:   #二维码只在没锁定时解，锁定后不再占 CPU
                code = qr.feed(frame)
                if code is not None and ids is None:
                    ids = [int(code[0]), int(code[1]), int(code[2])]
                    print(f"锁定 QR {code} | 排爆{BOMB_ID[ids[0]]}"
                          f" | 救援{TARGET_ID[ids[1]]} | 安置{ZONE_ID[ids[2]]}", flush=True)
            if ids is None and qr.last_box is not None:   #还没锁定时把二维码位置标出来
                x, y, w, h = qr.last_box
                cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 128, 0), 2)
            if CURRENT == "auto":
                if n % 12 == 0:         #每 12 帧判一次区域，其余帧沿用结果，保证画面流畅
                    resolve_auto(frame)
                draw(frame, AUTO_GROUP, ids, auto=True)
            else:
                draw(frame, CURRENT, ids)
            out = frame if (PREVIEW_W, PREVIEW_H) == (frame.shape[1], frame.shape[0]) else \
                cv2.resize(frame, (PREVIEW_W, PREVIEW_H), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
            if ok:
                LATEST.put(buf.tobytes())
            if time.time() - t0 >= 5:
                print(f"fps {frames / (time.time() - t0):.1f}", flush=True)
                t0 = time.time()
                frames = 0


if __name__ == "__main__":
    main()
