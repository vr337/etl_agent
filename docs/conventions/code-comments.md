# etl_agent 代码注释规范

> | 项 | 值 |
> |----|-----|
> | 版本 | v1.0 |
> | 日期 | 2026-10-14 |
> | 适用 | S2 及后续所有阶段的 Python 产出 |
> | 依据 | SPEC v1.0.3-Baseline 附录 B「可维护」行；PRD 10.5 凭据零明文 |

---

## 一、三层注释（必加）

### 第 1 层：文件头（每个 `.py` 必加）

```python
"""M-A 认证鉴权 — trusted-gateway HMAC 验签。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 6.4 章错误码
覆盖 FR：FR-65（认证鉴权）
对应 AC：AC-11（认证边界）
"""
```

**约束**：
- 必须含：模块归属（M-x）+ 功能一句话 + SPEC 章节 + 覆盖 FR + 对应 AC
- SPEC 版本号必须与当前基线一致（当前 `v1.0.3-Baseline`）
- 无 SPEC/FR/AC 追溯视为不合格

### 第 2 层：公共函数/类 docstring（跨模块调用的必加）

```python
async def verify_and_consume_nonce(nonce: str, ttl: int = 120) -> None:
    """Nonce 一次性消费。

    SPEC 8「安全 · 认证」：Redis SET NX，跨实例一次性；存储不可用失败关闭。

    Args:
        nonce: 16–128 位安全字符
        ttl: 秒，默认 120

    Raises:
        AuthBadSignature:      Nonce 已消费（重放）
        NonceStoreUnavailable: Redis 不可用（失败关闭返回 503）
    """
```

**约束**：
- 只给**跨模块调用**的函数/类写
- 必须含 Args / Returns（如有）/ Raises（如有）
- **禁止复述代码行为**

### 第 3 层：关键决策点单行注释（只写"为什么"）

```python
# 失败关闭：Redis 不可用时返回 503，不降级放行（AC-11 / SPEC 8「安全 · 认证」）
if not await redis.ping():
    raise NonceStoreUnavailable()

# ADMIN 不隐式升级：即使 actor 是 ADMIN，无 SECURITY_APPROVER 授予 → 403
# （FR-65 职责分离红线 / 2.3 强制职责分离规则）
if "SECURITY_APPROVER" not in actor.harness_roles:
    raise RoleRequired("SECURITY_APPROVER")
```

**约束**：
- 只加在**安全/业务红线**决策点（P0-DENY、职责分离、失败关闭、防重放等）
- 必须注明依据（FR/AC/SPEC 章节）
- 只写"为什么"，不写"做了什么"

---

## 二、明确不加

| 场景 | 加？ | 原因 |
|------|------|------|
| 平凡 getter / setter | ❌ | 代码自解释 |
| 简单 Pydantic schema | ❌ | 字段名已说明一切 |
| 复述代码行为的注释 | ❌ | 负资产 |
| 每个函数都写 docstring | ❌ | 只给跨模块调用的写 |
| 错误码本体 | ❌ | SPEC 6.4 已有定义 |

**判断标准**：删掉注释后读者 5 秒内能看懂 → 注释多余。

---

## 三、强制要求（DoD 卡口）

1. 每个 `.py` 文件头必须含 **SPEC 版本 + 覆盖 FR + 对应 AC**；
2. 关键安全决策点必须注明 **依据（FR/AC/SPEC 章节）**；
3. 违反上述两条视为 **DoD 不合格**，禁止 `git_ship`；
4. 注释中出现凭证明文 → 直接阻塞（PRD 10.5）。

---

## 四、文件头模板（按模块复制）

### M-A 认证鉴权
```python
"""M-A 认证鉴权 — <功能一句话>。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 6.4 章错误码
覆盖 FR：FR-58/65/66
对应 AC：AC-11（认证边界）、AC-12（多租户隔离）
"""
```

### M-B 项目与工作区治理
```python
"""M-B 项目与工作区治理 — <功能一句话>。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-B
覆盖 FR：FR-01/02/03
对应 AC：AC-01（职责分离）、AC-12（多租户隔离）
"""
```

### M-C 数据连接与凭据治理
```python
"""M-C 数据连接与凭据治理 — <功能一句话>。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C
覆盖 FR：FR-04~11
对应 AC：AC-02（无敏感明文泄露）
"""
```

### M-D 元数据探查与数据分级
```python
"""M-D 元数据探查与数据分级 — <功能一句话>。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-D
覆盖 FR：FR-12/14/15
对应 AC：AC-02（脱敏样本无明文）
"""
```

### 其他模块（M-E ~ M-P）
按 SPEC 3.2 对应章节填写，格式同上。