# 菜品售卖时间 & 规格 / 附加选择

参考美团外卖的两块设置：**什么时间能卖**，以及**卖的时候有哪些可选规格**。

---

## 1. 售卖时间（哪些时段能下单）

### 配置

每个菜品可以配**多个时间段**（每天生效，按**店铺时区**）：

| 配置 | 含义 |
|---|---|
| 不配任何时间段 | **全天可售**（存量菜品零影响） |
| `08:00 – 20:00` | 当天区间，左闭右开 `[08:00, 20:00)` |
| `20:00 – 02:00` | **跨午夜**（开始 > 结束） |
| 多段，如 `07:00-10:00` + `17:00-21:00` | 早餐 + 晚餐 |

起止时间相同会被拒绝（`422`）——否则「0 长度」和「24 小时」会有歧义，要全天可售就留空。

### 三层拦截，缺一不可

1. **菜单接口**：返回 `sale_windows`、`on_sale_now`、`orderable`、`next_sale_start`；
   前端据此把按钮置灰并显示「供应时间 08:00-20:00 · 下次 06:15」。
2. **下单接口（关键）**：`POST /api/v1/orders` 会按店铺时区重新判断，
   不在时间段内直接 `409`，并告诉用户具体时间段。
   前端藏按钮只防误点，**不防伪造请求**——真正的拦截在这里。
3. **时区**：一律用 `store_settings.timezone`，不是服务器时区。服务器在别的机房也不会错。

### 接口

```http
PUT /api/v1/admin/products/{id}/sale-windows      # MANAGER
{"windows": [{"start": "08:00", "end": "20:00"}]}  # 空数组 = 全天可售
```

整体替换语义，并写入审计日志。

## 2. 规格 / 附加选择（参考美团）

### 结构

```
菜品
 └── 分组（group）
      ├── kind = SPEC   单选：中杯 / 大杯（required=true 表示必选）
      └── kind = ADDON  多选：加珍珠 / 加椰果（max_select 限制最多选几个）
           └── 选项（option）：名称（三语）+ 加价 price_delta_minor
```

- 选项可以带 `is_default`（默认选中）；
- 分组和选项都有 `active`：停用后菜单不下发，**旧链接直接下单也会被拒**；
- 一个分组里如果没有任何启用中的选项，整组不展示也不校验（否则会把下单卡死）。

### 定价：只认服务端

前端下单**只提交 `group_id` + `option_id`**：

```json
{"product_id": 1, "quantity": 2,
 "options": {"sweetness": 50,
             "selections": [{"group_id": 1, "option_id": 2},
                            {"group_id": 2, "option_id": 5}]}}
```

服务端回库把 `price_delta_minor` 取出来求和：

```
单价 = 菜品价 + Σ 选项加价
行小计 = 单价 × 数量
订单总额 = Σ 行小计
```

请求体里塞 `price_minor` / `unit_price_minor` / `line_total_minor` **一律不读**
（端到端用例里真的塞了 1 分钱，验证总额仍然是服务端算出来的值）。

### 校验（不通过返回 422）

| 情况 | 结果 |
|---|---|
| 必选分组没选 | `Please choose 规格` |
| 单选分组选了 2 个 | `Only one choice allowed for 规格` |
| 多选超过 `max_select` | `Choose at most 2 from 附加` |
| 分组不属于该菜品 / 已停用 | `This option group is not available for this product` |
| 选项不存在 / 已停用 | `Option is not available in group 附加` |
| 同一选项重复提交 | `Duplicate option selection` |

### 订单里存的是快照

`order_items.options_json`：

```json
{"sweetness": 50, "price_delta_minor": 300,
 "selections": [{"group_id": 1, "group_name": {"en": "Size"}, "group_kind": "SPEC",
                 "option_id": 2, "option_name": {"en": "Large"}, "price_delta_minor": 200}]}
```

存**当时的名称与加价**，所以之后改价、改名、删分组都不影响历史订单。
员工群的订单消息会渲染成 `• 2 × Milk Tea · 50% · Large +2.00 USD + Pearl +1.00 USD`——
后厨照着做，不会漏掉规格。

### 接口

```http
PUT /api/v1/admin/products/{id}/option-groups     # MANAGER，整体替换
{"groups": [{"name": {"en": "Size", "zh-CN": "规格"}, "kind": "SPEC", "required": true,
             "options": [{"name": {"en": "Large"}, "price_delta_minor": 200}]}]}
```

整体替换而不是逐条增删改：管理端是「编辑完整表单再保存」，一次 PUT 语义最清楚，
也不会出现「组删了选项还留着」的中间态。历史订单有快照，所以重建分组是安全的。

## 3. 管理端 / 客户端

- **管理端**：菜品列表每行有「售卖时间 / 规格」按钮，展开后可编辑时间段与分组选项，
  分别保存。
- **客户端**：菜品卡内联渲染规格选择器（单选=圆点、多选=复选框、加价显示在名称后），
  必选没选时点加购会提示；不在售卖时间时按钮置灰并显示供应时段。
  购物车里按「菜品 + 甜度 + 规格组合」分行——同菜不同规格必须是两行。

## 4. 验证

```bash
cd backend
python tests/product_options_test.py           # 39 项纯逻辑（时间边界/跨午夜/校验/加价）
python tests/menu_options_e2e.py http://127.0.0.1:8000   # 27 项接口全链路（含价格伪造）
```

端到端用例覆盖：窗口外下单 `409`、必选缺失 `422`、伪造分组/选项 `422`、
单选选两个 `422`、**伪造价格被忽略**、订单快照、员工群文案渲染、非 MANAGER `403`。
