import cv2
from config.config import HSV,MIN_AREA,MAX_AREA_RATIO
#- 把图从 BGR 转成 HSV （HSV 阈值比 RGB 抗光照，颜色识别更稳）
#- 按颜色名取出阈值区间，用`inRange` 把符合条件的像素 抠成一张黑白图 （mask，白色的就是目标色）
#- 找 mask 里的轮廓， 挑面积最大的那个 （假设目标就是最大色块）
#- 面积过滤 ：太小(<800)当反光/噪点扔掉，超过半屏(>0.5)也扔掉，防误报
#- 返回这个色块的 最小外接圆 ：圆心`(cx,cy)` + 半径
class ColorFinder():
    def __init__(self):
        self._hsv=HSV
        self._min_area=MIN_AREA
        self._max_area_ratio=MAX_AREA_RATIO #从config传参数
    def find(self,frame,color_name):
        hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV) #拿到的图转hsv

        ranges=self._hsv[color_name] #按照颜色名取区间
        mask=None
        for lower,upper in ranges: #把颜色区间提取出来，然后通过循环得到lower和upper
            m=cv2.inRange(hsv,lower,upper)
            if mask is None:
                mask=m
            else:  #如果已经有了，那就把mask和m合成一张
                mask=cv2.bitwise_or(mask,m)
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        #扫出外轮廓：把这张图上面的白色快描出轮廓
        if not contours:    #如果没有轮廓
            return None
        best=max(contours,key=cv2.contourArea)  #找出最大的轮廓 key=cv2.contourArea是按面积比大小
        area=cv2.contourArea(best)  #计算出best的面积，单位平方像素
        h,w=frame.shape[:2]
        if area <self._min_area or area>self._max_area_ratio*h*w: 
            #如果小于minarea（太小了不能用）或者超过画面一半（太大了）不能要
            return None
        (cx,cy),radius=cv2.minEnclosingCircle(best)
        return int(cx),int(cy),int(radius)
if __name__=="__main__":
    import os
    import sys
    from config.config import CAMERA
    from capture.camera import Camera
    headless=sys.platform.startswith("linux")and os.environ.get("DISPLAY" )is None#判断是不是没有屏幕且接着板子
    idx=CAMERA["index_win"]if sys.platform.startswith("win")else CAMERA["index_linux"]
    finder=ColorFinder()
    color="红"
    with Camera(index=idx,
                width=CAMERA["width"],
                height=CAMERA["height"],
                fps=CAMERA["fps"],
                warmup=CAMERA["warmup"],
                auto_exposure=CAMERA["auto_exposure"],
                exposure=CAMERA["exposure"],
                auto_wb=CAMERA["auto_wb"])as cam:
        while True:
            frame=cam.read() #抓一帧存取
            if frame is None:
                continue
            result=finder.find(frame,color)#把这一帧和颜色名喂给find()
            if result is not None:
                cx,cy,radius=result
                cv2.circle(frame,(cx,cy),radius,(0,255,0),2)#以cx，cy为圆心，radius为半径画圆
                print(f"{color}:({cx},{cy}) 半径{radius}")#打印坐标和半径发给电控
            if headless:
                continue
            cv2.imshow("color", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    if not headless:
        cv2.destroyAllWindows()

