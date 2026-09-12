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


@pytest.mark.parametrize(
    ("width", "height", "conversation_list_width", "sidebar_width", "input_top_ratio"),
    (
        (720, 520, 210, 190, 0.76),
        (1000, 700, 280, 190, 0.80),
        (1201, 877, 399, 144, 0.81),
        (1920, 1080, 410, 260, 0.78),
    ),
)
def test_wecom_crop_follows_input_panel_and_keeps_latest_message_below_old_fixed_limit(
    width: int,
    height: int,
    conversation_list_width: int,
    sidebar_width: int,
    input_top_ratio: float,
):
    """The latest bubble may sit below the old fixed 72% crop boundary."""
    image, chat_left, divider_x = _wecom_screenshot(
        width=width,
        height=height,
        conversation_list_width=conversation_list_width,
        sidebar_width=sidebar_width,
    )
    assert divider_x is not None
    input_top = int(height * input_top_ratio)
    old_fixed_bottom = int(height * ChatPlatform.for_key("wecom").chat_area.y_bottom)
    latest_bubble_bottom = input_top - max(10, height // 35)
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (chat_left + 24, old_fixed_bottom - 4, chat_left + width // 3, latest_bubble_bottom),
        fill=(235, 238, 242),
    )
    draw.rectangle((chat_left, input_top, divider_x - 1, height - 1), fill=(255, 255, 255))
    draw.line((chat_left, input_top, divider_x - 1, input_top), fill=(229, 232, 235), width=1)

    cropped, origin = _crop_detected_chat_area_with_origin(
        image,
        "wecom",
        ChatPlatform.for_key("wecom").chat_area,
    )
    crop_bottom = origin[1] + cropped.height

    assert crop_bottom > latest_bubble_bottom
    assert input_top - 6 <= crop_bottom <= input_top


@pytest.mark.parametrize(
    ("message_color", "input_color", "divider_color"),
    (
        ((247, 249, 252), (255, 255, 255), (229, 232, 235)),
        ((236, 239, 244), (246, 247, 249), (218, 221, 225)),
        ((43, 47, 53), (31, 34, 39), (72, 76, 82)),
        ((247, 249, 252), (247, 249, 252), (215, 218, 222)),
    ),
)
def test_wecom_input_boundary_uses_structure_instead_of_a_white_theme(
    message_color: tuple[int, int, int],
    input_color: tuple[int, int, int],
    divider_color: tuple[int, int, int],
):
    width, height = 1000, 700
    chat_left = int(width * ChatPlatform.for_key("wecom").chat_area.x_left)
    chat_right = int(width * ChatPlatform.for_key("wecom").chat_area.x_right)
    # Use an odd row so a one-pixel divider cannot pass only by matching a
    # particular scan-step parity.
    input_top = int(height * 0.80) + 1
    image = Image.new("RGB", (width, height), message_color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((chat_left, input_top, chat_right - 1, height - 1), fill=input_color)
    draw.line((chat_left, input_top, chat_right - 1, input_top), fill=divider_color, width=1)

    cropped, origin = _crop_detected_chat_area_with_origin(
        image,
        "wecom",
        ChatPlatform.for_key("wecom").chat_area,
    )
    crop_bottom = origin[1] + cropped.height

    assert input_top - 6 <= crop_bottom <= input_top


def test_wide_message_bubble_is_not_mistaken_for_the_input_panel() -> None:
    width, height = 1000, 700
    area = ChatPlatform.for_key("wecom").chat_area
    chat_left = int(width * area.x_left)
    chat_right = int(width * area.x_right)
    image = Image.new("RGB", (width, height), (247, 249, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (chat_left + 40, int(height * 0.61), chat_right - 120, int(height * 0.70)),
        fill=(235, 238, 242),
    )

    cropped, origin = _crop_detected_chat_area_with_origin(image, "wecom", area)

    assert origin[1] + cropped.height == int(height * area.y_bottom)
