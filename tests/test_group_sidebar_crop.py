import pytest
from PIL import Image, ImageDraw

from capture_ocr import _crop_detected_chat_area_with_origin
from wechat_window import ChatPlatform


def _wecom_screenshot(
    *,
    width: int,
    height: int,
    conversation_list_width: int,
    sidebar_width: int | None,
    divider_width: int = 1,
) -> tuple[Image.Image, int, int | None]:
    """Build a synthetic WeCom 4.x layout without using customer screenshots."""
    image = Image.new("RGB", (width, height), (247, 249, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, conversation_list_width - 1, height - 1), fill=(235, 238, 242))
    draw.rectangle(
        (conversation_list_width, 0, conversation_list_width + 1, height - 1),
        fill=(230, 233, 236),
    )

    if sidebar_width is None:
        return image, conversation_list_width, None

    divider_x = width - sidebar_width
    draw.rectangle((divider_x + divider_width, 0, width - 1, height - 1), fill=(250, 251, 252))
    # 群详情边界可能受 DPI 缩放影响呈现为 1~3 个物理像素。
    # 留出少量上下缺口，避免测试只覆盖“完美贯穿整窗”的理想情况。
    draw.rectangle(
        (
            divider_x,
            int(height * 0.09),
            divider_x + divider_width - 1,
            int(height * 0.95),
        ),
        fill=(235, 235, 235),
    )
    # 模拟群公告、群成员文字；这些像素绝不能进入聊天 OCR 裁剪区。
    text_left = divider_x + max(8, int(sidebar_width * 0.10))
    text_right = width - max(8, int(sidebar_width * 0.10))
    for ratio in (0.15, 0.20, 0.25, 0.30, 0.35):
        y = int(height * ratio)
        draw.rectangle((text_left, y, text_right, y + max(3, height // 140)), fill=(80, 100, 120))
    return image, conversation_list_width, divider_x


@pytest.mark.parametrize(
    ("width", "height", "conversation_list_width", "sidebar_width", "divider_width"),
    (
        (520, 480, 150, 220, 1),
        (720, 520, 210, 190, 1),
        (1000, 700, 280, 190, 2),
        (1386, 1015, 374, 201, 1),
        (1920, 1080, 410, 260, 3),
        (2560, 1440, 460, 320, 3),
    ),
)
def test_wecom_group_sidebar_is_excluded_at_every_window_size(
    width: int,
    height: int,
    conversation_list_width: int,
    sidebar_width: int,
    divider_width: int,
):
    image, chat_left, divider_x = _wecom_screenshot(
        width=width,
        height=height,
        conversation_list_width=conversation_list_width,
        sidebar_width=sidebar_width,
        divider_width=divider_width,
    )
    cropped, origin = _crop_detected_chat_area_with_origin(
        image,
        "wecom",
        ChatPlatform.for_key("wecom").chat_area,
    )

    assert divider_x is not None
    assert origin[0] == chat_left
    assert origin[0] + cropped.width == divider_x


@pytest.mark.parametrize(
    ("width", "height", "conversation_list_width"),
    ((520, 480, 150), (1000, 700, 280), (1386, 1015, 374), (1920, 1080, 410)),
)
def test_wecom_personal_chat_without_sidebar_keeps_the_right_chat_edge(
    width: int,
    height: int,
    conversation_list_width: int,
):
    image, chat_left, _divider_x = _wecom_screenshot(
        width=width,
        height=height,
        conversation_list_width=conversation_list_width,
        sidebar_width=None,
    )
    cropped, origin = _crop_detected_chat_area_with_origin(
        image,
        "wecom",
        ChatPlatform.for_key("wecom").chat_area,
    )

    assert origin[0] == chat_left
    assert origin[0] + cropped.width == int(image.width * 0.99)
