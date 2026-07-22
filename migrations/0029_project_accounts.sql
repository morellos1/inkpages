-- Project/collective accounts (zines, big bangs, anthologies, fic events).
-- These publish participant rosters that read exactly like an artist's own
-- links; flagged accounts are excluded from clustering, the review-UI
-- connections table, the paid hydration backlog, and singleton_gate review.
-- The flag is set by the classifier sweep in cluster.py (handle ends in
-- "zine", or project-flavored self-description); admins may flip it in SQL.
alter table accounts add column project boolean not null default false;

-- The hydration backlog and clustering scan non-project rows constantly.
create index accounts_project_idx on accounts (platform_id) where project;
