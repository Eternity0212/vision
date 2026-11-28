import sys
import serial  # 从config拿串口配置
import time
from config.config import SERIAL


class Tx:  # 定义一个发送类
    def __init__(self):
        # 传参数：波特率，串口号，存超时时间，时间间隔
        self.baud = SERIAL["baud"]
        self.port = SERIAL["port_win"] if sys.platform.startswith("win") else SERIAL["port_linux"]
        self.timeout = SERIAL["timeout"]
        self.interval = 1.0 / SERIAL["send_hz"]
        self.ser = None  # 串口对象
        self._last = 0.0  # 上次发送时刻
        self._rx_buf = b""  # 接收缓冲：攒够一整行再解析

    def open(self):  # 定义打开串口的方法
        try:
            self.ser = serial.Serial(
                self.port, self.baud, timeout=self.timeout
            )  # 拿三样：哪个口，波特率，超时
        except Exception:
            self.ser = None  # 没接电控时，com3不存在，try让代码在没接板子的时候也能跑
            print(f"串口{self.port}打开失败 (没接电控时正常现象)")
        return self

    def send(self, msg):  # 定义发送方法
        if self.ser is None:
            return False
        now = time.time()
        if now - self._last < self.interval:  # 规定了最少隔0.1s才能再寄
            return False
        self._last = now  # 把这次发送的时间保存下来
        try:
            self.ser.write(
                (msg + "\n").encode("ascii")
            )  # 发送的每条msg以换行结尾然后都要转换成ascii码，最后用write推送
            return True
        except Exception:
            self.ser = None
            return False

    def send_now(self, msg):  # 事件报文：不节流，立即发（K / Q / O / G 用这个）
        if self.ser is None:
            return False
        self._last = time.time()
        try:
            self.ser.write((msg + "\n").encode("ascii"))
            return True
        except Exception:
            self.ser = None
            return False

    def read_cmd(self):  # 读电控下发的命令，没有就返回None
        if self.ser is None:
            return None
        try:
            n = self.ser.in_waiting  # 缓冲区里现成有多少字节
            if n > 0:
                self._rx_buf += self.ser.read(n)  # 先攒起来，防止半条命令
        except Exception:
            self.ser = None
            return None
        cmd = None
        while b"\n" in self._rx_buf:  # 按行切开
            line, self._rx_buf = self._rx_buf.split(b"\n", 1)
            line = line.strip()
            if line:
                cmd = line.decode("ascii", "ignore")  # 只留最后一条完整的
        return cmd

    def close(self):
        if self.ser is not None:
            self.ser.close()
            self.ser = None  # 主动释放串口，如果不释放，别的地方拿不到


if __name__ == "__main__":

    class FakeSer:  # 假串口：只为测试，不碰真硬件
        def __init__(self):
            self.n = 0  # 数一共写了几次

        def write(self, data):
            self.n += 1

        def close(self):
            pass

    tx = Tx()
    tx.ser = FakeSer()  # 跳过 open()，直接塞假串口
    for i in range(30):  # 模拟摄像头 30 帧
        tx.send(f"T,1,{i},30")
        time.sleep(1.0 / 30)  # 每帧间隔 1/30 秒，让真实时间流逝
    print(f"30 帧 1 秒，实际发出 {tx.ser.n} 条")
