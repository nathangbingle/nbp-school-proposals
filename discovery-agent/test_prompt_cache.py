"""Checks that enrichment calls cache only the shared prefix.

No network. A live two-call probe runs only when both ANTHROPIC_API_KEY and
RUN_CACHE_PROBE=1 are set.
"""
import os
import unittest
from datetime import datetime, timezone

import enrich


def _club(club_id: str, name: str) -> dict:
    return {
        "id": club_id,
        "name": name,
        "school": name,
        "district": "Sentinel District",
        "zip": "29715",
        "tier": "2",
        "school_type": "middle",
        "fb": None,
        "ig": None,
        "email": None,
        "ad": None,
        "website": None,
        "activity": None,
    }


class PromptCacheTests(unittest.TestCase):
    def test_breakpoints_are_on_the_stable_prefix_only(self):
        club = _club("sentinel-id-9f3c", "Sentinel Creek Middle")
        request = enrich.enrichment_request(club, ["fb", "email"])
        breakpoints = enrich.cache_breakpoints(request)

        self.assertLessEqual(len(breakpoints), enrich.MAX_CACHE_BREAKPOINTS)
        self.assertEqual(breakpoints, [("tool", "web_search"), ("system", 0)])
        for tool in request["tools"]:
            if "cache_control" in tool:
                self.assertEqual(tool["cache_control"], {"type": "ephemeral"})
        for block in request["system"]:
            if "cache_control" in block:
                self.assertEqual(block["cache_control"], {"type": "ephemeral"})
        self.assertIsInstance(request["messages"][0]["content"], str)

    def test_prefix_is_identical_for_different_clubs(self):
        first = enrich.enrichment_request(
            _club("sentinel-id-9f3c", "Sentinel Creek Middle"), ["fb"]
        )
        second = enrich.enrichment_request(
            _club("other-id-11aa", "Other Ridge High"), ["ig", "ad"]
        )
        self.assertEqual(first["tools"], second["tools"])
        self.assertEqual(first["system"], second["system"])
        self.assertNotEqual(first["messages"], second["messages"])

    def test_per_request_data_stays_out_of_the_cached_prefix(self):
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        club_id = "sentinel-id-9f3c"
        name = f"Sentinel Creek Middle {stamp}"
        request = enrich.enrichment_request(_club(club_id, name), ["website"])
        prefix = str(request["tools"]) + str(request["system"])
        user_text = request["messages"][0]["content"]

        # The id is bookkeeping, not something the model needs. It must not
        # leak into the cached prefix. The school name and timestamp ride in
        # the user message only.
        self.assertNotIn(club_id, prefix)
        self.assertNotIn(club_id, user_text)
        for needle in (name, stamp):
            self.assertNotIn(needle, prefix)
            self.assertIn(needle, user_text)

    def test_default_model_prefix_is_below_the_cache_minimum(self):
        self.assertEqual(enrich.minimum_cache_tokens("claude-haiku-4-5-20251001"), 4096)
        self.assertEqual(enrich.minimum_cache_tokens("claude-sonnet-5-5"), 512)
        self.assertEqual(enrich.minimum_cache_tokens("claude-sonnet-5"), 1024)
        estimate = enrich.estimate_stable_prefix_tokens()
        self.assertLess(estimate, 4096)
        self.assertIn("will not read the cache", enrich.cache_prefix_status(enrich.MODEL))
        # The standing "Bailey" example is shared instruction text, not a club record.
        self.assertIn("Bailey", enrich.SYSTEM_PROMPT)


@unittest.skipUnless(
    os.environ.get("ANTHROPIC_API_KEY", "").strip() and os.environ.get("RUN_CACHE_PROBE") == "1",
    "Set ANTHROPIC_API_KEY and RUN_CACHE_PROBE=1 to send two live cache probes",
)
class LiveCacheProbeTests(unittest.TestCase):
    def test_second_call_reads_the_cache_when_the_prefix_is_long_enough(self):
        client = enrich.anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        first, second = enrich.probe_prompt_cache(client)
        print("cache probe usage", first, second)
        minimum = enrich.minimum_cache_tokens(enrich.MODEL)
        estimate = enrich.estimate_stable_prefix_tokens()
        read = second.get("cache_read_input_tokens") or 0
        if minimum is not None and estimate < minimum:
            self.assertEqual(read, 0)
        else:
            self.assertGreater(read, 0)


if __name__ == "__main__":
    unittest.main()
