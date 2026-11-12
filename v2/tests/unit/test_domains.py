import copy
import importlib
import unittest
from v2.shared.contracts import new_event, stable_id, validate
from v2.shared.routing import consumers


class Tx:
    def __init__(self):
        self.owner = "lab_consumer"
        self.data = {}
        self.out = []

    def get(self, kind, key):
        return copy.deepcopy(self.data.get((kind, key)))

    def put(self, kind, key, value):
        self.data[kind, key] = copy.deepcopy(value)

    def scan(self, kind):
        return [copy.deepcopy(v) for (k, _), v in self.data.items() if k == kind]

    def emit(self, e):
        validate(e)
        self.out.append(copy.deepcopy(e))


class Lab:
    def __init__(self):
        self.stores = {}
        self.events = []

    def handle(self, owner, event):
        tx = self.stores.setdefault(owner, Tx())
        tx.owner = owner
        importlib.import_module("v2.services." + owner + ".handler").handle(tx, event)
        self.events.extend(tx.out)
        tx.out = []

    def command(self, owner, action, subject="synthetic-user", **arguments):
        operation = stable_id(action, subject, str(arguments))
        e = new_event(
            owner + ".command.execute",
            owner,
            "command",
            operation,
            1,
            {
                "operation_id": operation,
                "action": action,
                "subject_id": subject,
                "actor": {"id": "synthetic-admin", "roles": ["admin"]},
                "expected_version": None,
                "arguments": arguments,
            },
        )
        self.handle(owner, e)
        return e

    def pump(self, limit=200):
        count = 0
        while self.events:
            e = self.events.pop(0)
            for o in consumers(e):
                self.handle(o, e)
            count += 1
            if count > limit:
                raise AssertionError("event loop did not quiesce")

    def rows(self, owner, kind):
        return self.stores.setdefault(owner, Tx()).scan(kind)


class DomainTests(unittest.TestCase):
    def setup_listing(self, text="SYNTHETIC rent 100 USD 2 rooms", message="m1"):
        lab = Lab()
        lab.command(
            "identity",
            "upsert_user",
            "synthetic-user",
            user_id="synthetic-user",
            consent=True,
        )
        lab.pump()
        lab.command(
            "notifications",
            "save_filter",
            "filter-A",
            user_id="synthetic-user",
            filter_id="filter-A",
            criteria={"max_price": 200},
        )
        lab.pump()
        lab.command(
            "listener",
            "ingest",
            message,
            text=text,
            source_id="source-A",
            channel_id="channel-A",
            message_id=message,
            update_type="received",
        )
        lab.pump()
        return lab

    def test_full_pipeline_and_initial_notification_once(self):
        lab = self.setup_listing()
        self.assertEqual(len(lab.rows("search", "listings")), 1)
        self.assertEqual(len(lab.rows("notifications", "jobs")), 1)
        lab.command("notifications", "deliver", sender_mode="success")
        lab.pump()
        self.assertEqual(lab.rows("notifications", "jobs")[0]["status"], "sent")
        for e in lab.rows("catalog", "snapshots"):
            lab.handle("notifications", e["event"])
        self.assertEqual(len(lab.rows("notifications", "jobs")), 1)

    def test_two_edits_then_empty_delete_do_not_resurrect(self):
        lab = self.setup_listing()
        for n in (120, 130):
            lab.command(
                "listener",
                "ingest",
                "m1",
                source_id="source-A",
                channel_id="channel-A",
                message_id="m1",
                update_type="edited",
                text=f"SYNTHETIC rent {n} USD 2 rooms",
            )
            lab.pump()
        lab.command(
            "listener",
            "ingest",
            "m1",
            source_id="source-A",
            channel_id="channel-A",
            message_id="m1",
            update_type="deleted",
        )
        lab.pump()
        self.assertEqual(lab.rows("search", "listings")[0]["visibility"], "hidden")
        self.assertEqual(lab.rows("listener", "messages")[0]["revision"], 4)

    def test_delete_before_create_blocks_delayed_creation(self):
        lab = Lab()
        lab.command(
            "listener",
            "ingest",
            "x",
            source_id="s",
            channel_id="c",
            message_id="x",
            update_type="deleted",
        )
        lab.pump()
        lab.command(
            "listener",
            "ingest",
            "x",
            source_id="s",
            channel_id="c",
            message_id="x",
            update_type="received",
            text="SYNTHETIC rent 100 USD",
        )
        lab.pump()
        self.assertFalse(lab.rows("search", "listings"))

    def test_other_active_source_keeps_merged_listing_visible(self):
        lab = self.setup_listing()
        lab.command(
            "listener",
            "ingest",
            "m2",
            source_id="source-B",
            channel_id="channel-B",
            message_id="m2",
            update_type="received",
            text="SYNTHETIC rent 100 USD 2 rooms",
        )
        lab.pump()
        self.assertEqual(len(lab.rows("catalog", "listings")), 1)
        lab.command(
            "listener",
            "ingest",
            "m1",
            source_id="source-A",
            channel_id="channel-A",
            message_id="m1",
            update_type="deleted",
        )
        lab.pump()
        self.assertEqual(lab.rows("search", "listings")[0]["visibility"], "active")

    def test_unknown_send_is_held_not_retried(self):
        lab = self.setup_listing()
        lab.command("notifications", "deliver", sender_mode="unknown")
        lab.pump()
        lab.command("notifications", "deliver", sender_mode="success")
        lab.pump()
        self.assertEqual(lab.rows("notifications", "jobs")[0]["status"], "unknown")
        self.assertEqual(len(lab.rows("notifications", "attempts")), 1)


class LifecycleTests(unittest.TestCase):
    def test_digest_fanout_keeps_real_message_identity_and_reparse_held(self):
        lab = Lab()
        lab.command(
            "listener",
            "set_subscription",
            "digest-source",
            recipe_key="generic.digest_blocks",
        )
        lab.pump()
        lab.command(
            "listener",
            "ingest",
            "digest",
            source_id="digest-source",
            channel_id="c",
            message_id="digest",
            text="1. SYNTHETIC rent USD 100 2 rooms\n2. SYNTHETIC rent USD 150 3 rooms",
        )
        lab.pump()
        rows = lab.rows("search", "listings")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["source_evidence"][0]["message_uid"] for r in rows}), 1)
        uid = rows[0]["source_evidence"][0]["message_uid"]
        lab.command(
            "source_parser",
            "reparse",
            uid,
            recipe_key="generic.single_listing",
            reason="synthetic merge interpretation",
        )
        lab.pump()
        self.assertTrue(any(r["visibility"] == "review" for r in lab.rows("catalog", "listings")))
        self.assertEqual(
            len([r for r in lab.rows("catalog", "listings") if r["visibility"] == "active"]),
            2,
        )

    def test_album_late_member_updates_same_canonical_and_keeps_members(self):
        lab = Lab()
        for n in (1, 2):
            lab.command(
                "listener",
                "ingest",
                str(n),
                source_id="album-source",
                channel_id="c",
                message_id=str(n),
                native_group_id="g",
                text="SYNTHETIC rent USD 100 2 rooms",
            )
            lab.pump()
        self.assertFalse(lab.rows("search", "listings"))
        lab.command("listener", "flush_albums", approved=True)
        lab.pump()
        first = lab.rows("search", "listings")[0]
        self.assertEqual(len(first["source_evidence"]), 2)
        lab.command(
            "listener",
            "ingest",
            "3",
            source_id="album-source",
            channel_id="c",
            message_id="3",
            native_group_id="g",
            text="SYNTHETIC late caption",
        )
        lab.pump()
        lab.command("listener", "flush_albums", approved=True)
        lab.pump()
        current = lab.rows("search", "listings")[0]
        self.assertEqual(first["canonical_id"], current["canonical_id"])
        self.assertEqual(len(current["source_evidence"]), 3)

    def test_closed_buffer_quarantine_can_be_reparsed_with_audit(self):
        lab = Lab()
        lab.command(
            "listener",
            "set_subscription",
            "buffer-source",
            recipe_key="generic.buffered_thread",
        )
        lab.pump()
        lab.command(
            "listener",
            "ingest",
            "b",
            source_id="buffer-source",
            channel_id="c",
            message_id="b",
            text="SYNTHETIC rent USD 100 2 rooms",
        )
        lab.pump()
        self.assertFalse(lab.rows("search", "listings"))
        self.assertEqual(len(lab.rows("source_parser", "buffers")), 1)
        uid = stable_id("message", "buffer-source", "c", "b")
        lab.command(
            "source_parser",
            "release_quarantine",
            uid,
            recipe_key="generic.single_listing",
            reason="synthetic operator approval",
        )
        lab.pump()
        self.assertEqual(len(lab.rows("source_parser", "replay_audit")), 1)
        self.assertEqual(len(lab.rows("search", "listings")), 1)

    def test_content_unknown_does_not_republish(self):
        lab = Lab()
        lab.command("content", "schedule", "post-1")
        lab.pump()
        lab.command("content", "publish", "post-1", sender_mode="unknown")
        lab.pump()
        lab.command("content", "publish", "post-1", sender_mode="success")
        lab.pump()
        self.assertEqual(lab.rows("content", "posts")[0]["status"], "unknown")
        self.assertEqual(len(lab.rows("content", "attempts")), 1)

    def test_taxonomy_remap_updates_catalog_filter_and_projection(self):
        lab = DomainTests().setup_listing()
        row = lab.rows("catalog", "listings")[0]
        row["listing"]["audience_tags"] = ["old"]
        lab.stores["catalog"].put("listings", row["canonical_id"], row)
        lab.command("taxonomy", "remap_tag", "old", tag="old", replacement="new")
        lab.pump()
        self.assertEqual(lab.rows("search", "listings")[0]["listing"]["audience_tags"], ["new"])


class AdditionalOwnerTests(unittest.TestCase):
    def test_ai_invalid_reply_is_audited_as_failed_without_catalog_event(self):
        lab = Lab()
        lab.command(
            "ai",
            "extract",
            "synthetic-run",
            text="SYNTHETIC invalid reply",
            mode="invalid",
        )
        lab.pump()
        self.assertEqual(lab.rows("ai", "runs")[0]["status"], "failed")
        self.assertFalse(lab.rows("catalog", "listings"))

    def test_source_approval_has_acknowledged_saga_result(self):
        lab = Lab()
        lab.command("identity", "upsert_user", "synthetic-user", user_id="synthetic-user")
        lab.pump()
        lab.command(
            "control",
            "suggest_source",
            "suggestion",
            source_id="synthetic-approved",
            user_id="synthetic-user",
        )
        lab.pump()
        lab.command(
            "control",
            "approve_source",
            "suggestion",
            recipe_key="generic.digest_blocks",
        )
        lab.pump()
        self.assertEqual(lab.rows("control", "suggestions")[0]["status"], "approved")
        self.assertEqual(lab.rows("control", "sagas")[0]["status"], "completed")


class DedupRecoveryTests(unittest.TestCase):
    def test_unconfirmed_signal_cannot_suppress_a_second_source_after_ai_failure(self):
        import json
        from pathlib import Path

        parsed = json.loads(Path("tests/fixtures/v2/contracts/06-digest-reparse.json").read_text())[
            "steps"
        ][0]["event"]
        lab = Lab()
        lab.handle("pre_ai", parsed)
        second = copy.deepcopy(parsed)
        second["event_id"] = stable_id("second-event")
        second["aggregate_id"] = stable_id("second-candidate")
        second["payload"]["lineage"]["candidate_id"] = second["aggregate_id"]
        second["payload"]["lineage"]["source_refs"][0]["message_uid"] = stable_id("second-message")
        lab.handle("pre_ai", second)
        self.assertEqual(lab.events[-1]["payload"]["decision"], "accepted")


class SearchParityTests(unittest.TestCase):
    def test_daily_price_is_normalized_before_search_and_sale_is_hidden(self):
        from v2.services.search.handler import query

        lab = Lab()
        lab.command(
            "listener",
            "ingest",
            "daily",
            source_id="price-source",
            channel_id="c",
            message_id="daily",
            text="SYNTHETIC daily USD 10 2 rooms",
        )
        lab.pump()
        tx = lab.stores["search"]
        self.assertEqual(query(tx, {"max_price": 299})["metadata"]["total"], 0)
        self.assertEqual(query(tx, {"min_price": 300, "max_price": 300})["metadata"]["total"], 1)
        lab.command(
            "listener",
            "ingest",
            "sale",
            source_id="price-source",
            channel_id="c",
            message_id="sale",
            text="SYNTHETIC sale USD 100",
        )
        lab.pump()
        self.assertEqual(query(tx, {})["metadata"]["total"], 1)


class MediaReadinessTests(unittest.TestCase):
    def test_actual_codec_output_reaches_catalog_without_bytes_in_event(self):
        lab = Lab()
        lab.command(
            "listener",
            "ingest",
            "image",
            source_id="media-source",
            channel_id="c",
            message_id="image",
            text="SYNTHETIC rent USD 100",
            media_capture_refs=["offline://fixtures/image.png"],
        )
        lab.pump()
        row = lab.rows("catalog", "listings")[0]
        self.assertEqual(row["media"]["state"], "ready")
        self.assertEqual(row["media"]["assets"][0]["width"], 16)
        self.assertEqual(len(lab.rows("ai", "objects")), 1)
        self.assertTrue(all("content" not in asset for asset in row["media"]["assets"]))

    def test_required_failed_media_prevents_publication(self):
        lab = Lab()
        lab.command(
            "listener",
            "ingest",
            "required",
            source_id="media-source",
            channel_id="c",
            message_id="required",
            text="SYNTHETIC rent USD 100",
            media_capture_refs=["offline://fixtures/expired"],
            media_required=True,
        )
        lab.pump()
        self.assertEqual(lab.rows("catalog", "listings")[0]["visibility"], "review")
