"""机票价格监控 主入口

用法:
    python main.py                # 按 config.yaml 配置启动定时监控
    python main.py --once         # 立即跑一次后退出
    python main.py -c other.yaml  # 指定配置文件
"""
import argparse
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from core.logger import setup_logger
from core.models import Route
from core.storage import PriceStorage
from core.alerter import Alerter
from core.scheduler import run_scheduler
from core.notifier import build_notifier
from crawlers import REGISTRY


def load_config(path: str) -> dict:
    try:
        # utf-8-sig：兼容 Windows 记事本存的「带 BOM 的 UTF-8」
        with open(path, "r", encoding="utf-8-sig") as f:
            return yaml.safe_load(f)
    except UnicodeDecodeError:
        msg = (f"配置文件 {path} 不是 UTF-8 编码。请用记事本打开，"
               "「另存为」时编码选 UTF-8 后保存，再重新启动。\n")
        # 日志还没初始化，且 Windows 后台(pythonw)没有控制台：写一个文件留痕
        os.makedirs("logs", exist_ok=True)
        with open(os.path.join("logs", "startup_error.txt"), "w", encoding="utf-8") as f:
            f.write(msg)
        print(msg, file=sys.stderr) if sys.stderr else None
        sys.exit(1)


def build_routes(cfg: dict):
    routes = []
    for r in cfg.get("routes", []):
        routes.append(Route(
            from_code=r["from"],
            from_name=r.get("from_name", r["from"]),
            to_code=r["to"],
            to_name=r.get("to_name", r["to"]),
            dates=list(r.get("dates", [])),
            alert_threshold=float(r.get("alert_threshold", 0) or 0),
        ))
    return routes


def make_job(cfg: dict, logger, storage: PriceStorage, alerter: Alerter):
    crawler_cfg = cfg.get("crawler", {})
    platforms = cfg.get("platforms", ["fliggy_cal", "tongcheng", "tuniu_cal", "ctrip_cal"])
    routes = build_routes(cfg)

    crawlers = []
    for name in platforms:
        cls = REGISTRY.get(name)
        if cls is None:
            logger.warning("未知平台: %s，已跳过", name)
            continue
        crawlers.append(cls(crawler_cfg, logger))

    login_warned = {}  # 每个平台登录过期只提醒一次，恢复后重置

    def job():
        # 只查今天及以后的日期；全部过期 → 推送一次后停用服务，等下次手动重新开启
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        active, bad = [], []
        for r in routes:
            ds = []
            for d in r.dates:
                try:
                    if date.fromisoformat(str(d)) >= today:
                        ds.append(str(d))
                except ValueError:
                    bad.append(str(d))
            if ds:
                active.append((r, ds))
        if bad:
            logger.error("配置里有无法解析的日期 %s（要写成 YYYY-MM-DD），已跳过", bad)
        if not active:
            # 只有「确实配置了日期、全部能解析、且都早于今天」才停服务；配置异常只报错不停
            all_dates = [d for r in routes for d in r.dates]
            if routes and all_dates and not bad:
                expire_and_stop(logger, alerter, routes, cfg.get("service") or {})
            else:
                logger.error("没有可查询的日期（routes/dates 为空或格式错误），本轮跳过，服务不停")
            return
        logger.info("===== 开始一轮抓取 =====")
        for route, dates in active:
            all_prices = []
            for c in crawlers:
                prices = c.safe_fetch(route.from_code, route.to_code, dates)
                storage.save_many(prices)
                all_prices.extend(prices)
            alerter.check_and_alert(route, all_prices)
        for c in crawlers:
            if getattr(c, "login_expired", False):
                c.login_expired = False
                if not login_warned.get(c.name):
                    login_warned[c.name] = True
                    logger.warning("[%s] 登录态失效，已推送提醒", c.name)
                    if alerter.notifier:
                        alerter.notifier.send(f"机票监控：{c.name} 登录过期",
                                              "途牛要求重新登录，价格暂时查不到。请重新登录途牛，更新 tuniu_cookie_file 里的 TUNIU_COOKIE。")
            else:
                login_warned[c.name] = False
        logger.info("===== 本轮抓取结束 =====")

    return job


DEFAULT_LAUNCHD_LABEL = "com.jipiao.monitor"


DEFAULT_WINDOWS_TASK = "JiPiaoMonitor"


def _disable_service(logger, service_cfg: dict):
    """停用后台服务，让它重启电脑/重新登录后也不再启动。
    macOS：launchctl disable（配合 plist KeepAlive={SuccessfulExit:false}，退出码 0 不被拉起）
    Windows：schtasks 停用计划任务
    其他系统或没装成服务：失败只记日志。
    """
    if sys.platform == "darwin":
        label = service_cfg.get("launchd_label", DEFAULT_LAUNCHD_LABEL)
        cmd = ["launchctl", "disable", f"gui/{os.getuid()}/{label}"]
    elif sys.platform == "win32":
        task = service_cfg.get("windows_task_name", DEFAULT_WINDOWS_TASK)
        cmd = ["schtasks", "/Change", "/TN", task, "/DISABLE"]
    else:
        logger.info("当前系统不自动停用服务，进程直接退出")
        return
    try:
        r = subprocess.run(cmd, check=False, timeout=15, capture_output=True)
        out = (r.stdout + r.stderr).decode("utf-8" if sys.platform != "win32" else "gbk", errors="replace").strip()
        logger.info("停用后台服务 %s 返回码 %s %s", " ".join(cmd), r.returncode, out[:200])
    except Exception as e:
        logger.warning("停用后台服务失败: %s", e)


def expire_and_stop(logger, alerter, routes, service_cfg: dict = None):
    """所有监控日期已过：推送一次通知 → 停用后台服务 → 退出码 0。重新开启见 README「到期自动停」。"""
    desc = "；".join(f"{r.from_name}→{r.to_name} {','.join(map(str, r.dates))}" for r in routes)
    logger.info("所有监控日期均已过期（%s），停止服务", desc)
    notifier = getattr(alerter, "notifier", None)
    if notifier:
        try:
            notifier.send("机票监控已到期停止", f"{desc}\n日期都已过，服务已停用。改好 config.yaml 后按 README 重新开启。")
        except Exception as e:
            logger.warning("到期通知发送失败: %s", e)
    _disable_service(logger, service_cfg or {})
    logging_shutdown()
    os._exit(0)  # 在调度线程里也能直接结束进程


def logging_shutdown():
    import logging
    logging.shutdown()


def main():
    ap = argparse.ArgumentParser(description="机票价格监控工具")
    ap.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    ap.add_argument("--once", action="store_true", help="只运行一次后退出")
    ap.add_argument("--login", metavar="PLATFORM",
                    help="登录指定平台(ctrip/fliggy/tongcheng)，弹出可见浏览器，登录完成后回车保存会话")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"配置文件不存在: {cfg_path}", file=sys.stderr)
        sys.exit(1)
    cfg = load_config(str(cfg_path))

    out = cfg.get("output", {})
    logger = setup_logger(out.get("log_path", "logs/monitor.log"))
    storage = PriceStorage(out.get("db_path", "data/prices.db"))
    notifier = build_notifier(cfg.get("notifier"), logger)
    if notifier:
        logger.info("已启用推送: %s", type(notifier).__name__)
    notify_cfg = cfg.get("notifier") or {}
    alerter = Alerter(
        logger,
        notifier=notifier,
        storage=storage,
        push_drop_min=float(notify_cfg.get("push_drop_min", 30)),
        push_rise_min=float(notify_cfg.get("push_rise_min", 50)),
    )

    # ---- 登录模式 ----
    if args.login:
        name = args.login.strip().lower()
        cls = REGISTRY.get(name)
        if cls is None:
            print(f"未知平台: {name}，可选: {list(REGISTRY)}", file=sys.stderr)
            sys.exit(2)
        crawler = cls(cfg.get("crawler", {}), logger)
        crawler.interactive_login()
        return

    job = make_job(cfg, logger, storage, alerter)

    if args.once:
        job()
        return

    sched_cfg = cfg.get("schedule", {})
    interval = int(sched_cfg.get("interval_minutes", 30))
    jitter = int(sched_cfg.get("jitter_minutes", 0))
    run_on_start = bool(sched_cfg.get("run_on_start", True))
    logger.info("启动定时调度，每 %d 分钟一次 (±%d 分钟随机扰动, 首次立即运行=%s)",
                interval, jitter, run_on_start)
    try:
        run_scheduler(job, interval, run_on_start, jitter_minutes=jitter)
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到退出信号，停止监控")


if __name__ == "__main__":
    main()
