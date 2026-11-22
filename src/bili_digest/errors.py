"""共享异常与日志。内容 stdout / 进度与告警 stderr，与 bili-subtitle 约定一致。"""

import sys


class CliError(Exception):
    """带退出码的业务错误：0 成功 / 2 用法 / 3 未登录或无字幕且 ASR 失败 / 4 网络风控 / 1 其它。"""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def log(msg):
    sys.stderr.write(f"digest: {msg}\n")
