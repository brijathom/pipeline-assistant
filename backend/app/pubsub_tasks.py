"""Bounded Pub/Sub dispatch and pull processing; no in-process API background work."""
import argparse
import asyncio
import os
import sys

from app.config import get_settings
from app.database import get_engine
from app.task_delivery import dispatch_one, handle_message

RPC_TIMEOUT = 10
ACK_SECONDS = 120


def _paths(settings):
    prefix = f"projects/{settings.pubsub_project_id}"
    return (f"{prefix}/topics/{settings.pubsub_topic_id}",
            f"{prefix}/subscriptions/{settings.pubsub_subscription_id}",
            f"{prefix}/topics/{settings.pubsub_topic_id}-dead-letter")


def _emulator_only(settings) -> None:
    if os.environ.get("PUBSUB_EMULATOR_HOST") not in {"127.0.0.1:8085", "localhost:8085"} or settings.pubsub_project_id != "pipeline-assistant-local":
        raise ValueError("Emulator initialization requires loopback port 8085 and the local project.")


def initialize_emulator(publisher, subscriber, settings) -> None:
    from google.api_core.exceptions import AlreadyExists
    _emulator_only(settings)
    topic, subscription, dead_letter = _paths(settings)
    for name in (topic, dead_letter):
        try:
            publisher.create_topic(request={"name": name}, retry=None, timeout=RPC_TIMEOUT)
        except AlreadyExists:
            pass
    for name, source in ((subscription + "-dead-letter", dead_letter), (subscription, topic)):
        request = {"name": name, "topic": source, "ack_deadline_seconds": ACK_SECONDS}
        if name == subscription:
            request.update(dead_letter_policy={"dead_letter_topic": dead_letter, "max_delivery_attempts": 5},
                           retry_policy={"minimum_backoff": {"seconds": 10}, "maximum_backoff": {"seconds": 60}})
        try:
            subscriber.create_subscription(request=request, retry=None, timeout=RPC_TIMEOUT)
        except AlreadyExists:
            pass


def check_subscription(subscriber, settings) -> None:
    topic, subscription, dead_letter = _paths(settings)
    current = subscriber.get_subscription(request={"subscription": subscription}, retry=None, timeout=RPC_TIMEOUT)
    if (current.topic != topic or current.ack_deadline_seconds < ACK_SECONDS
            or current.dead_letter_policy.dead_letter_topic != dead_letter
            or current.dead_letter_policy.max_delivery_attempts != 5
            or current.retry_policy.minimum_backoff.seconds != 10
            or current.retry_policy.maximum_backoff.seconds != 60):
        raise ValueError("Subscription requires the documented topic, acknowledgement, retry and dead-letter policy.")


def consume_one(subscriber, subscription: str, bind, *, api_key=None, generate=None) -> str:
    from google.api_core.exceptions import DeadlineExceeded
    try:
        response = subscriber.pull(request={"subscription": subscription, "max_messages": 1}, retry=None, timeout=RPC_TIMEOUT)
    except DeadlineExceeded:
        return "empty"
    if not response.received_messages:
        return "empty"
    received = response.received_messages[0]
    request = {"subscription": subscription, "ack_ids": [received.ack_id]}
    try:
        subscriber.modify_ack_deadline(request={**request, "ack_deadline_seconds": ACK_SECONDS}, retry=None, timeout=RPC_TIMEOUT)
        result = asyncio.run(handle_message(received.message.data, bind, api_key=api_key, generate=generate))
    except Exception:
        # Infrastructure errors get message retries, not new model attempts.
        # Failed/running/succeeded persisted tasks are acknowledged on redelivery.
        subscriber.modify_ack_deadline(request={**request, "ack_deadline_seconds": 0}, retry=None, timeout=RPC_TIMEOUT)
        raise
    subscriber.acknowledge(request=request, retry=None, timeout=RPC_TIMEOUT)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init-emulator", "dispatch", "work", "status"))
    parser.add_argument("--limit", type=int, default=10, help="Maximum publications or messages (1-20); exits on an empty pull")
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error("--limit must be between 1 and 20")
    publisher = subscriber = None
    try:
        settings = get_settings()
        if args.action == "status":
            from sqlalchemy import select
            from sqlalchemy.orm import Session
            from app.models import InvestigationTask
            with Session(get_engine()) as session:
                rows = session.execute(select(InvestigationTask.id, InvestigationTask.status, InvestigationTask.dispatch_attempts,
                    InvestigationTask.dispatch_after, InvestigationTask.dispatched_at).order_by(InvestigationTask.created_at.desc()).limit(50))
                for task_id, status, attempts, after, sent in rows:
                    print(f"{task_id}: {status}; dispatch attempts={attempts}/5; sent={sent is not None}; next eligibility={after.isoformat()}")
            return 0
        if args.action == "init-emulator":
            _emulator_only(settings)  # Guard before constructing any client or reading ADC.
        if args.action == "work" and (settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value().strip()):
            raise ValueError("Worker generation is not configured.")
        from google.cloud import pubsub_v1
        topic, subscription, _ = _paths(settings)
        if args.action in {"init-emulator", "dispatch"}:
            publisher = pubsub_v1.PublisherClient(publisher_options=pubsub_v1.types.PublisherOptions(
                enable_message_ordering=False, retry=None, timeout=RPC_TIMEOUT))
        if args.action in {"init-emulator", "work"}:
            subscriber = pubsub_v1.SubscriberClient()
        if args.action == "init-emulator":
            initialize_emulator(publisher, subscriber, settings)
            check_subscription(subscriber, settings)
            print("Local emulator topic, worker subscription and dead-letter subscription are ready.")
            return 0
        if args.action == "work":
            check_subscription(subscriber, settings)
        def publish(data):
            publisher.publish(topic, data).result(timeout=RPC_TIMEOUT + 2)
        for _ in range(args.limit):
            if args.action == "dispatch":
                result = dispatch_one(get_engine(), publish)
                if result is None:
                    break
                print(f"Task {result[0]}: {'dispatched' if result[1] else 'publication unconfirmed; attempt recorded'}.")
                if not result[1]:
                    return 1
            else:
                result = consume_one(subscriber, subscription, get_engine(), api_key=settings.gemini_api_key)
                if result == "empty":
                    break
                print(f"Message processed: {result}. No report was approved or published.")
        return 0
    except Exception:
        print("Pub/Sub operation failed. Check configuration, credentials, dispatch status and task status before retrying. Private error details were suppressed.", file=sys.stderr)
        return 1
    finally:
        if publisher is not None:
            publisher.stop()
            publisher.transport.close()
        if subscriber is not None:
            subscriber.close()


if __name__ == "__main__":
    raise SystemExit(main())
