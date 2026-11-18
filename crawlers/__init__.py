"""爬虫模块：纯 httpx，不开浏览器。

2026-11-18：飞猪 flightSearch 旧接口失效、途牛 listFlight 要登录，
改用四家低价日历（calendar.py，免登录）。旧的 fliggy / tuniu 保留备用。
"""
from .base import BaseCrawler
from .fliggy import FliggyCrawler
from .tuniu import TuniuCrawler
from .calendar import FliggyCalendar, TongchengCalendar, TuniuCalendar, CtripCalendar

REGISTRY = {
    "fliggy_cal": FliggyCalendar,
    "tongcheng": TongchengCalendar,
    "tuniu_cal": TuniuCalendar,
    "ctrip_cal": CtripCalendar,
    # 旧接口（备用）：fliggy 已失效；tuniu 需登录 cookie（crawler.tuniu_cookie_file）
    "fliggy": FliggyCrawler,
    "tuniu": TuniuCrawler,
}

__all__ = ["BaseCrawler", "REGISTRY"]
