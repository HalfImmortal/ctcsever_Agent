FROM python:3.8

# 将当前项目目录下的所有文件添加到容器的 /app 目录
ADD . /app

# 切换工作目录到 /app
WORKDIR /app

#安装需要的库
RUN pip install -r requirements.txt

# 安装 OpenGL 相关库
RUN apt-get update && \
    apt-get install -y libgl1-mesa-glx

# 声明容器将要监听的端口
EXPOSE 8181

# 将当前目录的内容复制到容器的 /app 目录中
COPY . .

CMD ["python","./服务demo.py"]