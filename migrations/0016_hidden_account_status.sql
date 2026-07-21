-- Allow accounts to be hidden from the directory without deleting them.
-- Used for manual verification culls (e.g. sub-threshold-follower Twitter/
-- Bluesky accounts) where we want the row, its snapshots, and its identity
-- edges preserved but the account gone from the public surface.
--
-- 'hidden' is excluded from directory_entries (which publishes only
-- active/unknown) and from roster-singleton creation in clustering, so a
-- hidden account is never re-promoted. Reversible: flip status back.

alter table accounts drop constraint accounts_status_check;
alter table accounts add constraint accounts_status_check
    check (status in ('active', 'suspended', 'deleted', 'deactivated', 'unknown', 'hidden'));
