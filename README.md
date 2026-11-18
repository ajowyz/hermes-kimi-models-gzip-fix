# Hermes Agent: Kimi provider model list silently stale (gzip decode bug) + fix

> 中文文档 / Chinese version: [README.zh-CN.md](./README.zh-CN.md)

## TL;DR

Hermes Agent's bundled `kimi-coding` provider plugin requests the model list
with `Accept-Encoding: gzip`, but the shared listing code in
`providers/base.py` reads the response with urllib and **never decompresses
gzip**. The fetch fails with a `UnicodeDecodeError`, is swallowed, and the
model picker silently falls back to the outdated bundled (curated) list — so
new models (e.g. `kimi-k3.1-pro`, `kimi-k3.1-pro-highspeed`) never appear,
even though the account's API key can list them.

This repo documents the bug and ships a **user-plugin override** that fixes
the listing fetch without touching Hermes core.

## Symptom

- Provider: `kimi-coding-cn` (also affects `kimi-coding`).
- The `/model` picker (CLI / desktop app) shows an old model list
  (`kimi-k3`, `kimi-k2.7-code`, …) and never shows newly released models.
- Deleting `$HERMES_HOME/provider_models_cache.json` does **not** help:
  the refetch fails the same way and the stale curated list is written back.

## Root cause

1. `plugins/model-providers/kimi-coding/__init__.py` sets profile headers
   including `"Accept-Encoding": "gzip"` (a deliberate workaround for a
   brotli streaming bug in chat responses — issues #28043, #48428, #59556).
2. `ProviderProfile.fetch_models` (`providers/base.py`) builds a urllib
   request with `self.default_headers` and then does:
   ```python
   with open_credentialed_url(req, timeout=timeout) as resp:
       data = json.loads(resp.read().decode())
   ```
   urllib does not auto-decompress; the body is gzip (magic bytes `1f 8b`),
   so `.decode()` raises
   `'utf-8' codec can't decode byte 0x8b in position 1`.
3. The exception is caught (`except Exception … return None`), so the picker
   degrades to the static curated catalog — silently.

Reproduce the failure with debug logging enabled:

```python
logging.basicConfig(level=logging.DEBUG)
prof.fetch_models(api_key=key, base_url=prof.base_url)
# -> providers.base DEBUG fetch_models(kimi-coding-cn):
#    'utf-8' codec can't decode byte 0x8b in position 1: invalid start byte
# -> returns None
```

Meanwhile the endpoint itself is fine — plain GET with the same key returns:

```
['kimi-k3', 'kimi-k3.1-pro', 'kimi-k3.1-pro-highspeed']
```

## Fix (user-plugin override, no core changes)

Hermes user plugins shadow bundled plugins of the same name. Install the
override:

```
$HERMES_HOME/plugins/model-providers/kimi-coding/
├── plugin.yaml
└── __init__.py
```

(both included in [`override/`](./override)). It reimplements `fetch_models`
gzip-aware:

```python
with open_credentialed_url(req, timeout=timeout) as resp:
    raw = resp.read()
    if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode())
```

Then clear the picker cache and restart Hermes:

```bash
rm "$HERMES_HOME/provider_models_cache.json"   # Windows: delete the file
# fully quit and restart the desktop app / CLI
```

Verified on Hermes (Windows 11, provider `kimi-coding-cn`): the picker then
lists `kimi-k3`, `kimi-k3.1-pro`, `kimi-k3.1-pro-highspeed`.

## Suggested upstream patch

In `providers/base.py::ProviderProfile.fetch_models`, decompress when the
server says so (or don't forward `Accept-Encoding` for the listing request):

```python
with open_credentialed_url(req, timeout=timeout) as resp:
    raw = resp.read()
    if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
        import gzip
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode())
```

This fixes every profile that sets a compression header, not just Kimi.

## Workaround without the override

Model aliases bypass the picker list entirely:

```bash
hermes config set model.aliases.k31      "kimi-coding-cn/kimi-k3.1-pro"
hermes config set model.aliases.k31-fast "kimi-coding-cn/kimi-k3.1-pro-highspeed"
# then: /model k31  or  /model k31-fast
```
