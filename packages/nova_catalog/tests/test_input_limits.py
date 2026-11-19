"""视觉输入 resize 默认值的适用条件与合并优先级。"""

from nova_protocol import ModelFields

from nova_catalog.input_limits import apply_image_resize_defaults


def test_text_only_model_does_not_receive_resize_limits():
    fields = ModelFields(input_types=["text"])
    apply_image_resize_defaults(fields)
    assert "input_limits" not in fields


def test_vision_model_receives_complete_conservative_resize_defaults():
    fields = ModelFields(input_types=["text", "image"])
    apply_image_resize_defaults(fields)
    limits = fields.get("input_limits")
    assert limits is not None
    assert limits["images"]["resize"] == {
        "max_width": 2000,
        "max_height": 2000,
        "max_bytes": int(4.5 * 1024 * 1024),
        "jpeg_quality": 80,
    }


def test_existing_limits_and_resize_values_win_without_losing_siblings():
    fields = ModelFields(
        input_types=["image"],
        input_limits={
            "max_request_bytes": 123,
            "images": {
                "max_per_request": 2,
                "resize": {"max_width": 800, "jpeg_quality": 65},
            },
        },
    )
    apply_image_resize_defaults(fields)
    limits = fields.get("input_limits")
    assert limits is not None
    assert limits["max_request_bytes"] == 123
    assert limits["images"]["max_per_request"] == 2
    assert limits["images"]["resize"] == {
        "max_width": 800,
        "max_height": 2000,
        "max_bytes": int(4.5 * 1024 * 1024),
        "jpeg_quality": 65,
    }
