"""加载项目根目录下的 encipher.py（外挂加密参数生成代码）。

上游自 5.8 起把内置 a_bogus 算法置为占位常量，签名能力改由根目录的
encipher.py 提供。本模块把该扩展点补进当前代码基线，行为与上游
src/tools/dynamic_import.py 一致：

- 文件不存在  -> 返回 None，程序继续使用内置算法（旧行为不变）
- 文件有错误  -> 打印提示后返回 None，不让采集流程崩溃
- 加载成功    -> 返回 DouYinParams 实例

删除 encipher.py 即可完全回到原始行为。
"""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

__all__ = ["load_external_douyin_params"]

_SENTINEL = object()
_cached = _SENTINEL


def _report(message: str) -> None:
    """纯 ASCII 输出，避免 Windows 控制台 GBK 编码下出现乱码。"""
    try:
        print(message, flush=True)
    except Exception:
        pass


def _base_dir() -> Path:
    """项目根目录；兼容 PyInstaller / cx_Freeze 冻结产物。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def load_external_douyin_params():
    """返回 encipher.py 中的 DouYinParams 实例，不可用时返回 None。"""
    global _cached
    if _cached is not _SENTINEL:
        return _cached

    _cached = None
    file_path = _base_dir() / "encipher.py"
    if not file_path.is_file():
        return None

    try:
        spec = spec_from_file_location("douk_external_encipher", file_path)
        if spec is None or spec.loader is None:
            _report("[encipher] load failed: cannot create module spec")
            return None
        module = module_from_spec(spec)
        # 注册到 sys.modules，供 encipher.py 内部的相对导入使用。
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        factory = getattr(module, "DouYinParams", None)
        if factory is None:
            _report("[encipher] load failed: DouYinParams not found")
            return None
        _cached = factory()
        websign = getattr(_cached, "_websign", None)
        _report(
            "[encipher] external signing loaded; WebSign="
            + ("ON" if websign is not None else "OFF (Node.js missing?)")
        )
    except Exception as error:  # noqa: BLE001 - 任何失败都必须可降级
        _report(f"[encipher] load failed: {error!r}")
        _cached = None
    return _cached
