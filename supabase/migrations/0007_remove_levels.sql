-- Levels removed entirely - safe to run whether or not 0006's level
-- threshold change was applied first (drop-if-exists throughout).
drop view if exists public_profiles;
alter table profiles drop column if exists level;
create view public_profiles as
  select id, name, points, created_at from profiles;
grant select on public_profiles to anon, authenticated;
