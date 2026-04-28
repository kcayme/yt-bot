import threading

from yt_bot.worker import ShutdownContext


def test_register_and_unregister():
    ctx = ShutdownContext()
    a = threading.Event()
    b = threading.Event()

    assert not ctx.has_active()
    ctx.register(a)
    ctx.register(b)
    assert ctx.has_active()

    ctx.unregister(a)
    assert ctx.has_active()
    ctx.unregister(b)
    assert not ctx.has_active()


def test_cancel_all_sets_shutdown_and_all_active():
    ctx = ShutdownContext()
    a = threading.Event()
    b = threading.Event()
    ctx.register(a)
    ctx.register(b)

    ctx.cancel_all()

    assert ctx.shutdown.is_set()
    assert a.is_set()
    assert b.is_set()


def test_cancel_all_no_active_just_sets_shutdown():
    ctx = ShutdownContext()
    ctx.cancel_all()
    assert ctx.shutdown.is_set()
    assert not ctx.has_active()


def test_unregister_unknown_is_noop():
    ctx = ShutdownContext()
    ctx.unregister(threading.Event())  # should not raise
