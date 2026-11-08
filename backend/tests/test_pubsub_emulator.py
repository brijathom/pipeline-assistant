"""Opt-in real Google emulator round trip; guarded test database and fake AI only."""
import os
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(os.environ.get("RUN_PUBSUB_EMULATOR_TESTS") != "1", reason="Start the local emulator and set RUN_PUBSUB_EMULATOR_TESTS=1")


def test_google_emulator_dispatch_and_duplicate_delivery(db_session, monkeypatch):
    pubsub = pytest.importorskip("google.cloud.pubsub_v1")
    from app.config import Settings
    from app.investigation_tasks import enqueue_task
    from app.investigations import build_report
    from app.models import PipelineRun, InvestigationTask, PipelineInvestigationDraft
    from app.pubsub_tasks import initialize_emulator, check_subscription, consume_one, _paths
    from app.seed import seed_runs, seed_demo_evidence
    from app.task_delivery import dispatch_one

    # Never use ambient ADC or a cloud project for this test.
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")
    suffix = uuid4().hex
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test",
        pubsub_project_id="pipeline-assistant-local", pubsub_topic_id=f"test-tasks-{suffix}", pubsub_subscription_id=f"test-worker-{suffix}")
    publisher = pubsub.PublisherClient(publisher_options=pubsub.types.PublisherOptions(retry=None, timeout=10))
    subscriber = pubsub.SubscriberClient()
    topic, subscription, dead_letter = _paths(settings)
    created = False
    try:
        initialize_emulator(publisher, subscriber, settings)
        created = True
        check_subscription(subscriber, settings)
        seed_runs(db_session)
        seed_demo_evidence(db_session)
        run = db_session.scalar(select(PipelineRun).where(PipelineRun.github_run_id == 1002))
        task_id = uuid4()
        enqueue_task(db_session, task_id, run.id, "gemini-3.5-flash-lite")
        db_session.commit()
        def publish(data): publisher.publish(topic, data).result(timeout=12)
        assert dispatch_one(db_session.get_bind(), publish) == (task_id, True)
        publish(str(task_id).encode())  # Deliberate broker duplicate, not a new task.
        calls = 0
        async def generate(bundle, **kwargs):
            nonlocal calls
            calls += 1
            return build_report(bundle)
        results = []
        deadline = time.monotonic() + 35
        while len(results) < 2 and time.monotonic() < deadline:
            result = consume_one(subscriber, subscription, db_session.get_bind(), generate=generate)
            if result != "empty": results.append(result)
        assert results == ["succeeded", "succeeded"] and calls == 1
        db_session.expire_all()
        task = db_session.get(InvestigationTask, task_id)
        assert db_session.get(PipelineInvestigationDraft, task.draft_id).status == "pending_review"
    finally:
        if created:
            # Only the UUID-named resources created by this test in the emulator.
            for name in (subscription, subscription + "-dead-letter"):
                subscriber.delete_subscription(request={"subscription": name}, retry=None, timeout=10)
            for name in (topic, dead_letter):
                publisher.delete_topic(request={"topic": name}, retry=None, timeout=10)
        publisher.stop()
        publisher.transport.close()
        subscriber.close()
