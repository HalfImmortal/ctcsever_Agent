# -*- coding: utf-8 -*-
import base64
import os
import io
from PIL import Image
import tempfile
from elasticsearch_dsl import connections, Text, Search, Q
import json
from fastapi import FastAPI, Request,File, UploadFile,Form,HTTPException
# from starlette.responses import FileResponse
# web服务器
import uvicorn
from pydantic import BaseModel, Field
from typing import List,Optional
import requests
import time
import logging

from demo import ocr_process

import uuid


class Attachment(BaseModel):
    id: str
    type: str

class BotChat(BaseModel):
    role: str
    content: str
    attachments: List[Attachment] = []

class ChatRequest(BaseModel):
    openid: str
    chat_id: int
    message: List[BotChat]

class ChatMessage(BaseModel):
    """通原课堂返回响应的数据格式"""
    chat_id: int
    content: str
    attachments: List[Attachment] = []

class Token(BaseModel):
    """access_token接口返回的数据结构"""
    access_token: str
    expire: int  # 多少秒后过期

token_pool={
    "ori_token":{
        # 普通的token和到期时间
        "access_token":None,
        "expires_at": 0.0
    },
    "model_token": {
        # 大模型的token（其实就是测试服务器的token）和到期时间
        "access_token": None,
        "expires_at": 0.0
    }
}

def restore_text_and_images(text_and_images):
    restored_content = text_and_images["paragraph_contents"]
    for docs in restored_content:
        for doc in docs["whole"]:
            if doc["type"] == 'text':
                pass
            elif doc["type"] == 'image' or doc["type"] == 'img':
                # 通原题库图片字段叫image ，学生老师问答图片字段叫img
                img_base64 = doc["content"]
                img_data = base64.b64decode(img_base64)
                doc["content"] = img_data
    return restored_content

def query_to_ctcTopic(qs,bot_id):
    img_attachments = [] #存储md中所有图片的attachments

    # ---------查询操作--------------
    # 1.Search
    # 创建一个查询对象
    search = Search(index="ctc2.3")

    # 添加一个match查询
    query = Q("match", pure_text=qs)  # 选择了下面的Q的查询方法，同理可以写mathc_phrase等
    s = search.query(query)
    # 执行查询
    result = s.execute()

    # 接收es的json信息用于显示
    receive_content = {
        "paragraph_contents": []
    }
    # 遍历查询结果
    for hit in result.hits:
        para_dict = {"pure_text": hit.pure_text,
                     "whole": json.loads(hit.whole)}  # json转换成list
        receive_content["paragraph_contents"].append(para_dict)
    restored_content = restore_text_and_images(receive_content)

    """创建一个新的md文件并将内容写入其中"""
    count=0
    num=2 # 限制返回的题目数量
    # 创建一个字符串来存储Markdown格式的文本
    markdown_content = rf"根据您的提问，为您从通信原理题库中匹配到{num}道相关题目，如下："+'\n'+'\n'

    for docs in restored_content:
        if count<num:
            #每道题前面加一个标号
            markdown_content += f'{count+1}.'
            for doc in docs["whole"]:
                if doc["type"] == 'text':
                    markdown_content += doc["content"]
                elif doc["type"] == 'latex':
                    markdown_content += doc["content"]
                elif doc["type"] == 'image':
                    # 图像单独放一行
                    markdown_content+='\n'
                    markdown_content+='\n'

                    # 图片数据：1.上传码上侧的图片数据端口，获得图片的id 2.将图片id放入md字符串，eg：ezopen://{文件id}
                    # 将图片转换为二进制流
                    image_stream = io.BytesIO(doc["content"])

                    image = Image.open(image_stream)

                    # 保存图像对象到临时文件
                    temp_img_file = tempfile.NamedTemporaryFile(delete=False)
                    image.save(temp_img_file, format='PNG', quality=95)  # 保存为PNG格式

                    #1.上传数据库，得到图片id
                    attachment_id = upload_file(bot_id=bot_id,file_path=temp_img_file.name)

                    #2.将id写入md文件
                    markdown_content+=f" ezopen://{attachment_id} "

                     # 图像单独放一行
                    markdown_content+='\n'
                    markdown_content+='\n'


                    #3.将图片id和类型保存到img_attachments中
                    img_attachments.append(Attachment(id=attachment_id,type="img"))

                    # 关闭文件
                    temp_img_file.close()

                    # 删除临时文件
                    os.unlink(temp_img_file.name)
            #每个题目结束换行(再隔一行)
            markdown_content+='\n'
            markdown_content+='\n'
            count+=1 # 题目计数
    return markdown_content,img_attachments


def query_to_ctcDiscussion(qs,bot_id):
    img_attachments = [] #存储md中所有图片的attachments

    # ---------查询操作--------------
    # 1.Search
    # 创建一个查询对象
    search = Search(index="ctc_discussion1.4")

    # 添加一个match查询
    query = Q("match", question_pure_text=qs)  # 选择了下面的Q的查询方法，同理可以写mathc_phrase等
    s = search.query(query)
    # 执行查询
    result = s.execute()

    # 接收es的json信息用于显示
    receive_content = {
        "paragraph_contents": []
    }
    # 遍历查询结果
    for hit in result.hits:
        para_dict = {"whole": json.loads(hit.question_whole),
                     "answer":json.loads(hit.answer)}  # json转换成list
        receive_content["paragraph_contents"].append(para_dict)
    restored_content = restore_text_and_images(receive_content)

    """创建一个新的md文件并将内容写入其中"""
    count=0
    num=1 # 限制返回的对话数量
    # 创建一个字符串来存储Markdown格式的文本
    markdown_content = rf"根据您的提问，为您从通信原理课堂问答数据库中匹配到{num}条相关问答记录，如下："+'\n'+'\n'

    for docs in restored_content:
        if count<num:
            #每道题前面加一个标号
            markdown_content += f'{count+1}.'
            #学生的问题描述
            markdown_content += "学生提问："
            for doc in docs["whole"]:
                if doc["type"] == 'text':
                    markdown_content += doc["content"]
                elif doc["type"] == 'image' or doc["type"] == 'img':
                    # 通原题库图片字段叫image ，学生老师问答图片字段叫img
                    # 图像单独放一行
                    markdown_content+='\n'
                    markdown_content+='\n'

                    # 图片数据：1.上传码上侧的图片数据端口，获得图片的id 2.将图片id放入md字符串，eg：ezopen://{文件id}
                    # 将图片转换为二进制流
                    image_stream = io.BytesIO(doc["content"])

                    image = Image.open(image_stream)

                    # 保存图像对象到临时文件
                    temp_img_file = tempfile.NamedTemporaryFile(delete=False)
                    image.save(temp_img_file, format='PNG', quality=95)  # 保存为PNG格式

                    #1.上传数据库，得到图片id
                    attachment_id = upload_file(bot_id=bot_id,file_path=temp_img_file.name)

                    #2.将id写入md文件
                    markdown_content+=f" ezopen://{attachment_id} "

                     # 图像单独放一行
                    markdown_content+='\n'
                    markdown_content+='\n'

                    #3.将图片id和类型保存到img_attachments中
                    img_attachments.append(Attachment(id=attachment_id,type="img"))

                    # 关闭文件
                    temp_img_file.close()

                    # 删除临时文件
                    os.unlink(temp_img_file.name)

            #学生提问内容显示后，换行
            markdown_content += '\n'
            markdown_content += '\n'

            #老师的回答
            markdown_content += "教师回答："
            for doc in docs["answer"]:
                if doc["type"] == 'text':
                    markdown_content += doc["content"]
                elif doc["type"] == 'image':
                    # 图像单独放一行
                    markdown_content+='\n'
                    markdown_content+='\n'

                    # 图片数据：1.上传码上侧的图片数据端口，获得图片的id 2.将图片id放入md字符串，eg：ezopen://{文件id}
                    # 将图片转换为二进制流
                    image_stream = io.BytesIO(doc["content"])

                    image = Image.open(image_stream)

                    # 保存图像对象到临时文件
                    temp_img_file = tempfile.NamedTemporaryFile(delete=False)
                    image.save(temp_img_file, format='PNG', quality=95)  # 保存为PNG格式

                    #1.上传数据库，得到图片id
                    attachment_id = upload_file(bot_id=bot_id,file_path=temp_img_file.name)

                    #2.将id写入md文件
                    markdown_content+=f" ezopen://{attachment_id} "

                     # 图像单独放一行
                    markdown_content+='\n'
                    markdown_content+='\n'

                    #3.将图片id和类型保存到img_attachments中
                    img_attachments.append(Attachment(id=attachment_id,type="img"))

                    # 关闭文件
                    temp_img_file.close()

                    # 删除临时文件
                    os.unlink(temp_img_file.name)

            #每个题目结束换行(再隔一行)
            markdown_content+='\n'
            markdown_content+='\n'
            count+=1 # 题目计数
    return markdown_content,img_attachments

def get_XH_accessToken(appId,appSecret):
    """获取鉴权的令牌"""
    url=f"{XH_url}/bot/v1/authorization/access-token"
    data = {
        "app_id": appId,
        "secret": appSecret
    }

    """获取一个有效的access_token，如果当前 token 已过期则更新它"""
    global token_pool
    current_time = time.time()
    if token_pool["model_token"]["access_token"] is None or current_time > token_pool["model_token"]["expires_at"]:
        # token 已过期则更新它,并返回
        try:
            # 发送 POST 请求
            response = requests.post(url, json=data, verify=False)  # 注意：verify=False 会跳过证书验证，但在生产环境中不推荐使用
            # 检查响应状态码
            if response.status_code == 200:
                # 如果请求成功，返回响应的 JSON 数据,并存入令牌池
                access_token = response.json()
                token_pool["model_token"]["access_token"] = access_token
                token_pool["model_token"]["expires_at"] = current_time+7000 #实际是7200过期，我这样为了安全
                return token_pool["model_token"]["access_token"]
            else:
                # 如果请求失败，打印错误信息并返回 None
                print(f"Request failed with status code {response.status_code}")
                return None
        except requests.RequestException as e:
            # 捕获请求异常，打印错误信息并返回 None
            print(f"Request failed: {e}")
            return None
    else:
        return token_pool["model_token"]["access_token"]

def get_accessToken(appId,appSecret):
    """获取鉴权的令牌"""
    url=f"{base_url}/bot/v1/authorization/access-token"
    data = {
        "app_id": appId,
        "secret": appSecret
    }

    """获取一个有效的access_token，如果当前 token 已过期则更新它"""
    global token_pool
    current_time = time.time()
    if token_pool["ori_token"]["access_token"] is None or current_time > token_pool["ori_token"]["expires_at"]:
        # token 已过期则更新它,并返回
        try:
            # 发送 POST 请求
            response = requests.post(url, json=data, verify=False)  # 注意：verify=False 会跳过证书验证，但在生产环境中不推荐使用
            # 检查响应状态码
            if response.status_code == 200:
                # 如果请求成功，返回响应的 JSON 数据,并存入令牌池
                access_token = response.json()
                token_pool["ori_token"]["access_token"] = access_token
                token_pool["ori_token"]["expires_at"] = current_time+7000 #实际是7200过期，我这样为了安全
                return token_pool["ori_token"]["access_token"]
            else:
                # 如果请求失败，打印错误信息并返回 None
                print(f"Request failed with status code {response.status_code}")
                return None
        except requests.RequestException as e:
            # 捕获请求异常，打印错误信息并返回 None
            print(f"Request failed: {e}")
            return None
    else:
        #直接放回现成的可用的token
        return token_pool["ori_token"]["access_token"]

def download_file(bot_id,attachment_id):
    """从码上侧获得attachment_id对应的文件（图片等），临时保存在temp_pics里面"""
    url = f"{base_url}/bot/v1/{bot_id}/attachment/{attachment_id}"

    # 获取鉴权的令牌
    access_token = get_accessToken(appId=appId,appSecret=appSecret)["access_token"]  # 获取响应字典中的access_token
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        # 发送 GET 请求，并设置 stream=True 以逐块下载响应内容
        response = requests.get(url, headers=headers, stream=True, verify=False)  # 注意：verify=False 会跳过证书验证，但在生产环境中不推荐使用
        # 检查响应状态码
        if response.status_code == 200:
            # 获取文件名
            content_disposition = response.headers.get('content-disposition')
            filename = content_disposition.split('filename=')[1].strip('"')
            temp_name = "./temp_pics/"+ str(uuid.uuid4()) + filename

            # 逐块写入文件
            with open(temp_name, 'wb') as file:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        file.write(chunk)
            print(f"Attachment downloaded successfully: {temp_name}")
            return temp_name
        else:
            # 如果请求失败，打印错误信息
            print(f"Failed to download attachment with status code {response.status_code}")
    except requests.RequestException as e:
        # 捕获请求异常，打印错误信息
        print(f"Request failed: {e}")

def upload_file(bot_id, file_path):
    url = f"{base_url}/bot/v1/{bot_id}/attachment"
    # 获取鉴权的令牌
    access_token = get_accessToken(appId=appId,appSecret=appSecret)["access_token"]  # 获取响应字典中的access_token
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        # 打开要上传的文件
        with open(file_path, 'rb') as file:
            files = {'file': ('image.png', file, 'image/png')}  # 设置文件名和  MIME类型(告诉接收端这是图片-png格式)
            # 发送包含文件的 POST 请求
            response = requests.post(url, headers=headers, files=files, verify=False)  # 注意：verify=False 会跳过证书验证，但在生产环境中不推荐使用

        # 检查响应状态码
        if response.status_code in {200, 201, 409}:
            #HTTP 状态码 409 表示冲突（Conflict），根据返回的响应内容，服务器检测到上传的文件是重复的
            print("Attachment uploaded successfully")

            # 解析响应获取上传图片的id
            # 解析 JSON 响应数据
            response_json = response.json()
            # 获取返回的数据结构字段
            attachment_id = response_json['id']
            is_duplicate = response_json['is_duplicated'] # 若此字段为true则代表平台之前已上传过此附件，附件id会复用之前此文件的id
            checksum = response_json['checksum']
            return attachment_id
        else:
            # 如果请求失败，打印错误信息
            print(f"Failed to upload attachment with status code {response.status_code}")
    except requests.RequestException as e:
        # 捕获请求异常，打印错误信息
        print(f"Request failed: {e}")
    except IOError as e:
        # 捕获文件读取异常，打印错误信息
        print(f"Failed to read file: {e}")

def XH_model(bot_id,qs):
    url = f"{XH_url}/bot/v1/{bot_id}/flux"
    # 获取鉴权的令牌
    access_token = get_XH_accessToken(appId=appId,appSecret=appSecret)["access_token"]  # 获取响应字典中的access_token
    headers = {"Authorization": f"Bearer {access_token}"}
    #指定请求的大模型类型，SPARK-3.5, GPT-3, GPT-4; 可不传，默认SPARK-3.5。
    params = {
        "modelType": "GPT-3"  # 可选参数
    }
    # 请求体内容
    body = {
        "content": "在通信领域中，" + qs
    }
    try:
        # 发送POST请求，设置stream=True以处理流式响应
        response = requests.post(url, headers=headers, params=params,json=body, stream=True, verify=False)

        # 检查响应状态码
        if response.status_code == 200:
            # 创建一个空字符串用于拼接响应内容
            result = rf"根据您的提问，我为您解释如下(此内容由大模型生成，仅供参考)："+'\n'+'\n'

            # 迭代响应内容块
            for chunk in response.iter_content(chunk_size=8192):
                # 解码块内容
                decoded_chunk = chunk.decode('utf-8')
                # 去除块内容中的 "data:" 前缀
                if decoded_chunk.startswith("data:"):
                    decoded_chunk = decoded_chunk[len("data:"):].strip()

                # 拼接内容
                result += str(decoded_chunk)

            return result
        else:
            # 返回错误信息
            return None, response.status_code, response.text
    except requests.exceptions.RequestException as e:
        # 处理请求异常
        return None, None, str(e)

"""连接elasticsearch"""
connection = connections.create_connection(alias="default",hosts=["http://10.3.244.173:9201"])

# 实例化总的路由对象
app = FastAPI()
XH_url = "https://101.42.12.233:10443/api"
base_url = "https://ezcoding.bupt.edu.cn:443/api"

# ctc的：
appId = "e143d737-d5d9-4805-89e8-dd93f2f58b79"  #appId也是botId
appSecret = "PrXKohjx7ZTLzyixOjqFpbEN5UofAoaI"


@app.get('/')
async def root():
    return {"message":"hello"}

@app.post("/dispatch/invoke")
async def communication_theory_Query(request: Request):
    # 读取请求体中的 JSON 数据
    try:
        request_data = await request.json()  # 获取 JSON 数据并转化为字典
        chat_request = ChatRequest(**request_data)  # 使用解包操作将字典传递给 ChatRequest 构造函数
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 获取当前轮的对话消息，也就是message列表的最后一个字典的内容
    last_message = chat_request.message[-1]

    question="" # 用户的提问
    empty_attachments = [Attachment(id="no_attachment", type="img")] #响应的attachments列表，主要是md文件图片的id值,这个是拿来占位的

    if len(last_message.attachments)==0:
        # attachment为空的情况，即用户没有过上传图片，只有文本的提问
        question=last_message.content
    else:
        # 有上传的图片
        # 当前逻辑是，content也有内容的话将content和所有图片字句拼成一个question
        question+=last_message.content # 拼上用户的文字提问

        # 从码上侧获取用户上传的所有图片并进行ocr处理，拼接到question上
        for attachment in last_message.attachments:
            temp_name = download_file(bot_id=appId,attachment_id=attachment.id) # 获取对应图片的位置和名字，eg:./temp_pics/1.png
            img_qs = ocr_process(temp_name) # ocr解析图片中文字
            question+=img_qs # 图片的问题拼接到提问中

            # 删除这个临时图片
            os.remove(temp_name)
    print(question)
    # # 根据用户提问获取星火大模型的回答
    XH_answer = XH_model(appId,qs=question)

    # 根据用户提问获取es中的匹配通原题目
    topic_answer,topic_img_attachments = query_to_ctcTopic(qs=question,bot_id=appId)

    # 根据用户提问获取es中的匹配问答记录
    discussion_answer,discussion_img_attachments = query_to_ctcDiscussion(qs=question,bot_id=appId)

    # 拼接所有的回答
    final_answer = XH_answer + "\n" + "\n" + topic_answer + "\n" + "\n" + discussion_answer

    # 合并通原题目和问题记录数据库中的img_attachments的列表
    img_attachments = topic_img_attachments + discussion_img_attachments

    # 构造响应数据
    response_data = ChatMessage(
        chat_id=chat_request.chat_id,  # 替换为实际的对话 ID
        content=final_answer,
        attachments= img_attachments if len(img_attachments) != 0 else empty_attachments
    )
    return response_data

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=9432)  # 第一个参数要写这个python脚本的名字，reload就是是否自动刷新

