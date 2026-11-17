import math
from config.config import CAMERA,CALIB  #一个取宽度，一个取视场角和直径（指的是物体宽度）
class Measure:
    def __init__(self):
        self.w=CAMERA["width"]
        self.f=(self.w/2.0)/math.tan(math.radians(CALIB["hfov_deg"]/2.0))#算焦距
        self.diam_cm=CALIB["target_diam_cm"]

    def angle(self,cx): #测量方位
        return math.degrees(math.atan((cx-self.w/2.0)/self.f))
    def distance(self,radius_px):   #测距离=f*直径/（2*半径）半径是像素半径
        if radius_px<=0:
            return None
        return self.f*self.diam_cm/(2.0*radius_px)

    def to_target(self,pos):    #打包上两个数据
        cx,cy,r=pos
        d=self.distance(r)
        if d is None:
            return None
        return (cx,cy,round(self.angle(cx),1),round(d,1)) #round只接受两个参数