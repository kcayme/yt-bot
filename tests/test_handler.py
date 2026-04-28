import queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from yt_bot import config, handler


def _make_message(
    *,
    text: str = "",
    quoted_text: str = "",
    sender: str = "15551234567",
    is_from_me: bool = False,
    is_group: bool = False,
):
    quoted = SimpleNamespace(
        conversation="",
        extendedTextMessage=SimpleNamespace(text=quoted_text),
    )
    ext = SimpleNamespace(
        text=text,
        contextInfo=SimpleNamespace(quotedMessage=quoted),
    )
    msg = SimpleNamespace(
        Info=SimpleNamespace(
            MessageSource=SimpleNamespace(
                IsFromMe=is_from_me,
                IsGroup=is_group,
                Sender=SimpleNamespace(User=sender),
                Chat=SimpleNamespace(User=sender, USER_FIELD_NUMBER=1),
            )
        ),
        Message=SimpleNamespace(conversation="", extendedTextMessage=ext),
    )
    return msg


@pytest.fixture
def patch_fetch_title(monkeypatch):
    monkeypatch.setattr(handler, "fetch_title", lambda u: "Title")


@pytest.fixture(autouse=True)
def allowlist(monkeypatch):
    monkeypatch.setattr(
        config, "ALLOWED_SENDERS", {"15551234567"}
    )


def test_group_messages_ignored(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=3)
    msg = _make_message(text="@ytbot https://youtu.be/abc", is_group=True)

    handler.on_message(client, msg, q)

    assert q.empty()
    client.reply_message.assert_not_called()


def test_disallowed_sender_ignored(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=3)
    msg = _make_message(text="@ytbot https://youtu.be/abc", sender="999")

    handler.on_message(client, msg, q)

    assert q.empty()
    client.reply_message.assert_not_called()


def test_self_messages_accepted(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=3)
    # Sender not in allowlist, but IsFromMe should still allow it.
    msg = _make_message(
        text="@ytbot https://youtu.be/abc",
        sender="999",
        is_from_me=True,
    )

    handler.on_message(client, msg, q)

    assert q.qsize() == 1
    client.reply_message.assert_called_once()


def test_no_trigger_ignored(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=3)
    msg = _make_message(text="https://youtu.be/abc")

    handler.on_message(client, msg, q)

    assert q.empty()
    client.reply_message.assert_not_called()


def test_trigger_with_no_url_ignored(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=3)
    msg = _make_message(text="@ytbot hi")

    handler.on_message(client, msg, q)

    assert q.empty()
    client.reply_message.assert_not_called()


def test_trigger_and_url_queued(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=3)
    msg = _make_message(text="@ytbot https://youtu.be/abc")

    handler.on_message(client, msg, q)

    assert q.qsize() == 1
    chat, urls, original = q.get()
    assert urls == ["https://youtu.be/abc"]
    assert original is msg
    client.reply_message.assert_called_once()


def test_url_in_quoted_message(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=3)
    msg = _make_message(text="@ytbot", quoted_text="here https://youtu.be/xyz")

    handler.on_message(client, msg, q)

    assert q.qsize() == 1
    _, urls, _ = q.get()
    assert urls == ["https://youtu.be/xyz"]


def test_multiple_urls_collected(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=3)
    msg = _make_message(
        text="@ytbot https://youtu.be/aaa and https://share.google/zzz",
    )

    handler.on_message(client, msg, q)

    _, urls, _ = q.get()
    assert urls == ["https://youtu.be/aaa", "https://share.google/zzz"]


def test_queue_full_rejects(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=1)
    q.put(("placeholder", [], None))
    msg = _make_message(text="@ytbot https://youtu.be/abc")

    handler.on_message(client, msg, q)

    # Still only the placeholder; nothing was added.
    assert q.qsize() == 1
    client.reply_message.assert_called_once()
    call_text = client.reply_message.call_args[0][0]
    assert "busy" in call_text.lower()


def test_conversation_field_used_when_extendedtext_empty(patch_fetch_title):
    client = MagicMock()
    q = queue.Queue(maxsize=3)
    msg = _make_message(text="")
    msg.Message.conversation = "@ytbot https://youtu.be/abc"

    handler.on_message(client, msg, q)

    assert q.qsize() == 1
