-- Dead-link checking: when a profile URL was last verified reachable.
alter table accounts add column link_checked_at timestamptz;
