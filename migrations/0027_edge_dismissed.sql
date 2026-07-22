-- A human can rule that a connection is pure noise (no relation to the
-- artist at all — not even worth showing as `related`). Such edges become
-- status='dismissed': hidden from every surface (queries filter on
-- status='present') and — the load-bearing part — never resurrected by
-- re-extraction: upsert_edge's ON CONFLICT UPDATE carries
-- `where status is distinct from 'dismissed'`, so as long as the bio still
-- contains the link, the row conflicts, the update is skipped, and the
-- dismissal holds.

alter table identity_edges drop constraint identity_edges_status_check;
alter table identity_edges add constraint identity_edges_status_check
    check (status in ('present', 'stale', 'retracted', 'dismissed'));
