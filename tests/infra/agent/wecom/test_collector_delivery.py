from unittest.mock import AsyncMock, Mock

import pytest

from src.infra.agent.wecom.collector import (
    WECOM_MESSAGE_BYTE_LIMIT,
    WeComResponseCollector,
    _split_by_utf8_byte_limit,
)


def _collector(*, segmented_reply: bool = True) -> tuple[WeComResponseCollector, Mock]:
    client = Mock()
    client.reply_stream = AsyncMock(return_value=True)
    client.send_proactive_message = AsyncMock(return_value=True)
    manager = Mock()
    manager.find_bot.return_value = client
    collector = WeComResponseCollector(
        manager=manager,
        aibotid="bot-a",
        chat_id="user-1",
        segmented_reply=segmented_reply,
    )
    return collector, client


def test_split_prefers_chinese_sentence_boundaries_and_preserves_text() -> None:
    text = ("第一句。第二句！第三句？" * 300).strip()
    chunks = _split_by_utf8_byte_limit(text, byte_limit=120)

    assert "".join(chunks) == text
    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= 120 for chunk in chunks)
    assert all(chunk[-1] in "。！？" for chunk in chunks[:-1])


@pytest.mark.asyncio
async def test_normal_stream_finalization_sends_independent_segments() -> None:
    collector, client = _collector()
    collector._stream_started = True
    collector._stream_id = "stream-1"
    collector.text_parts = ["这是一个很长的回复。" * 500]

    assert await collector.finalize_stream_message() is True

    final_stream = client.reply_stream.await_args.args[2]
    proactive = [call.args[1] for call in client.send_proactive_message.await_args_list]
    assert len(final_stream.encode("utf-8")) <= WECOM_MESSAGE_BYTE_LIMIT
    assert proactive
    assert all(len(chunk.encode("utf-8")) <= WECOM_MESSAGE_BYTE_LIMIT for chunk in proactive)
    assert final_stream + "".join(proactive) == "这是一个很长的回复。" * 500


def test_only_first_revealed_file_is_selected() -> None:
    collector, _ = _collector()
    collector.add_file_to_reveal({"key": "one", "name": "one.pdf"})
    collector.add_file_to_reveal({"key": "two", "name": "two.pdf"})

    assert collector.files_to_reveal == [{"key": "one", "name": "one.pdf"}]

