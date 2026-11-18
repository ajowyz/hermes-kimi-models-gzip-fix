# Hermes Agent：Kimi 提供商模型列表静默过期（gzip 解码 bug）及修复

> English version: [README.md](./README.md)

## 摘要

Hermes Agent 内置的 `kimi-coding` 提供商插件在请求模型列表时携带
`Accept-Encoding: gzip`，但 `providers/base.py` 中共享的列表拉取代码使用
urllib 读取响应后**从不解压 gzip**。拉取因此抛出 `UnicodeDecodeError`，
异常被静默吞掉，模型选择器随后回退到过时的内置（curated）名单——所以
新模型（例如 `kimi-k3.1-pro`、`kimi-k3.1-pro-highspeed`）永远不会出现，
即使你的 API key 本身能列出它们。

本仓库记录了该 bug，并附带一个**用户覆盖插件**，在不改动 Hermes 核心
代码的前提下修复列表拉取。

## 现象

- 受影响提供商：`kimi-coding-cn`（`kimi-coding` 同样受影响）。
- `/model` 选择器（CLI / 桌面应用）一直显示旧模型列表
  （`kimi-k3`、`kimi-k2.7-code`……），新发布的模型从不出现。
- 删除 `$HERMES_HOME/provider_models_cache.json` **没有用**：重新拉取
  会以同样的方式失败，然后把旧的 curated 名单写回缓存。

## 根因

1. `plugins/model-providers/kimi-coding/__init__.py` 在插件请求头中设置了
   `"Accept-Encoding": "gzip"`（这是官方为规避聊天响应 brotli 流式解码
   bug 而做的刻意设计——见 issue #28043、#48428、#59556）。
2. `ProviderProfile.fetch_models`（`providers/base.py`）用 urllib 构造
   请求并转发 `self.default_headers`，然后执行：
   ```python
   with open_credentialed_url(req, timeout=timeout) as resp:
       data = json.loads(resp.read().decode())
   ```
   urllib 不会自动解压；响应体是 gzip（魔数 `1f 8b`），`.decode()` 于是
   抛出 `'utf-8' codec can't decode byte 0x8b in position 1`。
3. 异常被 `except Exception … return None` 捕获，选择器因此静默地
   回退到静态 curated 名单。

开启 debug 日志可复现失败过程：

```python
logging.basicConfig(level=logging.DEBUG)
prof.fetch_models(api_key=key, base_url=prof.base_url)
# -> providers.base DEBUG fetch_models(kimi-coding-cn):
#    'utf-8' codec can't decode byte 0x8b in position 1: invalid start byte
# -> 返回 None
```

而接口本身没有问题——用同一个 key 直接 GET 返回的是：

```
['kimi-k3', 'kimi-k3.1-pro', 'kimi-k3.1-pro-highspeed']
```

## 修复方案（用户覆盖插件，不改核心代码）

Hermes 约定同名用户插件覆盖内置插件。安装覆盖插件：

```
$HERMES_HOME/plugins/model-providers/kimi-coding/
├── plugin.yaml
└── __init__.py
```

（两个文件都在本仓库 [`override/`](./override) 目录中。）它将
`fetch_models` 重新实现为 gzip 感知：

```python
with open_credentialed_url(req, timeout=timeout) as resp:
    raw = resp.read()
    if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode())
```

然后清除选择器缓存并重启 Hermes：

```bash
rm "$HERMES_HOME/provider_models_cache.json"   # Windows：直接删除该文件
# 完全退出并重新打开桌面应用 / CLI
```

已在 Hermes（Windows 11，提供商 `kimi-coding-cn`）验证：修复后选择器能
列出 `kimi-k3`、`kimi-k3.1-pro`、`kimi-k3.1-pro-highspeed` 全部三个模型。

## 建议的上游补丁

在 `providers/base.py::ProviderProfile.fetch_models` 中，按服务端声明
解压（或者列表请求不要转发 `Accept-Encoding`）：

```python
with open_credentialed_url(req, timeout=timeout) as resp:
    raw = resp.read()
    if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
        import gzip
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode())
```

这样可以一次性修复所有设置了压缩请求头的提供商，而不仅是 Kimi。

## 不使用覆盖插件的临时方案

模型别名完全绕开选择器列表，随时可用：

```bash
hermes config set model.aliases.k31      "kimi-coding-cn/kimi-k3.1-pro"
hermes config set model.aliases.k31-fast "kimi-coding-cn/kimi-k3.1-pro-highspeed"
# 然后：/model k31  或  /model k31-fast
```
