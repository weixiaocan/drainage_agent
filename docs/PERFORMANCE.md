# Ticket 13 性能与容量基线

基准使用 `scripts/generate_synthetic_capacity_data.py` 生成完全合成且可复现的数据，不包含真实项目、设备或点位信息。验收规模为 50 个点位、30 天、1 分钟采样，共 2,160,000 行。

2026-07-28 在本地 Windows 开发环境、`drainage-agent` Conda 环境中执行：

```powershell
python scripts/benchmark_capacity.py --clean
```

结果如下；机器可读记录位于 `docs/PERFORMANCE_BASELINE.json`。

| 阶段 | 耗时 | Python 峰值内存 |
|---|---:|---:|
| 合成数据生成 | 127.491 s | 0.2 MiB |
| 上传检查 | 16.200 s | 544.7 MiB |
| 分块标准化 | 114.937 s | 41.3 MiB |
| 数据质量分析 | 20.951 s | 621.2 MiB |

源 CSV 为 114,216,407 字节。峰值内存由 `tracemalloc` 测量，只覆盖 Python 分配器；Pandas/NumPy 原生分配和进程基础开销可能更高，因此生产部署建议至少提供 2 GiB 可用内存。

第一版支持容量止于 50 点位、30 天、1 分钟采样。首次标准化属于耗时操作，不承诺交互式秒级完成。半年连续数据仅用于压力探索，不属于当前容量承诺；如要支持，应先引入面向文件的上传检查与分区读取，不应直接提高限制。

默认单文件上传上限为 256 MiB，可通过 `DRAINAGE_MAX_UPLOAD_BYTES` 调整。提高上限前必须重新运行本基准，因为上传检查仍需要解码完整文件。
