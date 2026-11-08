from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

pytest.importorskip("google.cloud.pubsub_v1", reason="Install the pubsub dependency group")

from google.api_core.exceptions import DeadlineExceeded
from app import pubsub_tasks as transport
from app.config import Settings


@pytest.fixture
def settings():
    return Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test")


def subscription(settings):
    topic, _, dead_letter = transport._paths(settings)
    return SimpleNamespace(topic=topic, ack_deadline_seconds=120,
        dead_letter_policy=SimpleNamespace(dead_letter_topic=dead_letter, max_delivery_attempts=5),
        retry_policy=SimpleNamespace(minimum_backoff=SimpleNamespace(seconds=10), maximum_backoff=SimpleNamespace(seconds=60)))


@pytest.mark.parametrize("host", [None, "external.example:8085", "127.0.0.1:9999"])
def test_init_refuses_nonlocal_emulator_before_any_client_call(settings, monkeypatch, host):
    if host is None:
        monkeypatch.delenv("PUBSUB_EMULATOR_HOST", raising=False)
    else:
        monkeypatch.setenv("PUBSUB_EMULATOR_HOST", host)
    publisher, subscriber = Mock(), Mock()
    with pytest.raises(ValueError):
        transport.initialize_emulator(publisher, subscriber, settings)
    assert not publisher.mock_calls and not subscriber.mock_calls


def test_init_creates_local_topics_and_bounded_subscription(settings, monkeypatch):
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")
    publisher, subscriber = Mock(), Mock()
    transport.initialize_emulator(publisher, subscriber, settings)
    assert publisher.create_topic.call_count == 2
    assert subscriber.create_subscription.call_count == 2
    main = subscriber.create_subscription.call_args.kwargs
    assert main["retry"] is None and main["timeout"] == 10
    assert main["request"]["dead_letter_policy"]["max_delivery_attempts"] == 5
    assert main["request"]["ack_deadline_seconds"] == 120


@pytest.mark.parametrize("invalid", [None, "topic", "ack", "dead_letter", "attempts", "retry"])
def test_worker_checks_subscription_policy(settings, invalid):
    current = subscription(settings)
    if invalid == "topic": current.topic = "projects/other/topics/other"
    if invalid == "ack": current.ack_deadline_seconds = 10
    if invalid == "dead_letter": current.dead_letter_policy.dead_letter_topic = ""
    if invalid == "attempts": current.dead_letter_policy.max_delivery_attempts = 100
    if invalid == "retry": current.retry_policy.minimum_backoff.seconds = 0
    subscriber = Mock()
    subscriber.get_subscription.return_value = current
    if invalid:
        with pytest.raises(ValueError): transport.check_subscription(subscriber, settings)
    else:
        transport.check_subscription(subscriber, settings)


def received_message():
    return SimpleNamespace(ack_id="test-ack", message=SimpleNamespace(data=str(uuid4()).encode()))


@pytest.mark.parametrize("state", ["succeeded", "failed", "running", "missing", "discarded"])
def test_acknowledges_all_processed_outcomes_without_retrying(state, monkeypatch):
    received = received_message()
    subscriber = Mock()
    subscriber.pull.return_value = SimpleNamespace(received_messages=[received])
    calls = []
    async def handle(data, bind, **kwargs):
        calls.append(data)
        assert subscriber.modify_ack_deadline.call_args.kwargs["request"]["ack_deadline_seconds"] == 120
        assert not subscriber.acknowledge.called
        return state
    monkeypatch.setattr(transport, "handle_message", handle)
    assert transport.consume_one(subscriber, "local-subscription", None) == state
    assert calls == [received.message.data]
    assert subscriber.pull.call_args.kwargs == {"request": {"subscription": "local-subscription", "max_messages": 1}, "retry": None, "timeout": 10}
    assert subscriber.acknowledge.call_args.kwargs["request"] == {"subscription": "local-subscription", "ack_ids": ["test-ack"]}


def test_infrastructure_error_nacks_and_stops_instead_of_repeating_model(monkeypatch):
    subscriber = Mock()
    subscriber.pull.return_value = SimpleNamespace(received_messages=[received_message()])
    async def fail(*args, **kwargs): raise RuntimeError("private database details")
    monkeypatch.setattr(transport, "handle_message", fail)
    with pytest.raises(RuntimeError): transport.consume_one(subscriber, "local-subscription", None)
    assert subscriber.modify_ack_deadline.call_args.kwargs["request"]["ack_deadline_seconds"] == 0
    assert not subscriber.acknowledge.called


@pytest.mark.parametrize("timeout", [False, True])
def test_empty_or_timed_out_pull_returns_without_ack(timeout):
    subscriber = Mock()
    if timeout: subscriber.pull.side_effect = DeadlineExceeded("empty")
    else: subscriber.pull.return_value = SimpleNamespace(received_messages=[])
    assert transport.consume_one(subscriber, "local-subscription", None) == "empty"
    assert not subscriber.acknowledge.called


def test_cli_suppresses_credentials_and_provider_errors(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["pubsub_tasks", "dispatch"])
    def fail(): raise RuntimeError("private-key-and-provider-body")
    monkeypatch.setattr(transport, "get_settings", fail)
    assert transport.main() == 1
    output = capsys.readouterr()
    assert "private-key" not in output.err and "failed" in output.err
