from typing import Any, Dict

# 配置总表：集中定义接口地址、鉴权参数、分页规则与处理逻辑。
# 通过环境变量注入敏感信息，避免硬编码 token/cookie。
CONFIG: Dict[str, Any] = {
    # 列表接口：用于拉取列车/班列基础数据
    "list_api": {
        # 接口地址与请求基础参数
        "url": "https://bgwlgl.bbwport.com/api/train-sea-union/real/train/listRealTrainInfo.do",
        "method": "POST",
        "timeout": 30,
        "retries": 5,
        "retry_backoff_base": 1.5,
        "sleep_between_pages": 0.2,
        # 请求头：AUTH_TOKEN 与 COOKIE 将由环境变量注入
        "headers": {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://bgwlgl.bbwport.com",
            "referer": "https://bgwlgl.bbwport.com/",
            "user-agent": "Mozilla/5.0",
            "auth_token": "${AUTH_TOKEN}",
            "cookie": "${COOKIE}",
        },
        # 列表查询参数模板（分页参数 + 业务过滤条件）
        "payload_template": {
            "pageNumber": 0,
            "pageSize": 200,
            "params": {
                "realTrainCode": "",
                "startStation": "",
                "endStation": "",
                "endProvince": "",
                "lineCode": "",
                "lineName": "",
                "upOrDown": "上行",
                "departureDateStart": "2026-01-13 00:00:00",
                "loadingTimeStart": "",
                "loadingTimeEnd": "",
            },
            "sorts": [],
        },
        # 分页解析规则：用于计算总页数与遍历页码
        "pagination": {
            "page_param": "pageNumber",
            "page_size_param": "pageSize",
            "page_size": 200,
            "start_page": 0,
            "one_based": False,
            "max_pages": 10000,
            "rows_field": "rows",
            "total_field": "total",
            "total_pages_field": "totalPage",
        },
    },
    # 导出接口：用于下载装箱数据 Excel
    "export_api": {
        # 导出接口地址与重试策略
        "url": "https://bgwlgl.bbwport.com/api/train-sea-union/bookingInfo/exportLoadedBox.do",
        "method": "POST",
        "timeout": 60,
        "retries": 3,
        # 接口业务标识
        "flag": "单表",
        # 与列表接口一致的鉴权头
        "headers": {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://bgwlgl.bbwport.com",
            "referer": "https://bgwlgl.bbwport.com/",
            "user-agent": "Mozilla/5.0",
            "auth_token": "${AUTH_TOKEN}",
            "cookie": "${COOKIE}",
        },
    },
    # 运行期参数：控制目标日期、输出目录与样本保存等
    "run": {
        "target_day": "",
        "output_dir": "data",
        "output_filename_template": "export_loaded_box_{day}.xlsx",
    },
    # 处理配置：用于拆分 Excel 并按实际订舱客户输出
    "processing": {
        "enabled": True,
        # 过滤与拆分依据字段
        "consigner_field": "委托客户",
        "consigner_env_key": "CONSIGNOR_NAME",
        "actual_booker_field": "实际订舱客户",
        "actual_booker_exclude": "陆海新通道",
        "output_dir": "data/actual_booker",
        "sheet_name": "data",
        "output_template": "{actual_booker}.xlsx",
    },
    # 自动登录相关配置（验证码 + 登录接口）
    "login_api": {
        "enabled": True,
        # 验证码接口配置
        "captcha": {
            "enabled": True,
            "value_env_key": "CAPTCHA_VALUE",
            "key_env_key": "CAPTCHA_KEY",
            "rs_id_env_key": "LOGIN_RS_ID",
            "url": "https://bgwlgl.bbwport.com/api/bgwl-cloud-center/random",
            "method": "GET",
            "timeout": 10,
            "headers": {
                "accept": "application/json, text/plain, */*",
                "origin": "https://bgwlgl.bbwport.com",
                "referer": "https://bgwlgl.bbwport.com/",
                "user-agent": "Mozilla/5.0",
            },
            "params": {"show": "${CAPTCHA_SHOW}"},
            "save_path": "data/captcha/latest.png",
            "retries": 3,
            "retry_sleep": 1,
            "response_type": "base64_json",
            "image_field": "randomCodeImage",
            "key_field": "captchaKey",
            "rs_id_field": "_rs_id",
        },
        # 登录接口参数与模板
        "login": {
            "url": "https://bgwlgl.bbwport.com/api/bgwl-cloud-center/login.do",
            "method": "POST",
            "timeout": 15,
            "password_hash": "md5",
            "headers": {
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json;charset=UTF-8",
                "user-agent": "Mozilla/5.0",
            },
            "params_template": {},
            "rs_id_param": "_rs_id",
            "random_code_param": "_randomCode_",
            "payload_template": {
                "username": "${LOGIN_USERNAME}",
                "password": "${LOGIN_PASSWORD}",
            },
            "captcha_field": "captcha",
            "captcha_key_field": "captchaKey",
        },
        # token 写入路径与环境变量映射
        "token_json_path": ["data", "token"],
        "token_env": "AUTH_TOKEN",
        "cookie_env": "COOKIE",
    },
}
