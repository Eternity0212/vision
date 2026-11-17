import os   #用来处理路径和文件系统 本模块负责存数据

BASE_DIR=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))#指向根目录
#总共四层 从里往外读 abspath可以保证file始终能得到完整路径
LOG_DIR=os.path.join(BASE_DIR,"logs")   #日志记录当时发生什么
FAIL_DIR=os.path.join(BASE_DIR,"fails") #指向失败目录，调试时认错的帧会存入，可以在这个文件夹里回溯实际画面
CAMERA={
    "index_win":0,
    "index_linux":10,   #USB 摄像头枚举在 /dev/video10（video0~9 是 MIPI/ISP 节点）
    "width":1280,
    "height":720,
    "fps":30,
    "warmup":5 , #"打开摄像头先扔掉前五帧，因为前五帧刚打开会过曝"
    "auto_exposure":0.25,  #V4L2 菜单：0.25手动，想自动曝光0.75
    "exposure":330, #直接对应 exposure_time_absolute（单位 100µs）：330≈33ms，30fps 上限
    "auto_wb":0  #关闭自动白平衡，以免无法固定hsv阈值
}
BOMB_ID={1:"蓝",2:"绿",3:"红"}  #爆炸物
TARGET_ID={1:"红",2:"黄",3:"绿",4:"浅蓝",5:"深蓝",6:"黑"}   #待识别目标
ZONE_ID={1:"红",2:"蓝",3:"黑"}  #投放区

#HSV阈值存放
#存放结构为字典--列表--元组--数字
HSV={
"红": [((0, 120, 120), (10, 255, 255)), ((170, 120, 120), (179, 255, 255))],
"黄": [((20, 100, 120), (35, 255, 255))],
"绿": [((40, 80, 80), (85, 255, 255))],
"浅蓝": [((85, 40, 150), (105, 255, 255))],
"深蓝": [((100, 140, 40), (125, 255, 190))],
"黑": [((0, 0, 0), (179, 255, 60))]
}

#面积过滤
MIN_AREA=800 #过滤掉小于800的色块，防止反光，衣服上红点这些变成假目标
MAX_AREA_RATIO=0.5  #超过画面一半的色块不算目标

#二维码
QR={"min_side_px":30,"confirm_frames":3}   
#边长小于30像素不认，太小的无法解码，浪费cpu（方案）
#连续三帧解出同样内容再确认，防止误码

#串口
SERIAL={
    "port_win":"COM3",  #笔记本串口名
    "port_linux":"/dev/ttyS3",   #泰山派串口设备，上板前必须确认！！！！
    "baud":115200,  #需要和电控完全一致！！！
    "send_hz":10,   #每秒发10次，带宽有限，太快会积压
    "timeout":0.05  #超时0.05s直接跳过，程序继续跑
}

#标定（摄像头实测：1280x720，HFOV 84.3°，VFOV 54.0°，DFOV 92.2°，35.5 px/cm）
CALIB={
    "hfov_deg":84.3,        #水平视场角，实测
    "vfov_deg":54.0,        #垂直视场角，实测
    "dfov_deg":92.2,        #对角视场角，实测，用来验算镜头一致性
    "px_per_cm":35.5,       #实测像素密度，对应约 20cm 距离；距离一变这个值就失效
    "target_diam_cm": 5.0,   #目标真实直径，占位，量到再改
    "camera_height_mm":None,    #摄像头离地高度，装好支架后量
    "pitch_deg":None    #镜头下俯角，装好支架后测
}

#--------------- 方案甲 v5.0 ---------------

#p3 停车判定
STOP={
    "center_deg":5.0,   #中间球偏离画面中心小于这个角度算居中
    "lock_frames":3     #连续几帧算稳定
}

#p3 位置编号：画面里最左边那颗球是几号位（1 或 3），现场核对后改
POS_LEFT_IS=1

#位置表：编号 -> 舵机角(度) / 伸出长度(cm)，全部现场标定
POS_TABLE={
    1:{"angle":None,"reach":None},  #最靠车尾
    2:{"angle":None,"reach":None},  #正对车侧
    3:{"angle":None,"reach":None}   #最靠车头
}

#p4 转盘（三个目标颜色随机，不写死颜色：扫 HSV 里全部颜色，去重后取三个）
P4={
    "b_tol_px":None,    #B 坑允许偏离画面中心的像素，None 时用画面宽 1/10
    "off_tol_cm":2.0,   #车相对盘心偏差小于这个就不发 O
    "max_step_cm":10,   #单次微调最大厘米
    "o_lock_s":1.5,     #发 O 后多久内不重复量
    "max_adjust":2,     #最多微调几次
    "g_lock_s":2.0,     #发 G 后多久不再发
    "g_stable_frames":3,  #目标 cx 连续稳定多少帧才发 G
    "g_stable_px":5       #稳定判定：相邻帧 cx 变化不超过多少像素
}

#连续丢几帧算真丢（30fps 下约 0.33 秒）
HOLD_FRAMES=10
