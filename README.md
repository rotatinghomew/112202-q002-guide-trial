# 展厅导览试印记录

`data/` 为离线合成业务记录。文件均为 UTF-8 的 Tab 分隔表，首行是列名，逗号分隔集合，`-` 表示不适用，`?` 表示未取得，不把它们当作零。标识符只在各表声明的归属范围内有效。

## 运行与资料核对

使用 Python 3.11。分析环境另附固定版本的数值与统计依赖，可在本地虚拟环境执行 `python3 -m pip install -r requirements.txt`。

```sh
python3 inspect_packet.py
python3 -m unittest discover -s tests
python3 -m exhibit_trial.report
```

前两条命令只检查表头、记录形状和口径测试。第三条生成 `report.md`（结论）与
`report.json`（摘要）。分析与续印核算的口径说明见 `exhibit_trial/README.md`。

## 结论摘要

- **按实际发印安排（原随机分组）**：成功率 A 66.0%、B 68.8%；按场次对分层的
  比例差 +1.6%，按户聚类 95% 区间 [-19.0%, +22.6%]，随机化检验 p=0.75。
  不能证明新版更好，也不能排除超过经营损失上限的退步。
- **缺测与串版**：24/138 条为未知；9 条 B 臂观测实际接触了旧版且全部成功。
  只保留纯版观测时，效应由 +1.6% 翻为 -10.1%，说明主口径的正号不可靠。
- **纯粹换一版（实际接触、可比条件）**：-16.4%，区间 [-38.2%, +5.5%]；
  只能是参考，不是随机效果。
- **保护层**：少儿与不熟悉场馆的已知结果都只有个位数，不能用全厅区间替代。
- **续印**：不据现有证据扩大新版。只兑现已有通知：F1–F3 发 A，F4 拆成 6 户 B
  + 6 户 A；不需要新签整批，已签的 B/zh 40 册（8,400 分）记为沉没/闲置，
  不能从成本里抹去。F4 混用在最不利情形越过不熟悉层上限，须升级签字。

## 输入表

- `assignment.tsv`：每一对为同厅两场的预定印本对照；时间窗以参加机会起、单位分钟。 列顺序为 `pair`、`hall`、`left`、`right`、`left_edition`、`right_edition`、`horizon`。
- `sessions.tsv`：start 为演练日内分钟。open_minutes 是实开时间；cancelled 无实际机会。志愿者 ID 相同表示同一人。 列顺序为 `session`、`start`、`open_minutes`、`volunteer`、`status`。
- `visits.tsv`：opportunity 和 minute 为相对场次开始的分钟；result 可为独立找到、求助后找到、未找到、缺交班结果。not-found 仅为记录员登记的观测；窗口不足时不据此判定最终失败。 列顺序为 `visit`、`household`、`session`、`profile`、`actual`、`opportunity`、`result`、`minute`。
- `printing.tsv`：比例差用小数；group 是需保护的访客分层；contamination_max 是允许的异版接触比例上限。 列顺序为 `hall`、`min_gain`、`group`、`max_loss`、`contamination_max`。
- `next_sessions.tsv`：可排场次；fixed 为空则可选版，否则已承诺版本；不同版同厅相邻之间至少 flush 分钟。 列顺序为 `option`、`hall`、`day`、`start`、`length`、`volunteer`、`fixed`、`flush`。
- `invitations.tsv`：prereg_profile 是分配前标签集合；邀请未实到时 entered=no；offered 记录告知版而非实际接触。 列为 `invitation`、`household`、`session`、`prereg_profile`、`language`、`entered`、`offered`。
- `loans.tsv`：登记一次真实接触或借阅；晚到批次可以补录同户第二版接触。 列为 `receipt`、`household`、`session`、`desk`、`version`、`minute`、`received_batch`。
- `edition_inventory.tsv`：pack_size 为追加印单起印整批单位，费用单位分。 列为 `edition`、`language`、`ready_day`、`stock`、`pack_size`、`lock_day`、`unit_cost`。
- `dispatch.tsv`：expected 为访客家庭数；profiles 是可能参与的保护层，联合分层计数见需求表；routes 是线路键，可共享服务台。 列为 `option`、`languages`、`profiles`、`expected`、`desk`、`routes`、`old_on_desk`。
- `dispatch_profiles.tsv`：标签集合为互斥组合，行可加；all 表示没有额外保护标签。 列为 `option`、`language`、`profiles`、`households`。
- `print_orders.tsv`：approved 是已签印量；notified 表示场次已有对外发放承诺。 列为 `order`、`option`、`edition`、`language`、`qty`、`approved_day`、`state`。
- `guardrails.tsv`：层级分别生效；比例用小数；budget 为整份追加采购成本上限，分。 列为 `hall`、`layer`、`min_gain`、`max_loss`、`mixed_ceiling`、`budget`。
