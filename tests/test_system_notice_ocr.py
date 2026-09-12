"""System notices must not change the customer turn or swallow real bubbles."""

import pytest
from PIL import Image, ImageDraw, ImageFont

from capture_ocr import ChatOCR
from group_chat_handler import GroupChatHandler
from settings import AppSettings


def _item(text, left, top, right, bottom):
    return dict(text=text, left=left, right=right, top=top, bottom=bottom,
                x=(left + right) / 2, y=(top + bottom) / 2)


def _scene(notice, *, width=700, bubble=False, centered=True, color=(153, 153, 153)):
    image = Image.new("RGB", (width, 240), (245, 247, 250))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 14)
    text_width = int(draw.textlength(notice, font=font)) + 2
    left = (width - text_width) // 2 if centered else 30
    if bubble:
        draw.rounded_rectangle((left - 12, 128, left + text_width + 12, 166),
                               radius=6, fill=(228, 230, 233))
    draw.text((left, 135), notice, font=font, fill=color)
    bounds = draw.textbbox((left, 135), notice, font=font)
    return image, _item(notice, *bounds)


@pytest.mark.parametrize("notice", [
    '来自微信的测试用户通过扫描测试成员分享的二维码加入了外部群聊',
    '"测试成员"邀请"新成员"加入了群聊',
    '测试成员加入了群聊',
    '测试成员退出了群聊',
    '来自微信的' + '测试机构名称' * 12 + '通过扫描测试成员分享的二维码加入了外部群聊',
])
def test_complete_membership_notice_is_filtered_without_image(notice):
    lines = ChatOCR._to_chat_lines([_item(notice, 30, 100, 670, 118)], 700)
    assert lines == []


@pytest.mark.parametrize("width", [480, 700, 1200])
def test_centered_muted_notice_is_filtered_even_with_unrecognized_text(width):
    image, item = _scene("系统提示识别残缺的文字", width=width)
    assert ChatOCR._to_chat_lines([item], width, image) == []


def test_fragmented_membership_notice_does_not_merge_into_question():
    items = [
        _item("测试成员@微信", 24, 30, 140, 47),
        _item("年报什么时候结束 @测试客服", 30, 58, 410, 76),
        _item("来自微信的测试机构用户", 65, 97, 345, 111),
        _item("通过扫描测试成员分享的二维码加入", 65, 119, 560, 133),
        _item("了外部群聊", 285, 141, 415, 155),
    ]
    lines = ChatOCR._to_chat_lines(items, 700)
    assert [(line.role, line.text) for line in lines] == [
        ("发送者", "测试成员@微信"), ("对方", "年报什么时候结束 @测试客服")
    ]


def test_notice_close_to_question_does_not_consume_question():
    items = [_item("年报什么时候结束 @测试客服", 30, 62, 610, 78),
             _item("来自微信的测试成员", 80, 86, 330, 100),
             _item("通过扫描二维码加入了外部群聊", 80, 110, 580, 124)]
    assert [line.text for line in ChatOCR._to_chat_lines(items, 700)] == [items[0]["text"]]


@pytest.mark.parametrize("text", [
    "怎么加入群聊？", "通过扫描二维码加入群聊失败怎么办？",
    '页面提示“测试成员加入了群聊”，但是没有进去，怎么办？',
    "系统提示识别残缺的文字", "测试成员加入了群聊",
])
def test_real_bubble_keeps_membership_words_and_quoted_notices(text):
    image, item = _scene(text, bubble=True)
    assert [line.text for line in ChatOCR._to_chat_lines([item], 700, image)] == [text]


def test_left_aligned_muted_sender_is_not_a_system_notice():
    image, item = _scene("测试成员", centered=False)
    assert [line.text for line in ChatOCR._to_chat_lines([item], 700, image)] == ["测试成员"]


def test_centered_dark_message_without_clear_bubble_is_kept():
    image, item = _scene("这是正常的消息内容", color=(30, 30, 30))
    assert [line.text for line in ChatOCR._to_chat_lines([item], 700, image)] == [item["text"]]


def test_system_only_image_has_no_customer_turn():
    image, item = _scene("新成员通过二维码进入本群")
    ocr = ChatOCR(AppSettings())
    ocr._recognize = lambda _image: [item]
    context = ocr.read_image(image)
    assert context == ""
    assert not GroupChatHandler().detect_at_mention(context, "测试客服")


def test_arriving_notice_leaves_customer_context_unchanged():
    image, notice = _scene("新成员通过二维码进入本群")
    customer = [_item("测试成员@微信", 25, 26, 135, 42),
                _item("年报什么时候结束 @测试客服", 30, 62, 330, 80)]
    before = ChatOCR._to_chat_lines(customer, 700, image)
    after = ChatOCR._to_chat_lines(customer + [notice], 700, image)
    assert [(line.role, line.text) for line in after] == [(line.role, line.text) for line in before]


def test_recall_stays_a_system_event_and_blocks_old_question():
    items = [_item("@测试客服 年报怎么修改", 30, 50, 280, 70),
             _item("测试成员撤回了一条消息", 220, 110, 480, 127)]
    lines = ChatOCR._to_chat_lines(items, 700)
    assert lines[-1].role == "系统"
    context = "\n".join(f"[{line.role}] {line.text}" for line in lines)
    assert not GroupChatHandler().detect_at_mention(context, "测试客服")
