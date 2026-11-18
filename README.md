# jipiao-monitor · 机票降价监控

盯国内航线的机票价格，跌破你设的价格就推送到手机（Telegram）、Mac 桌面或微信（Server酱）。

- **四家一起比**：飞猪、同程、途牛、携程的低价日历，每轮取最低价
- **免登录、不开浏览器**：纯 HTTP 请求，依赖只有 3 个 Python 包
- **不刷屏**：同一价格只推一次，再降 ¥30 或涨 ¥50 才再推
- **到期自动停**：监控的日期全部过去后，推送一条通知并停掉后台服务

> 本项目改自 [yangka1212/JiPiao](https://github.com/yangka1212/JiPiao)（MIT）。低价日历接口参考了
> [dengmeiluan/ticket-monitoring](https://github.com/dengmeiluan/ticket-monitoring)、
> [zemojix-0305/ticket-radar](https://github.com/zemojix-0305/ticket-radar)、
> [jerryzhou945/FlightPriceRecord](https://github.com/jerryzhou945/FlightPriceRecord)。

## 推送长什么样

（示例）

```
✈️ 上海→成都 2026-12-20 ¥480
- 当前最低价：¥480
- 设定阈值：¥500
- 来源：同程
- 航班：MU5401 08:00
👉 在 同程 查看详情（链接）
```

## 1. 安装

需要 Python 3.9+。

```bash
git clone https://github.com/catecao/jipiao-monitor.git
cd jipiao-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. 配置

```bash
cp config.example.yaml config.yaml
```

打开 `config.yaml`，至少改两处：

**航线**

```yaml
routes:
  - from: SHA          # 出发城市三字码
    from_name: 上海
    to: CTU            # 到达城市三字码
    to_name: 成都
    dates:
      - "2026-12-20"   # 必须写成 YYYY-MM-DD
    alert_threshold: 500
```

- 用**城市码**，不要用机场码：上海写 `SHA`（虹桥 + 浦东都算），写 `PVG` 会查不到
- 常用城市码：北京 `BJS`、上海 `SHA`、广州 `CAN`、深圳 `SZX`、成都 `CTU`、杭州 `HGH`、重庆 `CKG`、西安 `SIA`、昆明 `KMG`、厦门 `XMN`、三亚 `SYX`
- 价格是**票面价**（不含机建燃油），和 App 列表页显示的一致
- 只支持国内航线

**推送**（选一个或几个打开）

- **Telegram**：找 [@BotFather](https://t.me/BotFather) 建一个 bot 拿到 token，给 bot 发一句话后用
  `https://api.telegram.org/bot<token>/getUpdates` 查到自己的 chat id。然后建凭证文件：

  ```bash
  mkdir -p ~/.config/jipiao
  printf 'TG_BOT_TOKEN=你的token\nTG_CHAT_ID=你的chatid\n' > ~/.config/jipiao/telegram.env
  chmod 600 ~/.config/jipiao/telegram.env
  ```

  再把 `notifier.telegram.enabled` 改成 `true`。凭证不写进 config.yaml。
- **Mac 桌面通知**：`notifier.local.enabled: true`，默认已开
- **微信**：在 [Server酱](https://sct.ftqq.com/) 拿 SendKey，填到 `notifier.serverchan`

## 3. 运行

先跑一次试试，能看到四家的价格就说明通了：

```bash
.venv/bin/python main.py --once
```

日志里会有这样的对比行（示例）：

```
[对比] 上海->成都 2026-12-20 最低=tongcheng ¥480  (同程: ¥480 | 飞猪: ¥490 | 途牛: ¥490 | 携程: ¥495)
```

**前台常驻**（关掉终端就停）：

```bash
.venv/bin/python main.py
```

**Mac 后台常驻**（登录自动启动，推荐）：

```bash
./deploy/install_launchd.sh
```

装好后常用命令（`com.jipiao.monitor` 是默认服务名）：

```bash
launchctl kickstart -k gui/$(id -u)/com.jipiao.monitor   # 改完配置后重启、立刻查一轮
tail -f logs/launchd.err.log                             # 看日志
launchctl bootout gui/$(id -u)/com.jipiao.monitor        # 停掉并卸载
```

> 改了 `config.yaml` 一定要重启服务才生效。Mac 睡眠时不会查价，醒来后接着查。

## 4. 到期自动停

每轮只查今天及以后的日期。所有日期都过去后，程序会推一条「机票监控已到期停止」，
然后执行 `launchctl disable` 并退出，重启电脑也不会再拉起。

要监控新的行程：改好 `config.yaml` 里的日期，再执行一次 `./deploy/install_launchd.sh` 即可。

配置写错（日期格式不对、dates 为空）时只记错误日志，不会误停服务。

## 5. 常见问题

- **某一家显示「无价格」**：飞猪日历短时间请求多了会被限流几分钟，下一轮通常就恢复，不影响其他三家。
  调试时别连续反复跑 `--once`。
- **携程某天价格明显偏高**：携程日历偶尔有缓存，和别家对不上；因为每轮取四家最低，不影响提醒。
- **日历只有最低价**：飞猪、途牛、携程不带航班号，收到提醒后去 App 里看具体是哪一班。
- **国内连不上 Telegram**：在 `notifier.telegram.proxy` 填本地代理地址。
- **平台接口失效**：这些都是网页公开接口，平台随时可能改版。哪家长期拿不到数据，就先从 `platforms` 里注释掉。

## 6. 项目结构

```
├── main.py                  # 入口：调度、到期自动停
├── config.example.yaml      # 示例配置（复制为 config.yaml 使用，config.yaml 不进仓库）
├── deploy/install_launchd.sh  # macOS 后台服务安装脚本
├── core/
│   ├── alerter.py           # 多平台比价、阈值提醒、去重
│   ├── notifier.py          # Telegram / Mac 桌面 / Server酱
│   ├── storage.py           # SQLite：价格历史 + 推送状态
│   ├── scheduler.py         # APScheduler 定时 + 随机扰动
│   └── models.py / logger.py
└── crawlers/
    ├── calendar.py          # 现用：飞猪/同程/途牛/携程低价日历
    ├── fliggy.py            # 旧：飞猪搜索接口（已失效，保留备查）
    └── tuniu.py             # 旧：途牛列表接口（需登录 cookie，备用）
```

价格历史在 `data/prices.db`（SQLite，表 `flight_prices`），想画走势图可以直接查。

## 7. 免责声明

仅供个人学习和自用，请控制查询频率，遵守各平台的服务条款，不要用于商业用途或高频抓取。
价格以各平台实际下单页为准。

## License

MIT，见 [LICENSE](LICENSE)。
