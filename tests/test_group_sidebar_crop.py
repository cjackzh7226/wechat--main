from PIL import Image, ImageDraw

from capture_ocr import _crop_detected_chat_area_with_origin
from wechat_window import ChatPlatform


def _wecom_screenshot(*, with_group_sidebar: bool) -> tuple[Image.Image, int | None]:
    """Build a synthetic WeCom 4.x layout without using customer screenshots."""
    width, height = 1386, 1015
    image = Image.new("RGB", (width, height), (247, 249, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 373, height - 1), fill=(235, 238, 242))

    if not with_group_sidebar:
        return image, None

    divider_x = 1185
    draw.rectangle((divider_x + 1, 0, width - 1, height - 1), fill=(250, 251, 252))
    # 企业微信当前版本的群详情边界只有一个物理像素宽，并非两列。
    # 留出少量上下缺口，避免测试只覆盖“完美贯穿整窗”的理想情况。
    draw.line((divider_x, 95, divider_x, 965), fill=(235, 235, 235), width=1)
    # 模拟群公告、群成员文字；这些像素绝不能进入聊天 OCR 裁剪区。
    for y in (145, 190, 230, 270, 310, 350):
        draw.rectangle((1210, y, 1335, y + 7), fill=(80, 100, 120))
    return image, divider_x


def test_wecom_group_sidebar_with_single_pixel_divider_is_excluded():
    image, divider_x = _wecom_screenshot(with_group_sidebar=True)
    cropped, origin = _crop_detected_chat_area_with_origin(
        image,
        "wecom",
        ChatPlatform.for_key("wecom").chat_area,
    )

    assert divider_x is not None
    assert origin[0] + cropped.width == divider_x


def test_wecom_personal_chat_without_sidebar_keeps_the_right_chat_edge():
    image, _divider_x = _wecom_screenshot(with_group_sidebar=False)
    cropped, origin = _crop_detected_chat_area_with_origin(
        image,
        "wecom",
        ChatPlatform.for_key("wecom").chat_area,
    )

    assert origin[0] + cropped.width == int(image.width * 0.99)
