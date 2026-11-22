# 展厅导览试印记录

`data/` 为离线合成业务记录。文件均为 UTF-8 的 Tab 分隔表，首行是列名，逗号分隔集合，`-` 表示不适用，`?` 表示未取得，不把它们当作零。标识符只在各表声明的归属范围内有效。

## 运行与资料核对

使用 Python 3.11。分析环境另附固定版本的数值与统计依赖，可在本地虚拟环境执行 `python3 -m pip install -r requirements.txt`。

```sh
python3 inspect_packet.py
python3 -m unittest discover -s tests
```

前一条命令只检查表头和记录形状并显示行数。可传入另一目录读取同格式记录。当前没有分析、决策或业务处理结果。

## 输入表

- `assignment.tsv`：每一对为同厅两场的预定印本对照；时间窗以参加机会起、单位分钟。 列顺序为 `pair`、`hall`、`left`、`right`、`left_edition`、`right_edition`、`horizon`。
- `sessions.tsv`：start 为演练日内分钟。open_minutes 是实开时间；cancelled 无实际机会。志愿者 ID 相同表示同一人。 列顺序为 `session`、`start`、`open_minutes`、`volunteer`、`status`。
- `visits.tsv`：opportunity 和 minute 为相对场次开始的分钟；result 可为独立找到、求助后找到、未找到、缺交班结果。not-found 仅为记录员登记的观测；窗口不足时不据此判定最终失败。 列顺序为 `visit`、`household`、`session`、`profile`、`actual`、`opportunity`、`result`、`minute`。
- `printing.tsv`：比例差用小数；group 是需保护的访客分层；contamination_max 是允许的异版接触比例上限。 列顺序为 `hall`、`min_gain`、`group`、`max_loss`、`contamination_max`。
- `next_sessions.tsv`：可排场次；fixed 为空则可选版，否则已承诺版本；不同版同厅相邻之间至少 flush 分钟。 列顺序为 `option`、`hall`、`day`、`start`、`length`、`volunteer`、`fixed`、`flush`。
