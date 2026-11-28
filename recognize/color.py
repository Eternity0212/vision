import cv2
import numpy as np
from config.config import BOMB_HSV, MIN_AREA, MAX_AREA_RATIO, DETECT_SCALE

_OPEN_KERNEL = np.ones((5, 5), np.uint8)  # 开运算核，先去掉零碎噪点，轮廓数量能少一个数量级
_OPEN_KERNEL_DARK = np.ones((9, 9), np.uint8)  # 黑色目标常和阴影/缝隙连成细桥，5×5 断不开，用 9×9


# - 把图从 BGR 转成 HSV （HSV 阈值比 RGB 抗光照，颜色识别更稳）
# - 按颜色名取出阈值区间，用`inRange` 把符合条件的像素 抠成一张黑白图 （mask，白色的就是目标色）
# - 找 mask 里的轮廓， 挑面积最大的那个 （假设目标就是最大色块）
# - 面积过滤 ：太小(<800)当反光/噪点扔掉，超过半屏(>0.5)也扔掉，防误报
# - 返回这个色块的 最小外接圆 ：圆心`(cx,cy)` + 半径
class ColorFinder:
    def __init__(self, palette=None, allow_tall=False):
        self._hsv = BOMB_HSV if palette is None else palette  # 三组阈值分开用，默认排爆组
        self._allow_tall = allow_tall  # 救援锥体细高、实心度天生低，单独放宽（球类仍走严格判据）
        self._min_area = MIN_AREA
        self._max_area_ratio = MAX_AREA_RATIO  # 从config传参数

    @property
    def colors(self):
        return list(self._hsv.keys())  # 这一组支持的颜色名

    def find(self, frame, color_name, roi=None):
        res = self.find_all(frame, color_name, roi)
        return res[0] if res else None  # 默认只要最大的那个

    def find_all(self, frame, color_name, roi=None):
        """找出该颜色的所有合格色块，按半径从大到小返回 [(cx,cy,r),...]"""
        return [(cx, cy, r) for cx, cy, r, _ in self.find_shapes(frame, color_name, roi)]

    def find_shapes(self, frame, color_name, roi=None):
        """同 find_all，但多返回一个填充率 area/r²，预览自动判区域时用来区分圆球和圆台"""
        return self.find_many(frame, [color_name], roi)[color_name]

    def find_many(self, frame, names, roi=None):
        """一次 HSV 转换同时找多个颜色，返回 {颜色名:[(cx,cy,r,填充率),...]}
        主程序每帧要扫好几种颜色，共用一次转换能省掉大量 CPU"""
        s = DETECT_SCALE
        if roi is not None:  # 只在指定区域内算，省掉整幅图的转换和掩膜
            x1, y1, x2, y2 = roi
            sub = frame[y1:y2, x1:x2]
        else:
            x1 = y1 = 0
            sub = frame
        if s != 1.0:  # 先缩小再算，坐标最后换算回原图
            sub = cv2.resize(sub, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)  # 整帧只转一次
        h, w = sub.shape[:2]
        lim = self._max_area_ratio * h * w
        min_area = self._min_area * s * s  # 面积阈值跟着缩放走，语义仍是原图的 800 像素
        out = {}
        for color_name in names:
            mask = None
            for lower, upper in self._hsv[color_name]:  # 把颜色区间提取出来，多段就合并
                m = cv2.inRange(hsv, lower, upper)
                mask = m if mask is None else cv2.bitwise_or(mask, m)
            dark = all(up[2] <= 100 for _, up in self._hsv[color_name])  # 上界 V 很低 = 深色/黑色
            mask = cv2.morphologyEx(
                mask, cv2.MORPH_OPEN, _OPEN_KERNEL_DARK if dark else _OPEN_KERNEL
            )  # 去噪点，省掉大量轮廓遍历
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            res = []
            for c in contours:  # 合格的色块全留下，同色多颗也能都返回
                area = cv2.contourArea(c)
                if area < min_area or area > lim:
                    continue
                (cx, cy), radius = cv2.minEnclosingCircle(c)
                if radius < 8 * s:  # 太小的点直接扔（按原图像素算）
                    continue
                # 只认“实心、接近圆/圆台”的块：小球是圆的，圆台是梯形，两者外接圆填充率都不低
                # 衣褶、线缆、影子这类细长或镂空的形状填充率低、凹陷多，一律不要
                if area < 1.1 * radius * radius:  # 外接圆填充率 < 0.35
                    continue
                hull = cv2.contourArea(cv2.convexHull(c))
                if hull <= 0:
                    continue
                solid = area / hull
                if solid < 0.85:  # 实心度 < 0.85（凹陷太多）
                    # 圆台/锥体：细高、被夹爪啃掉一块、底下还带反光，实心度到不了 0.85
                    # 放宽成“高度 ≥1.2 倍宽度 + 填充率 ≥0.35”，球类不满足细高条件，仍走严格判据
                    bx, by, bw, bh = cv2.boundingRect(c)
                    if not (self._allow_tall and bh >= 1.2 * bw and area >= 1.1 * radius * radius):
                        continue
                res.append(
                    (int(cx / s) + x1, int(cy / s) + y1, int(radius / s), area / (radius * radius))
                )
            res.sort(key=lambda t: t[2], reverse=True)
            out[color_name] = res
        return out


if __name__ == "__main__":
    import os
    import sys
    from config.config import CAMERA, BOMB_HSV, TARGET_HSV, ZONE_HSV
    from capture.camera import Camera

    headless = (
        sys.platform.startswith("linux") and os.environ.get("DISPLAY") is None
    )  # 判断是不是没有屏幕且接着板子
    idx = CAMERA["index_win"] if sys.platform.startswith("win") else CAMERA["index_linux"]
    groups = {"bomb": BOMB_HSV, "target": TARGET_HSV, "zone": ZONE_HSV}
    group = (
        sys.argv[1] if len(sys.argv) > 1 else "bomb"
    )  # 用法: python3 -m recognize.color target 黄
    color = sys.argv[2] if len(sys.argv) > 2 else "红"
    finder = ColorFinder(groups.get(group, BOMB_HSV))
    print(f"调色板:{group} 颜色:{color}")
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
        while True:
            frame = cam.read()  # 抓一帧存取
            if frame is None:
                continue
            result = finder.find(frame, color)  # 把这一帧和颜色名喂给find()
            if result is not None:
                cx, cy, radius = result
                cv2.circle(
                    frame, (cx, cy), radius, (0, 255, 0), 2
                )  # 以cx，cy为圆心，radius为半径画圆
                print(f"{color}:({cx},{cy}) 半径{radius}")  # 打印坐标和半径发给电控
            if headless:
                continue
            cv2.imshow("color", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    if not headless:
        cv2.destroyAllWindows()
