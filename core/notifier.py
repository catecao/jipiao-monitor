"""消息推送

- ServerChanNotifier：Server 酱（sct.ftqq.com），会把内容发到第三方服务器
- MacNotifier：macOS 本地通知（osascript），完全不出网
"""
import json
import logging
import pathlib
import re
import subprocess
import sys
import urllib.parse
import urllib.request

import httpx
from typing import Optional


class ServerChanNotifier:
    """Server 酱·Turbo 版：https://sct.ftqq.com/

    免费额度：每日 5 条；超出付费。
    """

    ENDPOINT_TPL = "https://sctapi.ftqq.com/{key}.send"

    def __init__(self, send_key: str, logger: logging.Logger,
                 channel: Optional[str] = None):
        self.send_key = send_key.strip()
        self.logger = logger
        self.channel = channel  # 可指定推送通道，不填默认

    def send(self, title: str, desp: str = "") -> bool:
        if not self.send_key:
            self.logger.debug("Server酱 SendKey 未配置，跳过推送")
            return False
        url = self.ENDPOINT_TPL.format(key=self.send_key)
        payload = {
            # 限60字符
            "title": title[:60],
            # 支持 markdown
            "desp": desp[:32000],
        }
        if self.channel:
            payload["channel"] = self.channel

        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", "ignore")
                obj = json.loads(body)
                code = obj.get("code", -1)
                if code == 0:
                    self.logger.info("Server酱 推送成功: %s", title)
                    return True
                self.logger.warning("Server酱 推送失败 code=%s body=%s",
                                    code, body[:200])
                return False
        except Exception as e:
            self.logger.warning("Server酱 推送异常: %s", e)
            return False


class MacNotifier:
    """macOS 本地通知：走系统自带 osascript，内容不出本机。

    通知只有标题/副标题/正文三段，不支持 markdown，也无法点击跳转，
    因此完整详情（含购票链接）仍然写进日志，通知里只放关键信息。
    """

    def __init__(self, logger: logging.Logger, sound: str = "Glass"):
        self.logger = logger
        self.sound = (sound or "").strip()

    @staticmethod
    def _plain(desp: str) -> str:
        """把 alerter 生成的 markdown 详情压成一行纯文本要点。"""
        keep = []
        for line in (desp or "").splitlines():
            line = line.strip()
            m = re.match(r"^-\s+\*\*(当前最低价|设定阈值|来源)\*\*：(.+)$", line)
            if m:
                val = m.group(2).replace("**", "").strip()
                keep.append(f"{m.group(1)} {val}")
        return " ｜ ".join(keep)

    def send(self, title: str, desp: str = "") -> bool:
        message = self._plain(desp) or title
        # json.dumps 负责转义引号/反斜杠，避免 AppleScript 语法被内容打断
        script = (
            f"display notification {json.dumps(message, ensure_ascii=False)} "
            f"with title {json.dumps('✈️ 机票低价提醒', ensure_ascii=False)} "
            f"subtitle {json.dumps(title, ensure_ascii=False)}"
        )
        if self.sound:
            script += f" sound name {json.dumps(self.sound, ensure_ascii=False)}"
        try:
            # 不经 shell，参数以列表传入
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                self.logger.info("本地通知已发送: %s", title)
                return True
            self.logger.warning("本地通知失败 rc=%s err=%s",
                                r.returncode, (r.stderr or "").strip()[:200])
            return False
        except Exception as e:
            self.logger.warning("本地通知异常: %s", e)
            return False



def _read_env_file(path: str) -> dict:
    """读取 KEY=VALUE 格式的 env 文件（支持引号与 # 注释）。

    用于让密钥只存放在一处（如 ~/.config/jipiao/telegram.env，权限 600），
    而不必抄进项目的 config.yaml。
    """
    out = {}
    try:
        fp = pathlib.Path(path).expanduser()
        # utf-8-sig：兼容 Windows 记事本存的「带 BOM 的 UTF-8」
        for line in fp.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


class TelegramNotifier:
    """Telegram Bot 推送。

    需要两个参数：
      bot_token —— 找 @BotFather 建 bot 后拿到，形如 123456:ABC-DEF...
      chat_id   —— 你自己的用户 ID（找 @userinfobot 查）或群组 ID

    国内直连 api.telegram.org 通常不通，可用 proxy 配置项走本地代理。
    发纯文本（不设 parse_mode），避免 MarkdownV2 转义把消息打回。
    """

    API_TPL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, bot_token: str, chat_id: str, logger: logging.Logger,
                 proxy: Optional[str] = None, timeout: int = 20):
        self.bot_token = (bot_token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.logger = logger
        self.proxy = (proxy or "").strip() or None
        self.timeout = timeout

    @staticmethod
    def _plain(title: str, desp: str) -> str:
        """把 alerter 的 markdown 详情压成 Telegram 纯文本（链接保留裸 URL 可点）。"""
        lines = [title, ""]
        for line in (desp or "").splitlines():
            s = line.strip()
            if not s or s.startswith("## "):
                continue
            m = re.match(r"^-\s+\*\*(.+?)\*\*：(.+)$", s)
            if m:
                lines.append(f"{m.group(1)}：{m.group(2).replace('**', '').strip()}")
                continue
            m = re.match(r"^\[.*?\]\((.+?)\)$", s)
            if m:
                lines.append("")
                lines.append(m.group(1))
        return "\n".join(lines)

    def send(self, title: str, desp: str = "") -> bool:
        if not self.bot_token or not self.chat_id:
            self.logger.debug("Telegram 未配置完整（bot_token/chat_id），跳过推送")
            return False
        payload = {
            "chat_id": self.chat_id,
            "text": self._plain(title, desp)[:4000],
            "disable_web_page_preview": True,
        }
        try:
            kwargs = {"timeout": self.timeout}
            if self.proxy:
                kwargs["proxy"] = self.proxy
            with httpx.Client(**kwargs) as client:
                r = client.post(self.API_TPL.format(token=self.bot_token), json=payload)
            if r.status_code == 200 and (r.json() or {}).get("ok"):
                self.logger.info("Telegram 推送成功: %s", title)
                return True
            self.logger.warning("Telegram 推送失败 status=%s body=%s",
                                r.status_code, r.text[:200])
            return False
        except Exception as e:
            self.logger.warning("Telegram 推送异常: %s", e)
            return False


class MultiNotifier:
    """把消息同时发给多个通道；任一通道成功即视为成功。"""

    def __init__(self, notifiers: list, logger: logging.Logger):
        self.notifiers = notifiers
        self.logger = logger

    def send(self, title: str, desp: str = "") -> bool:
        results = []
        for n in self.notifiers:
            try:
                results.append(bool(n.send(title, desp)))
            except Exception as e:
                self.logger.warning("%s 推送异常: %s", type(n).__name__, e)
                results.append(False)
        return any(results)


def build_notifier(cfg: dict, logger: logging.Logger):
    """按配置构造推送器；可同时启用多个通道，都没启用则返回 None"""
    if not cfg:
        return None

    channels = []

    tg = (cfg.get("telegram") or {})
    if tg.get("enabled"):
        token, chat = tg.get("bot_token", ""), tg.get("chat_id", "")
        # 未在 config 里直接写死时，从外部 env 文件读（密钥不落进项目配置）
        env_file = tg.get("env_file")
        if env_file and not (token and chat):
            env = _read_env_file(env_file)
            token = token or env.get(tg.get("token_key", "TG_BOT_TOKEN"), "")
            chat = chat or env.get(tg.get("chat_key", "TG_CHAT_ID"), "")
            if not (token and chat):
                logger.warning("Telegram: 未能从 %s 读到凭证，该通道跳过", env_file)
        if token and chat:
            channels.append(TelegramNotifier(
                bot_token=token, chat_id=chat,
                logger=logger, proxy=tg.get("proxy"),
            ))

    local = (cfg.get("local") or {})
    if local.get("enabled"):
        if sys.platform == "darwin":
            channels.append(MacNotifier(logger=logger, sound=local.get("sound", "Glass")))
        else:
            logger.warning("桌面通知(local)只支持 macOS，当前系统已跳过；请用 Telegram 或 Server酱(微信)")

    sc = (cfg.get("serverchan") or {})
    if sc.get("enabled") and sc.get("send_key"):
        channels.append(ServerChanNotifier(
            send_key=sc["send_key"], logger=logger, channel=sc.get("channel"),
        ))

    if not channels:
        return None
    if len(channels) == 1:
        return channels[0]
    return MultiNotifier(channels, logger)
