"""图像输入的缓存安全默认缩放（pi catalog/build.ts 对位）。"""

from nova_protocol import ModelFields

_DEFAULT_RESIZE = {
    "max_width": 2000,
    "max_height": 2000,
    "max_bytes": int(4.5 * 1024 * 1024),
    "jpeg_quality": 80,
}


def apply_image_resize_defaults(fields: ModelFields) -> None:
    """视觉模型补保守 resize；provider 已给出的更窄值优先。"""
    if "image" not in (fields.get("input_types") or []):
        return
    input_limits = dict(fields.get("input_limits") or {})
    images = dict(input_limits.get("images") or {})
    resize = dict(images.get("resize") or {})
    images["resize"] = {**_DEFAULT_RESIZE, **resize}
    input_limits["images"] = images
    fields["input_limits"] = input_limits


__all__ = ["apply_image_resize_defaults"]
