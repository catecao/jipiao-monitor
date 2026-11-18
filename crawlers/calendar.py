"""低价日历数据源（2026-11-18 加）：免登录、免签名、纯 httpx。

接口来源：dengmeiluan/ticket-monitoring 的 crawlers/special.py，经 zemojix-0305/ticket-radar 转述；
携程日历来自 jerryzhou945/FlightPriceRecord。2026-11 用国内航线逐个实测可用。

口径：统一取「票面价/裸价」（不含机建燃油），和各家 App 列表页显示的价格一致。
局限：日历只给「当天最低价」，飞猪/途牛/携程不带航班号；同程 PC 接口带航班号和起飞时间。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date as _date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx

from core.models import FlightPrice
from .base import BaseCrawler

M_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
CN_TZ = timezone(timedelta(hours=8))


def _num(v) -> float:
    """价格字段可能是 "--"/None/字符串，转不了就当 0（随后被过滤）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _put_min(out: dict, d: str, row: dict):
    """同一天多条记录时保留最低价，而不是后一条覆盖前一条。"""
    if d not in out or row["price"] < out[d]["price"]:
        out[d] = row


def _add_days(d: str, n: int) -> str:
    return (_date.fromisoformat(d) + timedelta(days=n)).isoformat()


class CalendarCrawler(BaseCrawler):
    """子类实现 _query(from, to, dates) -> {date: dict(price=, flight_no=, ...)}"""
    name = "calendar"
    attempts = 2

    def fetch(self, from_city: str, to_city: str, dates: List[str]) -> List[FlightPrice]:
        fc, tc = from_city.upper(), to_city.upper()
        dates = sorted(str(d) for d in dates)
        got: Dict[str, dict] = {}
        for i in range(self.attempts):
            try:
                got = self._query(fc, tc, dates) or {}
                if got:
                    break
                self.logger.warning("[%s] 第 %d 次没拿到数据", self.name, i + 1)
            except Exception as e:
                self.logger.warning("[%s] 第 %d 次请求失败: %s", self.name, i + 1, e)
            if i + 1 < self.attempts:
                time.sleep(3)
        out = []
        for d in dates:
            row = got.get(d)
            if not row or _num(row.get("price")) <= 0:
                self.logger.warning("[%s] %s 无价格", self.name, d)
                continue
            fp = FlightPrice(
                platform=self.name, from_city=fc, to_city=tc, depart_date=d,
                price=_num(row["price"]),
                airline=row.get("airline", ""), flight_no=row.get("flight_no", ""),
                depart_time=row.get("depart_time", ""), arrive_time=row.get("arrive_time", ""),
                extra=json.dumps({k: v for k, v in row.items() if k in ("tax", "total", "url")},
                                 ensure_ascii=False) if any(k in row for k in ("tax", "total", "url")) else "",
            )
            self.logger.info("[%s] %s 最低价 ¥%.0f %s", self.name, d, fp.price,
                             f"({fp.flight_no} {fp.depart_time})" if fp.flight_no else "")
            out.append(fp)
        return out

    def _query(self, fc: str, tc: str, dates: List[str]) -> Dict[str, dict]:
        raise NotImplementedError


class FliggyCalendar(CalendarCrawler):
    """飞猪低价日历（JSONP）。一次给 7 天，按周翻页；price 是裸价，tax 另给。
    短时间连发会被 IP 级限流（全回 success:false 几分钟），所以页数有上限。"""
    name = "fliggy_cal"
    MAX_PAGES = 4

    def _query(self, fc, tc, dates):
        want, seen = set(dates), {}
        cursor, pages = dates[0], 0
        with httpx.Client(headers={"User-Agent": PC_UA,
                                   "Referer": "https://sijipiao.fliggy.com/ie/flight_search_result.htm"},
                          timeout=20) as c:
            while cursor <= dates[-1] and pages < self.MAX_PAGES:
                pages += 1
                r = c.get("https://r.fliggy.com/cheapestCalendar/pc", params={
                    "bizType": 1, "depCityCode": fc, "arrCityCode": tc, "leaveDate": cursor,
                    "calendarType": 0, "tripType": 0, "callback": "cb"})
                m = re.match(r"\s*cb\s*\(([\s\S]*)\)\s*;?\s*$", r.text)
                j = json.loads(m.group(1)) if m else {}
                for x in j.get("result") or []:
                    d = str(x.get("leaveDate", ""))[:10]
                    price = _num(x.get("price"))
                    if d in want and price > 0:
                        tax = _num(x.get("tax"))
                        url = str(x.get("url") or "")
                        _put_min(seen, d, {"price": price, "tax": tax, "total": price + tax,
                                           "url": ("https:" + re.sub(r"^https?:", "", url)) if url else ""})
                cursor = _add_days(cursor, 7)
                if cursor <= dates[-1]:
                    time.sleep(1.0)
        return seen


class TongchengCalendar(CalendarCrawler):
    """同程：PC 价格日历（带航班号/起飞时间）为主，小程序日历兜底。"""
    name = "tongcheng"

    def _query(self, fc, tc, dates):
        want = set(dates)
        try:
            r = httpx.post("https://www.ly.com/flights/api/getpricecalendar", json={
                "StartPort": fc, "EndPort": tc, "QueryBegDate": dates[0], "QueryEndDate": dates[-1],
                "QueryType": 1, "travelTypes": [1], "flat": 1, "plat": 1, "isFromKylin": 1, "refid": ""},
                headers={"User-Agent": PC_UA, "Origin": "https://www.ly.com",
                         "Referer": f"https://www.ly.com/flights/itinerary/oneway/{fc}-{tc}"}, timeout=20)
            j = r.json()
            if str(j.get("resCode")) == "0":
                out = {}
                for x in (j.get("body") or {}).get("fzpriceinfos") or []:
                    d = str(x.get("flydate", ""))[:10]
                    price = _num(x.get("price"))
                    if d in want and price > 0:
                        _put_min(out, d, {"price": price, "flight_no": x.get("flightno") or "",
                                  "depart_time": str(x.get("flytime") or "")[11:16],
                                  "arrive_time": str(x.get("arrtime") or "")[11:16]})
                if out:
                    return out
            self.logger.info("[tongcheng] PC 日历无数据，改用小程序日历")
        except Exception as e:
            self.logger.info("[tongcheng] PC 日历失败(%s)，改用小程序日历", e)

        r = httpx.post(
            "https://wx.17u.cn/cheapflights/bffapi/cheapflightapi/lowestprice/queryCalendarLowestPrice",
            json={"dateInfo": {"dateType": 0, "start": dates[0], "end": dates[-1]},
                  "originList": [{"code": fc, "name": fc, "type": 0, "nameType": "0"}],
                  "destList": [{"code": tc, "name": tc, "type": 0, "nameType": "0"}], "channel": 4},
            headers={"User-Agent": M_UA, "Referer": "https://wx.17u.cn/"}, timeout=20)
        j = r.json()
        out = {}
        if j.get("success"):
            m = (j.get("data") or {}).get("flightCalendarPriceMap") or {}
            rows = m.get(f"{fc}-{tc}") or (next(iter(m.values())) if m else [])
            for x in rows or []:
                d = str(x.get("date", ""))[:10]
                if d in want and _num(x.get("price")) > 0:
                    _put_min(out, d, {"price": _num(x.get("price"))})
        return out


class TuniuCalendar(CalendarCrawler):
    """途牛低价日历：只有国内，裸价。免登录（listFlight 要登录，这个不用）。"""
    name = "tuniu_cal"

    def _query(self, fc, tc, dates):
        want = set(dates)
        d = {"orgCityIataCode": fc, "dstCityIataCode": tc, "departureDate": dates[0],
             "backDate": "", "type": "1", "serverMonth": 12}
        r = httpx.get("https://flight-api.tuniu.com/query/flight/v1/calendar",
                      params={"d": json.dumps(d)},
                      headers={"User-Agent": M_UA, "Accept": "application/json",
                               "Referer": "https://m.tuniu.com/"}, timeout=20)
        j = r.json()
        if not j.get("success"):
            raise RuntimeError(str(j.get("msg") or j.get("errorMsg") or j.get("errorCode")))
        out = {}
        for x in j.get("data") or []:
            d = str(x.get("date", ""))[:10]
            if d in want and _num(x.get("price")) > 0:
                _put_min(out, d, {"price": _num(x.get("price"))})
        return out


class CtripCalendar(CalendarCrawler):
    """携程低价日历。price=裸价；totalPrice 口径不明（比裸价高 190 左右），不用。
    实测偶有某天报价明显高于其他几家（日历可能有缓存），所以只作为多家之一取最低。"""
    name = "ctrip_cal"

    def _query(self, fc, tc, dates):
        want = set(dates)
        body = {"head": {"cid": "", "ctok": "", "cver": "1.0", "lang": "01", "sid": "8888",
                         "syscode": "999", "auth": None, "extension": []},
                "departNewCityCode": fc, "arriveNewCityCode": tc, "startDate": dates[0],
                "grade": 15, "flag": 0, "channelName": "FlightIntlOnline", "searchType": 1,
                "passengerList": [{"passengercount": 1, "passengertype": "Adult"}],
                "calendarSelections": [{"selectionType": 8, "selectionContent": ["15"]}]}
        r = httpx.post("https://m.ctrip.com/restapi/soa2/15380/bjjson/FlightIntlAndInlandLowestPriceSearch",
                       json=body, headers={"User-Agent": M_UA}, timeout=20)
        out = {}
        for x in r.json().get("priceList") or []:
            m = re.search(r"/Date\((\d+)", str(x.get("departDate", "")))
            if not m:
                continue
            d = datetime.fromtimestamp(int(m.group(1)) / 1000, CN_TZ).strftime("%Y-%m-%d")
            if d in want and _num(x.get("price")) > 0:
                _put_min(out, d, {"price": _num(x.get("price"))})
        return out
