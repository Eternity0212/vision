import sys
from config.config import CAMERA    #从cofig文件夹里的cofig.py中把CAMERA拿出来
from capture.camera import Camera

idx = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]
#A if 条件 else B
print("要求:", CAMERA["width"], CAMERA["height"], CAMERA["fps"])
with Camera(index=idx, width=CAMERA["width"], height=CAMERA["height"],
            fps=CAMERA["fps"], auto_exposure=CAMERA["auto_exposure"],
            exposure=CAMERA["exposure"], auto_wb=CAMERA["auto_wb"],
            warmup=CAMERA["warmup"]) as cam:
    print("实际:", cam.info())
    for _ in range(60):
        cam.read()
    print("实测FPS:", round(cam.fps, 1))
