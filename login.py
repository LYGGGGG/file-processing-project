def test():
    # -*- coding: utf-8 -*-
    import hashlib
    import json
    import time
    from typing import Callable
    import ddddocr, base64, re
    import requests

    # 保存验证ma
    def save_api_data():
        url = "https://bgwlgl.bbwport.com/api/bgwl-cloud-center/random?show=1753427607760"
        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            rs_id = data["_rs_id"]
            # 保存图片
            image_data = data["randomCodeImage"]
            match = re.match(r"data:image/(\w+);base64,(.*)", image_data)

            if match:
                img_format = match.group(1)
                img_data = match.group(2)
                # 直接解码base64数据，避免写入临时文件
                image_bytes = base64.b64decode(img_data)

                # 直接使用解码后的数据进行OCR识别
                ocr = ddddocr.DdddOcr()
                text = ocr.classification(image_bytes)
                # print(text)
            # 保存rs_id
            # with open("rs_id.txt", "w") as f:
            #     f.write(data["_rs_id"])

            return rs_id, text
        except Exception as e:
            print(f"发生错误: {e}")

    """工厂函数生成指定输出格式的MD5计算函数"""

    def create_output_method(method: str) -> Callable[[str], str]:
        def hash_func(input_str: str) -> str:
            md5 = hashlib.md5(input_str.encode('utf-8'))
            if method == 'hexdigest':
                return md5.hexdigest()
            elif method == 'digest':
                return md5.digest().decode('latin1')
            raise ValueError(f"Unsupported method: {method}")

        return hash_func

    # 获取cookie
    def get_cookie():
        temp = save_api_data()
        rs_id = temp[0]
        code = temp[1]
        # print(rs_id,code)
        md5_hex = create_output_method('hexdigest')
        dataList = {
            "rsid": rs_id,
            "code": code,
            "password": md5_hex("${LOGIN_PASSWORD}"),
            "username": "${LOGIN_USERNAME}",
        }

        dataList = json.dumps(dataList)
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        }
        session = requests.Session()
        # 1. 首先执行登录 POST 请求
        url = f"https://bgwlgl.bbwport.com/api/bgwl-cloud-center/login.do?_rs_id={rs_id}&_randomCode_={code}"
        login_response = session.post(url, headers=headers, data=dataList)
        # print(login_response.json()['data'])

        return login_response, session

    def verify_cookies():
        temp = get_cookie()

        # 从登录响应中获取所有 cookies
        cookies_list = temp[1].cookies.items()
        # print("cookies_list:")
        # print(cookies_list)

        # 获取 AUTH_TOKEN
        auth_token = temp[0].json()['data']
        # print("AUTH_TOKEN:")
        # print(auth_token)
        # 确保从正确的 session 中获取 BGWL-EXEC-PROD
        bgwl_exec_prod = temp[1].cookies.get('BGWL-EXEC-PROD')
        cookies = {
            'IGNORE-SESSION': '-',
            'AUTH_TOKEN': auth_token,
            'BGWL-EXEC-PROD': bgwl_exec_prod,
            'HWWAFSESTIME': cookies_list[1][1],
            'HWWAFSESID': cookies_list[0][1],
        }
        print('最终的cookie:', cookies)




        return cookies

    try:
        return verify_cookies()
    except Exception as e:
        print(f"GWL-EXEC-PROD验证失败: {e}")