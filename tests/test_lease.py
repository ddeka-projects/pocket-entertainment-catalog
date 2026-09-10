from __future__ import annotations

import unittest

from pocket_entertainment_catalog.lease import EditorLease, LeaseConflict, LeaseExpired


class EditorLeaseTests(unittest.TestCase):
    def test_only_one_page_can_hold_the_lease(self) -> None:
        clock = [10.0]
        lease = EditorLease(ttl_seconds=15, clock=lambda: clock[0])

        lease.claim("client-a", "page-a")
        with self.assertRaises(LeaseConflict):
            lease.claim("client-b", "page-b")

        # Reads only inspect the anonymous snapshot and remain available.
        self.assertTrue(lease.snapshot().claimed)

    def test_heartbeat_extends_and_abandoned_lease_expires(self) -> None:
        clock = [10.0]
        lease = EditorLease(ttl_seconds=15, clock=lambda: clock[0])
        lease.claim("client-a", "page-a")

        clock[0] = 20.0
        lease.heartbeat("client-a", "page-a")
        clock[0] = 34.9
        lease.authorize("client-a", "page-a")

        clock[0] = 35.0
        with self.assertRaises(LeaseExpired):
            lease.authorize("client-a", "page-a")
        self.assertFalse(lease.snapshot().claimed)

    def test_release_requires_the_lease_owner(self) -> None:
        lease = EditorLease()
        lease.claim("client-a", "page-a")
        with self.assertRaises(LeaseConflict):
            lease.release("client-a", "page-b")
        lease.release("client-a", "page-a")
        self.assertFalse(lease.snapshot().claimed)


if __name__ == "__main__":
    unittest.main()
