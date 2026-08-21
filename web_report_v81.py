"""v81 兼容入口。

v84 已将网页报告重构为中文研究终端。保留这个模块名是为了让
scan_service.py、daily_pipeline.py、旧测试和外部脚本无需迁移即可继续工作。
"""

from web_report_v84 import *  # noqa: F403
from web_report_v84 import (
    _archive_html,
    _published_source_dir,
)
